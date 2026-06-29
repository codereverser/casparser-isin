"""Generic ISIN metadata from captn3m0/india-isin-data.

This feeds the ``isin`` table consumed by NSDL CAS support in casparser.

Upstream format
---------------
The upstream project publishes a SQLite database as a versioned GitHub
release asset. We discover the latest release via the GitHub API, then
read the rows we care about with a plain ``SELECT``.

The ~130 MB asset gets a dedicated on-disk cache (the shared
:func:`cptools.cache.is_fresh` / :data:`~cptools.cache.CACHE_TTL_SECONDS`
logic): a single fixed file under the tools cache dir with a 12-hour TTL --
a re-run within that window reuses the file instead of re-downloading it.
``CASPARSER_ISIN_TOOLS_NO_CACHE`` bypasses this cache and streams to a
throwaway temp file (the cron mode).

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
from contextlib import closing
from pathlib import Path

import requests

from ..cache import is_fresh
from ..constants import ISIN_ASSET_NAME, ISIN_GITHUB_LATEST_RELEASE_API
from ..settings import cache_disabled, get_cache_dir, logger

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
    with closing(sqlite3.connect(uri, uri=True)) as conn:
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

    ``stream=True`` makes :class:`cptools.cache.FileCacheSession` pass the
    request straight through -- a 130+ MB binary is cached as its own file by
    :func:`_ensure_cached_asset`, not in the response body cache.
    """
    response = session.get(url, stream=True, timeout=300)
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


def _download_and_log(session: requests.Session, url: str, dest: Path) -> int:
    """Stream the asset to ``dest`` and log its size. Returns the byte count."""
    bytes_written = _stream_download(session, url, dest)
    logger.info(
        "captn3m0 release asset downloaded: %d bytes (%.1f MB)",
        bytes_written,
        bytes_written / (1024 * 1024),
    )
    return bytes_written


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _ensure_cached_asset(session: requests.Session, url: str) -> Path:
    """Return the cached asset path, downloading only on a miss or expiry.

    A single fixed file holds the latest copy; a fresh download overwrites
    it. The cache hits when the file exists and is younger than the 12-hour
    TTL. The download lands in a ``.tmp`` sibling and is atomically renamed,
    so a crash mid-download can't leave a truncated file behind.
    """
    dest = get_cache_dir() / "captn3m0-isin.db"
    if is_fresh(dest):
        logger.info("captn3m0 asset cache hit: %s (%d bytes)", dest, dest.stat().st_size)
        return dest

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        _download_and_log(session, url, tmp)
        os.replace(tmp, dest)
    finally:
        _unlink_quietly(tmp)
    return dest


def get_isin_data(session: requests.Session) -> list[list[str]]:
    """Return rows of ``[isin, name, issuer, type, status]``.

    Out-of-scope instrument types (commercial paper, T-bills, etc.) are
    dropped here -- see :data:`KEEP_TYPES`.

    The upstream SQLite asset (~130 MB) gets its own on-disk cache: a single
    fixed file under the tools cache dir with the shared 12-hour TTL.
    ``CASPARSER_ISIN_TOOLS_NO_CACHE`` bypasses the cache: the asset streams to
    a temp file, is queried, then deleted (the correct mode for cron).
    """
    tag, asset_url = _find_latest_release_asset(session)
    logger.info("captn3m0 latest release: %s (%s)", tag, asset_url)

    if cache_disabled():
        # No persistent cache: temp file in the system temp dir so the
        # download lands neither in the repo nor in the cache dir.
        fd, tmp_name = tempfile.mkstemp(prefix="captn3m0-", suffix=".db")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            _download_and_log(session, asset_url, tmp_path)
            kept, dropped = parse_isin_db(tmp_path)
        finally:
            _unlink_quietly(tmp_path)
    else:
        asset_path = _ensure_cached_asset(session, asset_url)
        kept, dropped = parse_isin_db(asset_path)

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
