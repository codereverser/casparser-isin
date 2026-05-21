"""AMFI NAV-file scraping.

Two sources are merged:

- The live ``NAVAll.txt`` portal feed -> current scheme ISIN <-> AMFI code
  mappings (and a reinvest/payout flag where applicable).
- A frozen ``AMFI_NAV_31Jan2018.txt`` -> historical NAV for LTCG carry-forward.

The 2018 file is checked into ``tools/files/`` because AMFI no longer hosts
it.  Do not regenerate.
"""

from __future__ import annotations

import re
from typing import TypedDict

import requests

from ..constants import AMFI_NAV_URL
from ..settings import DATA_DIR, logger

_AMFI_2018_FILE = DATA_DIR / "AMFI_NAV_31Jan2018.txt"


class AmfiPayload(TypedDict):
    codes: dict[str, tuple[str, bool | None]]
    navs: dict[str, str]


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
    return {"codes": data, "navs": nav}


def parse_amfi_nav_text(text: str) -> dict[str, tuple[str, bool | None]]:
    """Parse the body of ``NAVAll.txt`` into ``isin -> (amfi_code, is_reinvest)``.

    ``is_reinvest`` is ``None`` when the scheme has only one ISIN (no payout vs
    reinvest distinction in the source).
    """
    data: dict[str, tuple[str, bool | None]] = {}
    for line in text.splitlines():
        tokens = line.split(";")
        if not tokens or not re.search(r"^\d+$", tokens[0]):
            continue
        amfi_code, isin1, isin2, *_rest, _nav, _date = tokens
        if isin1 != "-":
            reinvest = None if isin2 == "-" else False
            data[isin1] = (amfi_code, reinvest)
        if isin2 != "-":
            data[isin2] = (amfi_code, True)
    return data


def get_amfi_isin_map(session: requests.Session) -> AmfiPayload:
    """Fetch live AMFI mappings and merge with the frozen 2018 reference set."""
    response = session.get(AMFI_NAV_URL, timeout=60)
    if response.status_code != 200:
        raise ValueError(f"Invalid response while fetching AMFI NAV data :: {response.status_code}")
    if getattr(response, "from_cache", False):
        logger.debug("Loaded AMFI data from cache")

    live = parse_amfi_nav_text(response.text)
    if len(live) < 1000:
        # The portal returns ~10k entries on a normal day. Anything dramatically
        # lower means we likely got an error page or a WAF interstitial.
        raise ValueError(f"AMFI returned only {len(live)} parseable rows; refusing to proceed")

    payload = parse_2018_nav_file()
    payload["codes"].update(live)
    logger.info("AMFI: %d live ISIN mappings (+ %d frozen 2018)", len(live), len(payload["navs"]))
    return payload
