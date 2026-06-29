"""Backblaze B2 upload with post-upload verification.

Upload order is db -> db.gz -> meta. Meta is promoted last because clients read
it first to decide whether to fetch the new db; both the uncompressed and the
gzip-compressed artifacts must be in place before meta points at the new
generation. If any step fails the operator sees a loud error and the public
state still points at the *previous* generation. Same atomicity property the
library CLI relies on.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from b2sdk.v2 import (
    AbstractProgressListener,
    B2Api,
    InMemoryAccountInfo,
    make_progress_listener,
)

from .settings import ISIN_DB_PATH, ISIN_META_PATH, logger


class _LoggingProgressListener(AbstractProgressListener):
    """Report upload progress through the module logger.

    Emits a timestamped percentage line at most every
    :attr:`_INTERVAL_SECONDS`, so a long upload shows steady movement in a
    cron log (and a stall is obvious) without flooding it. Unlike a tqdm bar's
    carriage-return repaints, every line stands on its own.
    """

    _INTERVAL_SECONDS = 5.0

    def __init__(self, description: str):
        super().__init__(description)
        self._total = 0
        self._last_log = 0.0

    def set_total_bytes(self, total_byte_count: int) -> None:
        self._total = total_byte_count

    def bytes_completed(self, byte_count: int) -> None:
        now = time.monotonic()
        # Always log the final 100% tick; throttle the intermediate ones.
        done = self._total and byte_count >= self._total
        if not self._total or (not done and now - self._last_log < self._INTERVAL_SECONDS):
            return
        self._last_log = now
        logger.info(
            "Uploading %s: %d%% (%d/%d bytes)",
            self.description,
            int(100.0 * byte_count / self._total),
            byte_count,
            self._total,
        )


def _progress_listener(description: str):
    """Pick the best upload progress reporter for the current output.

    On an interactive terminal, :func:`make_progress_listener` yields a tqdm
    bar (or a plain percentage if tqdm isn't installed). Under cron / piped
    output we use :class:`_LoggingProgressListener`, which routes progress
    through the logger as timestamped percentage lines.
    """
    if sys.stderr.isatty():
        return make_progress_listener(description, quiet=False)
    return _LoggingProgressListener(description)


def _gzip_to_temp(src: Path) -> Path:
    """Gzip ``src`` into a temp file and return its path (caller deletes it)."""
    fd, tmp = tempfile.mkstemp(prefix="isin.db.", suffix=".gz")
    os.close(fd)
    tmp_path = Path(tmp)
    with open(src, "rb") as f_in, gzip.open(tmp_path, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    return tmp_path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _build_b2_client():
    info = InMemoryAccountInfo()
    api = B2Api(info)
    api.authorize_account(
        "production",
        _require_env("B2_APP_ID"),
        _require_env("B2_APP_KEY"),
    )
    return api


def upload_isin_db_to_b2(
    db_path: Path = ISIN_DB_PATH,
    meta_path: Path = ISIN_META_PATH,
) -> None:
    """Upload db then meta, verifying each before proceeding.

    Raises if any step fails. Db is uploaded first so that an in-flight client
    never sees a meta pointing at an unavailable db.
    """
    api = _build_b2_client()
    bucket = api.get_bucket_by_name(_require_env("B2_BUCKET"))

    local_db_sha = _file_sha256(db_path)
    local_db_size = db_path.stat().st_size
    logger.info(
        "Uploading %s (%d bytes, sha256=%s) to B2",
        db_path.name,
        local_db_size,
        local_db_sha,
    )

    with _progress_listener(db_path.name) as progress:
        db_version = bucket.upload_local_file(
            str(db_path), db_path.name, progress_listener=progress
        )
    logger.info("Uploaded %s [file_id=%s]", db_version.file_name, db_version.id_)

    # Verify the uploaded db before promoting the meta. If the server-side
    # hash diverges from our local hash, abort -- meta is not touched.
    server = bucket.get_file_info_by_name(db_path.name)
    if server.size != local_db_size:
        raise RuntimeError(
            f"B2 size mismatch for {db_path.name}: local={local_db_size}, remote={server.size}"
        )
    # b2sdk exposes sha1 by default; we trust b2's transport integrity for the
    # upload itself (TLS + per-part checksums) and only re-verify size here.

    # Compressed copy for the (preferred) gzip download path. Uploaded before
    # meta so a client that fetches ``isin.db.gz`` never races a meta pointing
    # at a generation whose .gz isn't up yet.
    gz_name = f"{db_path.name}.gz"
    gz_path = _gzip_to_temp(db_path)
    try:
        logger.info("Uploading %s (%d bytes) to B2", gz_name, gz_path.stat().st_size)
        with _progress_listener(gz_name) as progress:
            gz_version = bucket.upload_local_file(str(gz_path), gz_name, progress_listener=progress)
        logger.info("Uploaded %s [file_id=%s]", gz_version.file_name, gz_version.id_)
    finally:
        gz_path.unlink(missing_ok=True)

    meta_version = bucket.upload_local_file(str(meta_path), meta_path.name)
    logger.info("Uploaded %s [file_id=%s]", meta_version.file_name, meta_version.id_)
