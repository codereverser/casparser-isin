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
    IsinRow,
    SchemeRow,
    build_rows_from_bse,
    build_rows_from_franklin,
    merge_isin_rows,
    merge_rows,
    read_baseline,
    read_baseline_isin,
)
from cptools.fetchers.amfi import get_amfi_isin_map
from cptools.fetchers.bse import fetch_bse_master_data
from cptools.fetchers.isin import KEEP_TYPES, get_isin_data
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
                rta, rta_code, amc_code,
                sebi_category,
                last_seen
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
            "INSERT INTO scheme"
            "(id, name, isin, amfi_code, type, rta, rta_code, amc_code, "
            "sebi_category, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r.as_tuple() if isinstance(r, SchemeRow) else r for r in rows),
        )

        conn.execute("CREATE TABLE nav20180131(isin NOT NULL PRIMARY KEY, nav)")
        conn.executemany("INSERT INTO nav20180131(isin, nav) VALUES (?, ?)", nav_rows)

        conn.execute(
            "CREATE TABLE isin(isin NOT NULL PRIMARY KEY, name, issuer, type, status, last_seen)"
        )
        conn.executemany(
            "INSERT INTO isin(isin, name, issuer, type, status, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (r.as_tuple() if isinstance(r, IsinRow) else r for r in isin_rows),
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


class RowCountGuardError(RuntimeError):
    """Raised when a candidate DB has fewer in-scope rows than the baseline.

    Indicates a feed went bad (truncated CSV, WAF interstitial, partial
    response). The candidate is NOT promoted; the operator must investigate.
    """


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _count_baseline_isin_in_scope(conn: sqlite3.Connection) -> int:
    """Count baseline isin rows whose type is in KEEP_TYPES.

    Applies the same casing normalisation the fetcher does, so a baseline
    row with type='Debenture' (lowercase legacy variant) counts the same
    as one with 'DEBENTURE'. Without this, the first run under the new
    filter would falsely flag those rows as "dropped."
    """
    from cptools.fetchers.isin import _TYPE_CANONICAL

    count = 0
    for (t,) in conn.execute("SELECT type FROM isin"):
        canonical = _TYPE_CANONICAL.get(t, t)
        if canonical in KEEP_TYPES:
            count += 1
    return count


def _check_row_counts(baseline_path: Path, candidate_path: Path) -> None:
    """Refuse to publish if any table's in-scope row count drops.

    For ``scheme`` and ``nav20180131`` the comparison is strict monotonic
    (append-only). For ``isin`` we count only baseline rows whose type is
    in :data:`KEEP_TYPES` -- the one-time cleanup of out-of-scope rows on
    the first run with the new schema must not trip the guard.

    No-op when baseline doesn't exist (fresh checkout / stateless container).
    """
    if not baseline_path.exists():
        logger.info("No baseline DB; skipping row-count guard")
        return

    with (
        closing(sqlite3.connect(baseline_path)) as b,
        closing(sqlite3.connect(candidate_path)) as c,
    ):
        # scheme: strict append-only
        if _table_exists(b, "scheme"):
            baseline_scheme = b.execute("SELECT COUNT(*) FROM scheme").fetchone()[0]
        else:
            baseline_scheme = 0
        candidate_scheme = c.execute("SELECT COUNT(*) FROM scheme").fetchone()[0]
        if candidate_scheme < baseline_scheme:
            raise RowCountGuardError(
                f"scheme row count dropped: baseline={baseline_scheme}, "
                f"candidate={candidate_scheme}. Refusing to publish."
            )

        # isin: in-scope subset only (one-time cleanup of CP/CD/etc. allowed)
        if _table_exists(b, "isin"):
            baseline_isin_in_scope = _count_baseline_isin_in_scope(b)
        else:
            baseline_isin_in_scope = 0
        candidate_isin = c.execute("SELECT COUNT(*) FROM isin").fetchone()[0]
        if candidate_isin < baseline_isin_in_scope:
            raise RowCountGuardError(
                f"isin in-scope row count dropped: baseline_in_scope="
                f"{baseline_isin_in_scope}, candidate={candidate_isin}. "
                "Refusing to publish."
            )

        # nav20180131: static frozen reference -- never shrinks
        if _table_exists(b, "nav20180131"):
            baseline_nav = b.execute("SELECT COUNT(*) FROM nav20180131").fetchone()[0]
        else:
            baseline_nav = 0
        candidate_nav = c.execute("SELECT COUNT(*) FROM nav20180131").fetchone()[0]
        if candidate_nav < baseline_nav:
            raise RowCountGuardError(
                f"nav20180131 row count dropped: baseline={baseline_nav}, "
                f"candidate={candidate_nav}. Refusing to publish."
            )

        logger.info(
            "Row-count guard OK: scheme %d->%d, isin %d (in-scope baseline)->%d, nav %d->%d",
            baseline_scheme,
            candidate_scheme,
            baseline_isin_in_scope,
            candidate_isin,
            baseline_nav,
            candidate_nav,
        )


def _log_scheme_merge_stats(
    baseline: dict,
    bse_rows: dict,
    franklin_rows: dict,
    merged: list,
) -> None:
    """Emit per-source breakdown for the scheme merge.

    The numbers should be readable at a glance:

    - ``baseline_kept`` must equal ``len(baseline)`` under the append-only
      contract. Anything else means a row was dropped -- a bug.
    - ``bse_new`` / ``franklin_new`` count rows whose dedupe_key didn't
      exist in the baseline (genuinely new schemes).
    - ``bse_reconfirmed`` / ``franklin_reconfirmed`` count rows that
      matched a baseline dedupe_key (last_seen got bumped).
    """
    baseline_keys = {r.dedupe_key() for r in baseline.values()}
    bse_keys = {r.dedupe_key() for r in bse_rows.values()}
    franklin_keys = {r.dedupe_key() for r in franklin_rows.values()}
    merged_ids = {r.id for r in merged}

    baseline_kept = sum(1 for r in baseline.values() if r.id in merged_ids)
    baseline_dropped = len(baseline) - baseline_kept
    bse_new = len(bse_keys - baseline_keys)
    bse_reconfirmed = len(bse_keys & baseline_keys)
    franklin_new = len(franklin_keys - baseline_keys - bse_keys)
    franklin_reconfirmed = len(franklin_keys & baseline_keys)

    logger.info(
        "scheme merge: %d rows total | baseline_kept=%d bse_new=%d "
        "bse_reconfirmed=%d franklin_new=%d franklin_reconfirmed=%d "
        "baseline_dropped=%d",
        len(merged),
        baseline_kept,
        bse_new,
        bse_reconfirmed,
        franklin_new,
        franklin_reconfirmed,
        baseline_dropped,
    )
    if baseline_dropped != 0:
        # Append-only contract violation. The row-count guard should also
        # catch this, but log at ERROR so it's visible in cron mail.
        logger.error(
            "Append-only contract violated: %d baseline scheme rows dropped",
            baseline_dropped,
        )


def _log_isin_merge_stats(
    baseline: dict,
    fresh_rows: list,
    merged: list,
) -> None:
    """Emit per-source breakdown for the isin merge.

    Note that for the isin table, ``baseline_cleanup`` is expected to be
    non-zero on the first run with the new schema (out-of-scope CP / CD /
    T-Bills / Securitised / MF UNIT rows are filtered out). Subsequent
    runs should have ``baseline_cleanup=0``.
    """
    baseline_in_scope_isins = {isin for isin, r in baseline.items() if r.type in KEEP_TYPES}
    fresh_isins = {row[0] for row in fresh_rows}
    merged_isins = {r.isin for r in merged}

    baseline_kept = len(baseline_in_scope_isins & merged_isins)
    baseline_cleanup = len(baseline) - len(baseline_in_scope_isins)
    in_scope_dropped = len(baseline_in_scope_isins) - baseline_kept
    live_new = len(fresh_isins - baseline_in_scope_isins)
    live_reconfirmed = len(fresh_isins & baseline_in_scope_isins)

    logger.info(
        "isin merge: %d rows total | baseline_kept=%d live_new=%d "
        "live_reconfirmed=%d baseline_cleanup=%d in_scope_dropped=%d",
        len(merged),
        baseline_kept,
        live_new,
        live_reconfirmed,
        baseline_cleanup,
        in_scope_dropped,
    )
    if in_scope_dropped != 0:
        logger.error(
            "Append-only contract violated: %d in-scope baseline isin rows dropped",
            in_scope_dropped,
        )


def build_pipeline() -> sqlite3.Connection:
    """Fetch everything and return a populated in-memory connection."""
    session = get_session()

    # --- scheme table -----------------------------------------------------
    baseline_rows, baseline_amfi_map = read_baseline(ISIN_DB_PATH)

    amfi_payload = get_amfi_isin_map(session)
    bse_csvs = fetch_bse_master_data(session)
    fresh_isin_rows = get_isin_data(session)  # already filtered + normalised

    bse_rows, total_bse, skipped_bse = build_rows_from_bse(
        bse_csvs,
        amfi_payload["codes"],
        baseline_amfi_map,
        sebi_categories=amfi_payload["categories"],
    )
    franklin_rows = build_rows_from_franklin(DATA_DIR / "franklin_funds.csv")
    rows = merge_rows(baseline_rows, bse_rows, franklin_rows)
    logger.info(
        "BSE source: parsed=%d skipped=%d kept=%d",
        total_bse,
        skipped_bse,
        len(bse_rows),
    )
    _log_scheme_merge_stats(baseline_rows, bse_rows, franklin_rows, rows)

    # --- isin table -------------------------------------------------------
    # Append-only merge: baseline rows of in-scope types are preserved, live
    # rows either re-confirm a baseline ISIN (bump last_seen + refresh
    # metadata) or insert as new. Out-of-scope baseline types are filtered
    # out -- equivalent to a one-time cleanup on the first run under the
    # new scope contract.
    baseline_isin = read_baseline_isin(ISIN_DB_PATH)
    isin_rows = merge_isin_rows(baseline_isin, fresh_isin_rows, keep_types=KEEP_TYPES)
    _log_isin_merge_stats(baseline_isin, fresh_isin_rows, isin_rows)

    nav_rows = list(amfi_payload["navs"].items())
    return _build_in_memory(rows, nav_rows, isin_rows)


def run(*, no_upload: bool = False) -> int:
    """Build, diff against the live DB, swap + upload if changed."""
    mem_conn = build_pipeline()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "isin.db.candidate"
        _backup_to_file(mem_conn, tmp_path)

        # Row-count guard runs BEFORE the hash diff so a "no change" run
        # that silently lost rows still aborts loudly.
        _check_row_counts(ISIN_DB_PATH, tmp_path)

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
