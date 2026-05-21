"""Generic ISIN metadata from captn3m0/india-isin-data.

This feeds the ``isin`` table consumed by NSDL CAS support in casparser.
"""

from __future__ import annotations

import csv
import io

import requests

from ..constants import ISIN_URL
from ..settings import logger

_MIN_EXPECTED_ROWS = 10_000  # the live file ships >200k rows


def get_isin_data(session: requests.Session) -> list[list[str]]:
    """Return rows of ``[isin, name, issuer, type, status]``."""
    response = session.get(ISIN_URL, timeout=60)
    if response.status_code != 200:
        raise ValueError(f"Invalid response while fetching ISIN data :: {response.status_code}")
    if getattr(response, "from_cache", False):
        logger.debug("Loaded ISIN data from cache")

    rows: list[list[str]] = []
    with io.StringIO(response.text) as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            rows.append(
                [row["ISIN"], row["Description"], row["Issuer"], row["Type"], row["Status"]]
            )

    if len(rows) < _MIN_EXPECTED_ROWS:
        raise ValueError(
            f"ISIN feed returned only {len(rows)} rows (expected >={_MIN_EXPECTED_ROWS}); "
            "refusing to proceed"
        )
    logger.info("ISIN: %d rows", len(rows))
    return rows
