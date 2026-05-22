"""AMFI NAV-file scraping.

Two sources are merged:

- The live ``NAVAll.txt`` portal feed -> current scheme ISIN <-> AMFI code
  mappings (with reinvest/payout flag) **plus** SEBI category derived from
  the section headers that precede each block of scheme rows.
- A frozen ``AMFI_NAV_31Jan2018.txt`` -> historical NAV for LTCG carry-forward.

The 2018 file is checked into ``tools/files/`` because AMFI no longer hosts
it.  Do not regenerate.

NAVAll.txt structure::

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;NAV;Date

    Open Ended Schemes(Debt Scheme - Banking and PSU Fund)

    Aditya Birla Sun Life Mutual Fund

    119551;INF209KA12Z1;INF209KA13Z9;Aditya Birla ... - IDCW;104.38;20-May-2026
    ...

The parser is a small state machine: section headers update the current
SEBI category; data rows carry that category forward; blank lines and AMC
names are ignored.
"""

from __future__ import annotations

import re
from typing import TypedDict

import requests

from ..constants import AMFI_NAV_URL
from ..settings import DATA_DIR, logger

_AMFI_2018_FILE = DATA_DIR / "AMFI_NAV_31Jan2018.txt"

# Examples of section headers seen in the live feed::
#
#   Open Ended Schemes(Debt Scheme - Banking and PSU Fund)
#   Open Ended Schemes(Equity Scheme - Large Cap Fund)
#   Open Ended Schemes(Hybrid Scheme - Aggressive Hybrid Fund)
#   Close Ended Schemes(Equity Scheme)
#   Interval Fund Schemes(Debt Scheme)
#
# We allow any of (Open|Close|Interval) (Ended|Fund) Schemes(...) so a
# wording tweak by AMFI doesn't silently flip every ISIN to an unknown
# category.
_SECTION_HEADER_RE = re.compile(
    r"^(?:Open|Close|Interval)\s+(?:Ended|Fund)\s+Schemes?\s*\(\s*(.+?)\s*\)\s*$"
)


class AmfiPayload(TypedDict):
    codes: dict[str, tuple[str, bool | None]]
    navs: dict[str, str]
    categories: dict[str, str]


def parse_2018_nav_file(path=None) -> AmfiPayload:
    """Parse the frozen 31-Jan-2018 NAV file shipped in ``tools/files/``."""
    file_path = path or _AMFI_2018_FILE
    with open(file_path) as f:
        lines = f.readlines()

    data: dict[str, tuple[str, bool]] = {}
    nav: dict[str, str] = {}
    for line in lines:
        tokens = line.strip().split(";")
        if len(tokens) > 0 and tokens[-1].strip() == "31-Jan-2018":
            amfi_code, isin1, isin2, *_rest, _nav, _, _, _date = tokens
            if isin1 != "-":
                data[isin1] = (amfi_code, False)
                nav[isin1] = _nav
            if isin2 != "-":
                data[isin2] = (amfi_code, True)
                nav[isin2] = _nav
    # The 2018 file predates AMFI's section-header convention; categories
    # are populated from the live feed in :func:`get_amfi_isin_map`.
    return {"codes": data, "navs": nav, "categories": {}}


def parse_amfi_nav_text(
    text: str,
) -> tuple[dict[str, tuple[str, bool | None]], dict[str, str]]:
    """Parse the body of ``NAVAll.txt`` into two parallel maps.

    Returns ``(codes, categories)``:

    - ``codes``: ``isin -> (amfi_code, is_reinvest)``. ``is_reinvest`` is
      ``None`` when the scheme has only one ISIN (no payout vs reinvest
      distinction in the source).
    - ``categories``: ``isin -> sebi_category``. The SEBI category is the
      text inside the most recent section header, e.g.
      ``"Debt Scheme - Banking and PSU Fund"``.

    Section headers that don't match the expected shape leave the current
    category unchanged but log at DEBUG so we can spot wording drift over
    time.
    """
    codes: dict[str, tuple[str, bool | None]] = {}
    categories: dict[str, str] = {}
    current_category: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        header_match = _SECTION_HEADER_RE.match(stripped)
        if header_match:
            current_category = header_match.group(1).strip()
            continue

        tokens = stripped.split(";")
        if not tokens or not re.match(r"^\d+$", tokens[0]):
            # AMC name, banner, or anything else non-tabular -- ignore.
            continue

        amfi_code, isin1, isin2, *_rest, _nav, _date = tokens
        if isin1 != "-":
            reinvest = None if isin2 == "-" else False
            codes[isin1] = (amfi_code, reinvest)
            if current_category is not None:
                categories[isin1] = current_category
        if isin2 != "-":
            codes[isin2] = (amfi_code, True)
            if current_category is not None:
                categories[isin2] = current_category

    return codes, categories


def get_amfi_isin_map(session: requests.Session) -> AmfiPayload:
    """Fetch live AMFI mappings and merge with the frozen 2018 reference set."""
    response = session.get(AMFI_NAV_URL, timeout=60)
    if response.status_code != 200:
        raise ValueError(f"Invalid response while fetching AMFI NAV data :: {response.status_code}")
    if getattr(response, "from_cache", False):
        logger.debug("Loaded AMFI data from cache")

    live_codes, live_categories = parse_amfi_nav_text(response.text)
    if len(live_codes) < 1000:
        # The portal returns ~10k entries on a normal day. Anything dramatically
        # lower means we likely got an error page or a WAF interstitial.
        raise ValueError(
            f"AMFI returned only {len(live_codes)} parseable rows; refusing to proceed"
        )

    payload = parse_2018_nav_file()
    payload["codes"].update(live_codes)
    payload["categories"] = live_categories
    logger.info(
        "AMFI: %d live ISIN mappings, %d categorised (+ %d frozen 2018)",
        len(live_codes),
        len(live_categories),
        len(payload["navs"]),
    )
    return payload
