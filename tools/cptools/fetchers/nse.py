"""NSE equity symbol master -- maps an ISIN to its NSE trading symbol.

The captn3m0 ``isin`` feed gives every equity a name / issuer / type, but no
exchange ticker. Downstream price feeds (Yahoo's ``SYMBOL.NS`` chart endpoint,
the NSE quote API) key on the *trading symbol*, so we join NSE's published
equity list on ISIN to attach one.

Like the BSE scrape, this is best-effort: an NSE outage or a layout change must
NOT kill the build. The orchestrator treats a failure as "no fresh symbols this
run" and baseline carry-forward keeps any symbols a previous build resolved.

Upstream format (EQUITY_L.csv), comma-delimited with a header row whose names
carry leading spaces (`` SERIES``, `` ISIN NUMBER``)::

    SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE,
    MARKET LOT,  ISIN NUMBER, FACE VALUE
    20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5
"""

from __future__ import annotations

import csv
import io

import requests

from ..constants import NSE_EQUITY_LIST_URL
from ..settings import logger

# NSE lists ~2000 equities; anything dramatically smaller means we got a
# truncated download or an error page wrapped in a 200.
_MIN_EXPECTED_ROWS = 1000

# The nsearchives.* host serves the CSV without the cookie wall the www.* API
# imposes, but a bare python User-Agent is still rejected intermittently.
# Mimic a browser, matching the runtime NSE quote feed.
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15"
        " (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/csv,application/csv,*/*",
}


def parse_nse_equity_csv(text: str) -> dict[str, tuple[str, str]]:
    """Parse EQUITY_L.csv text into ``{isin: (symbol, "NSE")}``.

    NSE's header names carry leading spaces, so every key is stripped before
    lookup. Rows without a 12-char ISIN or a symbol are skipped. When the same
    ISIN appears under multiple series (``EQ`` plus a surveillance series such
    as ``BE`` / ``BZ``), the ``EQ`` row wins -- it's the normal rolling-
    settlement listing that the price feeds expect.
    """
    out: dict[str, tuple[str, str]] = {}
    series_by_isin: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        isin = row.get("ISIN NUMBER", "").upper()
        symbol = row.get("SYMBOL", "").upper()
        series = row.get("SERIES", "").upper()
        if len(isin) != 12 or not symbol:
            continue
        # Prefer the EQ series; never let a non-EQ row clobber an EQ one.
        if isin in out and series_by_isin.get(isin) == "EQ" and series != "EQ":
            continue
        out[isin] = (symbol, "NSE")
        series_by_isin[isin] = series
    return out


def fetch_nse_equity_isin_map(session: requests.Session) -> dict[str, tuple[str, str]]:
    """Download NSE's equity list and return ``{isin: (symbol, "NSE")}``.

    Raises ``ValueError`` on a non-200 response or a suspiciously small row
    count so the orchestrator's optional-fetch wrapper can log it and fall back
    to baseline symbols. Never returns a partial / truncated mapping silently.
    """
    response = session.get(NSE_EQUITY_LIST_URL, timeout=60, headers=_NSE_HEADERS)
    if response.status_code != 200:
        raise ValueError(f"NSE equity list returned {response.status_code}")
    mapping = parse_nse_equity_csv(response.text)
    if len(mapping) < _MIN_EXPECTED_ROWS:
        raise ValueError(
            f"NSE equity list yielded only {len(mapping)} ISIN->symbol rows "
            f"(expected >={_MIN_EXPECTED_ROWS}); refusing a truncated feed"
        )
    logger.info("NSE equity list: %d ISIN->symbol mappings", len(mapping))
    return mapping
