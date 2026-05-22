"""Generic ISIN metadata from captn3m0/india-isin-data.

This feeds the ``isin`` table consumed by NSDL CAS support in casparser.

Two transformations are applied to every row at the fetcher boundary:

1. **Casing normalisation** -- the upstream source has a few rows whose
   ``Type`` / ``Status`` columns differ only by case (e.g. ``Debenture`` vs
   ``DEBENTURE``, ``Deleted`` vs ``DELETED``). We collapse them to a single
   canonical form so downstream consumers don't have to worry about
   duplicates that are really just data-quality bugs.
2. **Type filter** -- the source contains ~290k rows spanning instrument
   types that NSDL/CDSL CAS files never reference (commercial paper,
   treasury bills, certificate of deposit, securitised instruments). They
   exist in the upstream registry but are institutional-only -- not in a
   retail demat statement. The ``KEEP_TYPES`` set scopes us to instruments
   that actually appear in CAS files.
"""

from __future__ import annotations

import csv
import io
from collections import Counter

import requests

from ..constants import ISIN_URL
from ..settings import logger

_MIN_EXPECTED_ROWS = 10_000  # the live file ships >200k rows

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


def _canonicalise(row: dict[str, str]) -> list[str] | None:
    """Normalise casing and apply the type filter.

    Returns ``[isin, name, issuer, type, status]`` for in-scope rows, or
    ``None`` for rows whose type isn't in ``KEEP_TYPES``.
    """
    raw_type = row["Type"]
    raw_status = row["Status"]
    type_ = _TYPE_CANONICAL.get(raw_type, raw_type)
    status = _STATUS_CANONICAL.get(raw_status, raw_status)
    if type_ not in KEEP_TYPES:
        return None
    return [row["ISIN"], row["Description"], row["Issuer"], type_, status]


def parse_isin_csv(text: str) -> tuple[list[list[str]], Counter]:
    """Parse the captn3m0 CSV body. Pure function -- no network.

    Returns ``(kept_rows, dropped_by_type)`` so callers can log the drop
    distribution. Tests use this entry point directly.
    """
    kept: list[list[str]] = []
    dropped: Counter[str] = Counter()
    with io.StringIO(text) as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            transformed = _canonicalise(row)
            if transformed is None:
                # Track the original (pre-normalisation) type so the
                # operator can see which feeds are noisiest.
                dropped[row["Type"]] += 1
                continue
            kept.append(transformed)
    return kept, dropped


def get_isin_data(session: requests.Session) -> list[list[str]]:
    """Return rows of ``[isin, name, issuer, type, status]``.

    Out-of-scope instrument types (commercial paper, T-bills, etc.) are
    dropped here -- see :data:`KEEP_TYPES`.
    """
    response = session.get(ISIN_URL, timeout=60)
    if response.status_code != 200:
        raise ValueError(f"Invalid response while fetching ISIN data :: {response.status_code}")
    if getattr(response, "from_cache", False):
        logger.debug("Loaded ISIN data from cache")

    kept, dropped = parse_isin_csv(response.text)

    total = len(kept) + sum(dropped.values())
    if total < _MIN_EXPECTED_ROWS:
        raise ValueError(
            f"ISIN feed returned only {total} rows (expected >={_MIN_EXPECTED_ROWS}); "
            "refusing to proceed"
        )

    logger.info(
        "ISIN: %d rows kept, %d dropped (out of %d total)",
        len(kept),
        sum(dropped.values()),
        total,
    )
    for type_, count in dropped.most_common(5):
        logger.debug("ISIN dropped %d rows of type %r", count, type_)
    return kept
