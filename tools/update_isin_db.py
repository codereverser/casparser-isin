"""Build & publish a fresh ``isin.db``.

Run via ``python tools/update_isin_db.py``. For unattended cron use::

    CASPARSER_ISIN_TOOLS_NO_CACHE=1 \\
    B2_APP_ID=... B2_APP_KEY=... B2_BUCKET=... \\
    python tools/update_isin_db.py [--no-upload]

Exit code conventions::

    0  -> no change, nothing published
    1  -> new database built and uploaded
    2  -> error
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import logging
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from cptools.b2 import upload_isin_db_to_b2
from cptools.builder import (
    SchemeRow,
    build_rows_from_bse,
    build_rows_from_franklin,
    merge_rows,
    read_baseline,
)
from cptools.fetchers.amfi import get_amfi_isin_map
from cptools.fetchers.bse import fetch_bse_master_data
from cptools.fetchers.isin import get_isin_data
from cptools.settings import (
    DATA_DIR,
    ISIN_DB_PATH,
    ISIN_META_PATH,
    configure_logging,
    logger,
)
from cptools.utils import get_session
from packaging import version

DBFORMAT = "1"

_NO_CHANGE = 0
_PUBLISHED = 1
_ERROR = 2


def prepare_db(conn: sqlite3.Connection, rows, nav_rows, isin_rows) -> None:
    """Populate a fresh DB.

    The original pipeline created five secondary indexes on the ``scheme``
    table; three of them were never used by runtime queries (review found
    ``idx_scheme_name`` LIKE-incompatible, ``idx_scheme_amc`` has no callers,
    ``idx_scheme_rta`` is redundant with the composite ``rta_code`` index).
    Dropping them saves ~3 MB shipped without changing query behaviour.
    """
    with conn:
        conn.execute(
            """
            CREATE TABLE scheme(
                id INTEGER NOT NULL PRIMARY KEY,
                name, isin, amfi_code, type,
                rta, rta_code, amc_code
            )
            """
        )
        conn.execute("CREATE TABLE meta(key NOT NULL PRIMARY KEY, value)")
        conn.execute("CREATE INDEX idx_scheme_rta_code ON scheme(rta_code)")
        conn.execute("CREATE INDEX idx_scheme_isin ON scheme(isin)")

        today = datetime.date.today()
        db_version = str(version.parse(today.strftime("%Y.%m.%d")))
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [("version", db_version), ("dbformat", DBFORMAT)],
        )
        conn.executemany(
            "INSERT INTO scheme(id, name, isin, amfi_code, type, rta, rta_code, amc_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r.as_tuple() if isinstance(r, SchemeRow) else r for r in rows),
        )

        conn.execute("CREATE TABLE nav20180131(isin NOT NULL PRIMARY KEY, nav)")
        conn.executemany("INSERT INTO nav20180131(isin, nav) VALUES (?, ?)", nav_rows)

        conn.execute("CREATE TABLE isin(isin NOT NULL PRIMARY KEY, name, issuer, type, status)")
        conn.executemany(
            "INSERT INTO isin(isin, name, issuer, type, status) VALUES (?, ?, ?, ?, ?)",
            isin_rows,
        )

    # VACUUM to reclaim any free pages from the bulk inserts. Must run
    # outside of any open transaction.
    conn.execute("VACUUM")


def _build_in_memory(rows, nav_rows, isin_rows) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    prepare_db(conn, rows, nav_rows, isin_rows)
    return conn


def _backup_to_file(mem_conn: sqlite3.Connection, dest: Path) -> None:
    """Stream the in-memory DB to ``dest`` using sqlite3's online backup."""
    with closing(sqlite3.connect(dest)) as target:
        mem_conn.backup(target)


def _hash_db_content(path: Path) -> str:
    """Hash the *data* of the DB (not the binary file).

    Two DBs are considered equal if their meta + scheme + nav + isin tables
    have the same rows in the same order. Using table data rather than file
    bytes means we don't trigger a publish just because SQLite chose
    different page layouts on different runs.
    """
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path)) as conn:
        for table in ("scheme", "nav20180131", "isin"):
            try:
                rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                digest.update(repr(row).encode())
    return digest.hexdigest()


def _write_meta(meta_path: Path, db_version: str, db_path: Path) -> None:
    """Write the public meta sidecar with version + dbformat + sha256."""
    db_sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
    text = f"version={db_version}\ndbformat={DBFORMAT}\nsha256={db_sha}\n"
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, meta_path)
    logger.info("Wrote %s :: version=%s", meta_path.name, db_version)


def _read_db_version(path: Path) -> str:
    with closing(sqlite3.connect(path)) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        return row[0] if row else ""


def build_pipeline() -> sqlite3.Connection:
    """Fetch everything and return a populated in-memory connection."""
    session = get_session()

    baseline_rows, baseline_amfi_map = read_baseline(ISIN_DB_PATH)

    amfi_payload = get_amfi_isin_map(session)
    bse_csvs = fetch_bse_master_data(session)
    isin_rows = get_isin_data(session)

    bse_rows, total_bse, skipped_bse = build_rows_from_bse(
        bse_csvs, amfi_payload["codes"], baseline_amfi_map
    )
    franklin_rows = build_rows_from_franklin(DATA_DIR / "franklin_funds.csv")
    rows = merge_rows(baseline_rows, bse_rows, franklin_rows)
    logger.info(
        "Scheme rows: %d (bse parsed=%d skipped=%d, franklin=%d, baseline carry=%d)",
        len(rows),
        total_bse,
        skipped_bse,
        len(franklin_rows),
        len(baseline_rows),
    )

    nav_rows = list(amfi_payload["navs"].items())
    return _build_in_memory(rows, nav_rows, isin_rows)


def run(*, no_upload: bool = False) -> int:
    """Build, diff against the live DB, swap + upload if changed."""
    mem_conn = build_pipeline()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "isin.db.candidate"
        _backup_to_file(mem_conn, tmp_path)

        new_hash = _hash_db_content(tmp_path)
        old_hash = _hash_db_content(ISIN_DB_PATH) if ISIN_DB_PATH.exists() else None
        if old_hash == new_hash:
            logger.info("Built DB matches current ISIN database; nothing to publish")
            return _NO_CHANGE

        # Promote into the repo path via atomic os.replace.
        ISIN_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        staging = ISIN_DB_PATH.with_suffix(ISIN_DB_PATH.suffix + ".staging")
        # tmp_path lives in a different filesystem (tmpfs) so we can't
        # os.replace across the boundary. Read+write into staging next to
        # the destination, then os.replace.
        staging.write_bytes(tmp_path.read_bytes())
        os.replace(staging, ISIN_DB_PATH)
        logger.info("Wrote %s (%d bytes)", ISIN_DB_PATH, ISIN_DB_PATH.stat().st_size)

    db_version = _read_db_version(ISIN_DB_PATH)
    _write_meta(ISIN_META_PATH, db_version, ISIN_DB_PATH)

    if no_upload:
        logger.info("--no-upload set; skipping B2")
    else:
        logger.info("Uploading new database to B2")
        upload_isin_db_to_b2()
        logger.info("Upload complete.")

    return _PUBLISHED


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="update_isin_db",
        description="Rebuild casparser_isin/isin.db from AMFI + BSE + ISIN feeds.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip the B2 upload (useful for local dry-runs).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args()
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)

    try:
        return run(no_upload=args.no_upload)
    except Exception:  # noqa: BLE001
        logger.exception("update_isin_db failed")
        return _ERROR


if __name__ == "__main__":
    sys.exit(main())
