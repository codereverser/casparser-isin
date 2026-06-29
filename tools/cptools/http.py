"""HTTP session factory used by all fetchers.

The cache is a plain on-disk file cache (see :mod:`cptools.cache`) with a
12-hour TTL -- one logic for every asset. ``CASPARSER_ISIN_TOOLS_NO_CACHE=1``
returns a plain ``requests.Session`` with no cache at all -- the correct mode
for cron.

Both modes carry a bounded retry policy (3 attempts, exponential backoff with
jitter) for transient AMFI / BSE / GitHub failures.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from requests.utils import default_user_agent
from urllib3.util.retry import Retry

from .cache import CACHE_TTL_SECONDS, FileCacheSession
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
        cache_dir = get_cache_dir()
        logger.debug("HTTP file cache at %s (12 h TTL)", cache_dir)
        session = FileCacheSession(cache_dir, CACHE_TTL_SECONDS)

    adapter = HTTPAdapter(max_retries=_build_retry())
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": _USER_AGENT})
    return session
