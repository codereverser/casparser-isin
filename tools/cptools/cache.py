"""Single on-disk HTTP cache shared by every fetcher.

Replaces requests-cache with a plain directory of files -- one per request,
keyed by method + URL + body, each valid for 12 hours. Only the latest copy
of a given request is kept: a refresh overwrites the file in place.

Two consumers share this one logic:

- :class:`FileCacheSession` caches small text / JSON / CSV responses (AMFI,
  BSE, NSE, the GitHub release API).
- The large captn3m0 ``isin.db`` asset (~130 MB) manages its own file copy in
  :mod:`cptools.fetchers.isin`, but uses the same :data:`CACHE_TTL_SECONDS`
  and :func:`is_fresh` freshness check.

Streamed downloads (``stream=True``) bypass :class:`FileCacheSession`.
``CASPARSER_ISIN_TOOLS_NO_CACHE`` disables caching entirely (see
:func:`cptools.http.build_session`).
"""

from __future__ import annotations

import hashlib
import json
import pickle
import time
from pathlib import Path

import requests
from requests.structures import CaseInsensitiveDict

# Every cached entry -- HTTP response or downloaded asset -- is valid for this
# long. A re-run within the window reuses the file; after it, the next request
# refreshes (overwrites) it.
CACHE_TTL_SECONDS = 12 * 60 * 60


def is_fresh(path: Path, ttl: float = CACHE_TTL_SECONDS) -> bool:
    """True when ``path`` exists, is non-empty, and younger than ``ttl`` seconds."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    return st.st_size > 0 and (time.time() - st.st_mtime) < ttl


def _request_key(method: str, url: str, params, data, json_body) -> str:
    """Stable hash identifying a request (method + URL + query + body)."""
    payload = json.dumps(
        {
            "m": method.upper(),
            "u": url,
            "p": params,
            "d": sorted(data.items()) if isinstance(data, dict) else data,
            "j": json_body,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _serialise(resp: requests.Response) -> bytes:
    return pickle.dumps(
        {
            "status": resp.status_code,
            "content": resp.content,
            "url": resp.url,
            "encoding": resp.encoding,
            "reason": resp.reason,
            "headers": dict(resp.headers),
        }
    )


def _deserialise(blob: bytes) -> requests.Response:
    d = pickle.loads(blob)  # noqa: S301 -- local, self-written cache file
    resp = requests.Response()
    resp.status_code = d["status"]
    resp._content = d["content"]
    resp.url = d["url"]
    resp.encoding = d["encoding"]
    resp.reason = d["reason"]
    resp.headers = CaseInsensitiveDict(d["headers"])
    return resp


class FileCacheSession(requests.Session):
    """A ``requests`` Session that caches response bodies on disk with a TTL.

    Only ``200`` responses are cached. Streamed requests (``stream=True``) are
    passed straight through -- large binaries don't belong in the body cache.
    A corrupt cache entry is silently ignored and re-fetched.
    """

    def __init__(self, cache_dir: Path, ttl: float = CACHE_TTL_SECONDS):
        super().__init__()
        self._cache_dir = cache_dir
        self._ttl = ttl

    def request(self, method, url, **kwargs):  # type: ignore[override]
        if kwargs.get("stream"):
            return super().request(method, url, **kwargs)

        key = _request_key(
            method, url, kwargs.get("params"), kwargs.get("data"), kwargs.get("json")
        )
        path = self._cache_dir / f"http-{key}.cache"
        if is_fresh(path, self._ttl):
            try:
                return _deserialise(path.read_bytes())
            except Exception:  # noqa: BLE001 -- corrupt entry, fall back to network
                pass

        resp = super().request(method, url, **kwargs)
        if resp.status_code == 200:
            tmp = path.with_suffix(".cache.tmp")
            tmp.write_bytes(_serialise(resp))
            tmp.replace(path)
        return resp
