"""Paths, environment variables, and logger setup for the build pipeline.

Resolution rules (overridable via environment):

- ``CASPARSER_ISIN_TOOLS_CACHE``   -> request cache directory
- ``CASPARSER_ISIN_TOOLS_NO_CACHE`` (``1``/``true``) -> disable the cache
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


def cache_disabled() -> bool:
    """True when the operator has explicitly disabled the HTTP cache.

    Set this in cron so two consecutive runs don't read stale entries.
    """
    return os.environ.get("CASPARSER_ISIN_TOOLS_NO_CACHE", "").lower() in ("1", "true", "yes")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure stdlib logging. Idempotent."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=False,
    )


logger = logging.getLogger("cptools")
