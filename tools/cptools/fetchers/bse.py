"""BSE StarMF scheme master scraping.

This is the most fragile fetcher in the pipeline: it scrapes an ASP.NET
WebForm, harvests ``__VIEWSTATE`` etc. from the GET, then POSTs three times
to download SCHEMEMASTER, SCHEMEMASTERDEMAT, and SCHEMEMASTERPHYSICAL.

If BSE ever redesigns this page the cron will start producing empty data
sets.  We assert non-empty inputs and reject obviously-wrong shapes so the
failure mode is loud, not silent.
"""

from __future__ import annotations

import time

import requests
from lxml.html import fromstring

from ..constants import BSE_STARMF_SCHEME_MASTER_URL
from ..settings import logger

# Each successful CSV has thousands of rows; anything dramatically smaller is
# a sign that the form returned an error page wrapped in 200 OK.
_MIN_EXPECTED_ROWS = 500

_FILE_TYPES = ("SCHEMEMASTER", "SCHEMEMASTERDEMAT", "SCHEMEMASTERPHYSICAL")


def fetch_bse_master_data(session: requests.Session) -> list[str]:
    """Fetch the three BSE scheme master CSVs.

    Returns the raw pipe-delimited CSV text for each file type.
    """
    response = session.get(BSE_STARMF_SCHEME_MASTER_URL, timeout=30)
    if response.status_code != 200:
        raise ValueError(f"BSE landing page returned {response.status_code}")

    page = fromstring(response.content)
    form_data = {
        x.get("name"): x.get("value")
        for x in page.xpath('.//form[@id="frmOrdConfirm"]//input[@type="hidden"]')
    }
    if not form_data:
        raise ValueError(
            "BSE form_data is empty; the page layout may have changed. "
            "Inspect the response manually before re-running."
        )

    csvs: list[str] = []
    for ftype in _FILE_TYPES:
        form_data.update({"ddlTypeOption": ftype, "btnText": "Export to Text"})
        response = session.post(BSE_STARMF_SCHEME_MASTER_URL, data=form_data, timeout=600)
        if response.status_code != 200:
            raise ValueError(f"BSE {ftype} returned {response.status_code}")

        # Sanity: count actual data rows (header + N).
        body = response.text
        line_count = body.count("\n")
        if line_count < _MIN_EXPECTED_ROWS:
            raise ValueError(
                f"BSE {ftype} returned only {line_count} lines (expected >={_MIN_EXPECTED_ROWS}); "
                "refusing to proceed"
            )

        if not getattr(response, "from_cache", False):
            logger.info("Fetched BSE %s (%d lines)", ftype, line_count)
            # Polite delay between live requests; skipped on cache hits.
            time.sleep(10)
        csvs.append(body)

    return csvs
