"""Generic ISIN metadata from captn3m0/india-isin-data.

This feeds the ``isin`` table consumed by NSDL CAS support in casparser.

Upstream format
---------------
The upstream project publishes a SQLite database as a versioned GitHub
release asset. We discover the latest release via the GitHub API, stream
the asset to a temporary file, and read the rows we care about with a
plain ``SELECT``.

The upstream ``isin`` table schema (subset we consume)::

    isin               TEXT PRIMARY KEY
    issuer_name        TEXT
    description        TEXT
    security_type_name TEXT
    status             TEXT

Transformations applied at the fetcher boundary:

1. **Casing normalisation** -- the upstream source has a few rows whose
   ``security_type_name`` / ``status`` differ only by case (``Debenture``
   vs ``DEBENTURE``, ``Deleted`` vs ``DELETED``). We collapse them to a
   single canonical form.
2. **Type filter** -- only instrument types that *can* appear in retail
   NSDL / CDSL CAS files (see :data:`KEEP_TYPES`).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

import requests

from ..constants import ISIN_ASSET_NAME, ISIN_GITHUB_LATEST_RELEASE_API
from ..settings import logger

# The live upstream DB ships > 380k rows. Anything dramatically below
# this is a sign we got a truncated download or a malformed file.
_MIN_EXPECTED_ROWS = 10_000

# Casing-bug duplicates observed in the source feed. Keys are seen-in-the-wild;
# values are the canonical form we want to store.
_TYPE_CANONICAL: dict[str, str] = {
    "Debenture": "DEBENTURE",
    "Government Securities": "GOVERNMENT SECURITIES",
    "Securitised Instrument": "SECURITISED INSTRUMENT",
    "Mutual Fund Unit (TRASE)": "MUTUAL FUND UNIT (TRASE)",
}

_STATUS_CANONICAL: dict[str, str] = {
    "Active": "ACTIVE",
    "Deleted": "DELETED",
    "Suspended": "SUSPENDED",
    "Blocked due to ACA": "BLOCKED DUE TO ACA",
}

# Instrument types that *can* appear in NSDL or CDSL CAS files.
#
# The source feed also contains COMMERCIAL PAPER, CERTIFICATE OF DEPOSIT,
# TREASURY BILLS, SECURITISED INSTRUMENT, and MUTUAL FUND UNIT* rows. None
# of these belong here: the first three are institutional-only money-market
# instruments, securitised instruments are PTC/MBS issuance held by
# institutions, and MF UNIT rows duplicate what the ``scheme`` table
# already covers.
KEEP_TYPES: frozenset[str] = frozenset(
    {
        "EQUITY SHARES",
        "PREFERENCE SHARES",
        "DEBENTURE",
        "BOND",
        "DEEP DISCOUNT BOND",
        "REGULAR RETURN BOND",
        "FLOATING RATE BOND",
        "MUNICIPAL BOND",
        "GOVERNMENT SECURITIES",
        "SOVEREIGN GOLD BOND",
        "INFRASTRUCTURE INVESTMENT TRUST",
        "REAL ESTATE INVESTMENT TRUSTS",
        "RIGHTS ENTITLEMENT",
        "WARRANT",
        "INDIAN DEPOSITORY RECEIPT",
        "ALTERNATIVE INVESTMENT FUND",
    }
)


def _canonicalise(
    isin: str,
    description: str | None,
    issuer_name: str | None,
    security_type_name: str | None,
    status: str | None,
) -> list[str] | None:
    """Normalise casing and apply the type filter.

    Returns ``[isin, name, issuer, type, status]`` for in-scope rows, or
    ``None`` for rows whose type isn't in :data:`KEEP_TYPES`.
    """
    raw_type = security_type_name or ""
    raw_status = status or ""
    type_ = _TYPE_CANONICAL.get(raw_type, raw_type)
    status_ = _STATUS_CANONICAL.get(raw_status, raw_status)
    if type_ not in KEEP_TYPES:
        return None
    return [isin, description or "", issuer_name or "", type_, status_]


def parse_isin_db(db_path: Path) -> tuple[list[list[str]], Counter]:
    """Read an upstream captn3m0 isin.db and return our filtered row set.

    Returns ``(kept_rows, dropped_by_type)`` so callers can log the drop
    distribution. Tests use this entry point directly with a fixture DB.
    """
    kept: list[list[str]] = []
    dropped: Counter[str] = Counter()
    # Open read-only via URI -- prevents any accidental write back to the
    # downloaded asset, and lets us drop the temp file as soon as the
    # iteration completes.
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        cur = conn.execute(
            "SELECT isin, description, issuer_name, security_type_name, status FROM isin"
        )
        for isin, description, issuer_name, security_type_name, status in cur:
            transformed = _canonicalise(isin, description, issuer_name, security_type_name, status)
            if transformed is None:
                dropped[security_type_name or "<empty>"] += 1
                continue
            kept.append(transformed)
    return kept, dropped


def _find_latest_release_asset(session: requests.Session) -> tuple[str, str]:
    """Hit the GitHub API to discover the latest release's isin.db asset.

    Returns ``(tag_name, asset_download_url)``. Raises ``ValueError`` if
    no asset named :data:`ISIN_ASSET_NAME` is found in the latest release.
    """
    response = session.get(
        ISIN_GITHUB_LATEST_RELEASE_API,
        timeout=30,
        headers={"Accept": "application/vnd.github+json"},
    )
    if response.status_code != 200:
        raise ValueError(
            f"GitHub release API returned {response.status_code} for captn3m0/india-isin-data"
        )
    data = response.json()
    tag = data.get("tag_name") or "<unknown>"
    for asset in data.get("assets", []):
        if asset.get("name") == ISIN_ASSET_NAME:
            url = asset.get("browser_download_url")
            if url:
                return tag, url
    raise ValueError(f"No asset named {ISIN_ASSET_NAME!r} found in captn3m0 release {tag}")


def _stream_download(session: requests.Session, url: str, dest: Path) -> int:
    """Stream a binary asset to ``dest``. Returns the byte count.

    If ``session`` is a requests-cache ``CachedSession``, we bypass the
    HTTP cache for this one request -- a 130+ MB binary doesn't belong
    in the cache SQLite backend, and we always want a fresh asset for a
    release build.
    """

    def _do_get():
        return session.get(url, stream=True, timeout=300)

    # `cache_disabled()` is only on requests-cache CachedSession; a plain
    # requests.Session lacks it. Probe and branch.
    cache_disabled = getattr(session, "cache_disabled", None)
    if callable(cache_disabled):
        with cache_disabled():
            response = _do_get()
    else:
        response = _do_get()

    if response.status_code != 200:
        raise ValueError(f"captn3m0 release asset returned {response.status_code} for {url}")
    total = 0
    with open(dest, "wb") as fp:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            fp.write(chunk)
            total += len(chunk)
    return total


def get_isin_data(session: requests.Session) -> list[list[str]]:
    """Return rows of ``[isin, name, issuer, type, status]``.

    Out-of-scope instrument types (commercial paper, T-bills, etc.) are
    dropped here -- see :data:`KEEP_TYPES`. The upstream SQLite asset is
    downloaded to a temp file, queried, and then deleted.
    """
    tag, asset_url = _find_latest_release_asset(session)
    logger.info("captn3m0 latest release: %s (%s)", tag, asset_url)

    # Use a temp file in the system temp dir so the 130 MB download doesn't
    # land in the repo or in the HTTP cache.
    fd, tmp_name = tempfile.mkstemp(prefix="captn3m0-", suffix=".db")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        bytes_written = _stream_download(session, asset_url, tmp_path)
        logger.info(
            "captn3m0 release asset downloaded: %d bytes (%.1f MB)",
            bytes_written,
            bytes_written / (1024 * 1024),
        )
        kept, dropped = parse_isin_db(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

    total = len(kept) + sum(dropped.values())
    if total < _MIN_EXPECTED_ROWS:
        raise ValueError(
            f"captn3m0 returned only {total} rows (expected >={_MIN_EXPECTED_ROWS}); "
            "refusing to proceed"
        )

    logger.info(
        "captn3m0: %d rows kept, %d dropped (out of %d total) from release %s",
        len(kept),
        sum(dropped.values()),
        total,
        tag,
    )
    for type_, count in dropped.most_common(5):
        logger.debug("captn3m0 dropped %d rows of type %r", count, type_)
    return kept
