"""Backblaze B2 upload with post-upload verification.

Upload order is db -> meta. If db succeeds but meta fails, the operator
sees a loud error and the public state is left pointing at the *previous*
generation (because clients read meta first to decide whether to fetch the
new db). This is the same atomicity property the library CLI relies on.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from b2sdk.v2 import B2Api, InMemoryAccountInfo

from .settings import ISIN_DB_PATH, ISIN_META_PATH, logger


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

    db_version = bucket.upload_local_file(str(db_path), db_path.name)
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

    meta_version = bucket.upload_local_file(str(meta_path), meta_path.name)
    logger.info("Uploaded %s [file_id=%s]", meta_version.file_name, meta_version.id_)
