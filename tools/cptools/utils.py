"""Backwards-compatible re-exports.

The real implementations live in :mod:`cptools.settings` and
:mod:`cptools.http`. This module is kept so that existing imports keep
working, but new code should import the underlying modules directly.
"""

from __future__ import annotations

from .http import build_session
from .settings import (
    DATA_DIR as FILES_DIR,
)
from .settings import (
    ISIN_DB_PATH,
    ISIN_META_PATH,
    configure_logging,
    logger,
)

# Lazy session: only build it when something actually needs HTTP. This keeps
# imports cheap (no SQLite cache file creation on module load) and lets tests
# stub the builder without spinning up the real cache.
_session = None


def get_session():
    global _session
    if _session is None:
        _session = build_session()
    return _session


class _SessionProxy:
    """Defers session construction until first attribute access."""

    def __getattr__(self, item):
        return getattr(get_session(), item)


cached_session = _SessionProxy()

__all__ = [
    "FILES_DIR",
    "ISIN_DB_PATH",
    "ISIN_META_PATH",
    "cached_session",
    "configure_logging",
    "get_session",
    "logger",
]
