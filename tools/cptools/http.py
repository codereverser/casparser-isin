"""HTTP session factory used by all fetchers.

Two important differences vs. the original::

1. The cache TTL is **15 minutes** by default, not one day. A daily cron with
   a daily TTL would forever lag the upstream sources by one run.
2. ``CASPARSER_ISIN_TOOLS_NO_CACHE=1`` returns a plain ``requests.Session``
   with no cache at all -- the correct mode for cron.

Both modes carry a bounded retry policy (3 attempts, exponential backoff with
jitter) for transient AMFI / BSE / GitHub failures.
"""

from __future__ import annotations

from datetime import timedelta

import requests
from requests.adapters import HTTPAdapter
from requests.utils import default_user_agent
from urllib3.util.retry import Retry

from .settings import cache_disabled, get_cache_dir, logger

_USER_AGENT = default_user_agent("casparser-isin-tools")


def _build_retry() -> Retry:
    return Retry(
        total=3,
        backoff_factor=1.5,  # 1.5s, 3s, 6s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST", "HEAD"),
        raise_on_status=False,
    )


def build_session() -> requests.Session:
    """Return a configured session honouring the cache env vars."""
    if cache_disabled():
        logger.info("HTTP cache disabled (CASPARSER_ISIN_TOOLS_NO_CACHE)")
        session = requests.Session()
    else:
        # Import lazily so disabled mode doesn't require requests-cache.
        from requests_cache import CachedSession, SQLiteCache

        cache_path = get_cache_dir() / "http_cache.sqlite"
        logger.debug("HTTP cache at %s (15 min TTL)", cache_path)
        session = CachedSession(
            backend=SQLiteCache(str(cache_path)),
            expire_after=timedelta(minutes=15),
            allowable_methods=("GET", "HEAD", "POST"),
        )

    adapter = HTTPAdapter(max_retries=_build_retry())
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": _USER_AGENT})
    return session
