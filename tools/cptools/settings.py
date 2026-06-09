"""Paths, environment variables, and logger setup for the build pipeline.

Resolution rules (overridable via environment):

- ``CASPARSER_ISIN_TOOLS_CACHE``   -> request cache directory
- ``CASPARSER_ISIN_TOOLS_NO_CACHE`` (``1``/``true``) -> disable the HTTP cache
- ``CASPARSER_ISIN_TOOLS_NO_BSE`` (``1``/``true``) -> skip the BSE scrape entirely
- ``B2_APP_ID`` / ``B2_APP_KEY`` / ``B2_BUCKET`` -> Backblaze credentials

The defaults use ``$XDG_CACHE_HOME/casparser-isin-tools`` (Linux/macOS).
Windows is not a supported cronjob target; running under Windows will fall
through to ``~/.cache`` which works but is not idiomatic.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Repo layout
TOOLS_DIR = Path(__file__).resolve().parent.parent  # .../tools/
REPO_DIR = TOOLS_DIR.parent  # .../casparser-isin/
DATA_DIR = TOOLS_DIR / "files"  # static reference data (AMFI 2018, Franklin CSV)

# Shipped artifacts that this pipeline overwrites
ISIN_DB_PATH = REPO_DIR / "casparser_isin" / "isin.db"
ISIN_META_PATH = REPO_DIR / "casparser_isin" / "isin.db.meta"

# Autoload .env from the repo root so B2_* credentials (and the
# CASPARSER_ISIN_TOOLS_* toggles) need not be exported by hand. Loaded at
# import time because every entry path -- the updater, the b2 uploader, the
# fetchers -- imports this module first. Explicit values already in the
# environment win over the file (load_dotenv does not override by default).
# python-dotenv lives in the optional ``tools`` group; degrade gracefully if
# settings is imported without it (e.g. a runtime-only install).
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_DIR / ".env")
except ModuleNotFoundError:
    pass


def _resolve_cache_dir() -> Path:
    """Pick a writable cache dir without pulling in platformdirs."""
    override = os.environ.get("CASPARSER_ISIN_TOOLS_CACHE")
    if override:
        return Path(override).expanduser()

    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "casparser-isin-tools"


def get_cache_dir() -> Path:
    """Return the cache directory, creating it if necessary."""
    path = _resolve_cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def cache_disabled() -> bool:
    """True when the operator has explicitly disabled the HTTP cache.

    Set this in cron so two consecutive runs don't read stale entries.
    """
    return _truthy_env("CASPARSER_ISIN_TOOLS_NO_CACHE")


def bse_disabled() -> bool:
    """True when the operator has explicitly disabled the BSE scrape.

    The library's lookup code is ISIN-first since v1.0, so the rta_code
    mapping derived from BSE is a fallback only -- skipping the BSE step
    degrades the build to "rta_code table doesn't refresh" rather than
    "build fails". Baseline carry-forward keeps the existing rta_code
    entries alive in the meantime.

    Set ``CASPARSER_ISIN_TOOLS_NO_BSE=1`` to skip the BSE fetch on a
    given run.
    """
    return _truthy_env("CASPARSER_ISIN_TOOLS_NO_BSE")


def nse_disabled() -> bool:
    """True when the operator has explicitly disabled the NSE equity fetch.

    The NSE equity list only *enriches* the ``isin`` table with trading
    symbols; skipping it degrades the build to "symbols don't refresh" (prior
    symbols carry forward) rather than "build fails".

    Set ``CASPARSER_ISIN_TOOLS_NO_NSE=1`` to skip the NSE fetch on a given run.
    """
    return _truthy_env("CASPARSER_ISIN_TOOLS_NO_NSE")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure stdlib logging. Idempotent."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=False,
    )


logger = logging.getLogger("cptools")
