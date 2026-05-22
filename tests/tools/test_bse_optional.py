"""Tests for the BSE-optional path in :mod:`update_isin_db`.

The library's lookup code is ISIN-first since v1.0. Skipping or failing
the BSE step degrades a *fallback* lookup path, not the primary one. The
cron must therefore stay green when BSE goes sideways:

- ``CASPARSER_ISIN_TOOLS_NO_BSE=1`` -> explicit skip, INFO log
- BSE raises                       -> exception caught, WARNING log, run continues
- Either way                       -> baseline carry-forward keeps the rta_code
                                       table populated

These tests exercise the orchestrator's three states (ok / disabled /
failed) via stubbed fetchers, asserting the resulting DB is valid in
each case.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from cptools.builder import IsinRow, SchemeRow


def _populate_baseline(path: Path, scheme_rows: list[SchemeRow], isin_rows: list[IsinRow]):
    """Seed a baseline isin.db on disk."""
    from update_isin_db import prepare_db

    conn = sqlite3.connect(path)
    try:
        prepare_db(conn, scheme_rows, [], [r.as_tuple() for r in isin_rows])
    finally:
        conn.close()


def _scheme_count(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT COUNT(*) FROM scheme").fetchone()[0]


def _stub_amfi_and_isin(monkeypatch, mod):
    """Stub AMFI/ISIN/Franklin so only BSE is in play for the test."""
    monkeypatch.setattr(
        mod,
        "get_amfi_isin_map",
        lambda s: {"codes": {}, "categories": {}, "navs": {}},
    )
    monkeypatch.setattr(mod, "get_isin_data", lambda s: [])
    monkeypatch.setattr(mod, "get_session", lambda: None)


def test_bse_disabled_via_env_var_succeeds(tmp_path, monkeypatch, caplog):
    """CASPARSER_ISIN_TOOLS_NO_BSE=1 -> BSE skipped; baseline preserved."""
    import update_isin_db

    baseline = [
        SchemeRow(
            id=1,
            name="Existing Fund",
            isin="INF000EXIST01",
            amfi_code="100",
            type="EQUITY",
            rta="CAMS",
            rta_code="EXIST",
            amc_code="01",
            last_seen="2024-01-15",
        )
    ]
    db_path = tmp_path / "isin.db"
    _populate_baseline(db_path, baseline, [])
    monkeypatch.setattr(update_isin_db, "ISIN_DB_PATH", db_path)
    monkeypatch.setattr(update_isin_db, "ISIN_META_PATH", db_path.with_suffix(".db.meta"))
    _stub_amfi_and_isin(monkeypatch, update_isin_db)
    # The BSE fetcher must NOT be called -- if it is, the test will hit a
    # real network. Stub it to raise so a wrong code path fails loudly.
    monkeypatch.setattr(
        update_isin_db,
        "fetch_bse_master_data",
        lambda s: (_ for _ in ()).throw(AssertionError("BSE fetcher must not be called")),
    )

    monkeypatch.setenv("CASPARSER_ISIN_TOOLS_NO_BSE", "1")
    with caplog.at_level(logging.INFO, logger="cptools"):
        update_isin_db.run(no_upload=True)

    # Baseline still there.
    assert _scheme_count(db_path) >= 1
    # The disabled log line was emitted.
    assert any("BSE disabled" in r.message for r in caplog.records)
    # bse_status=disabled surfaces in the merge-stats line.
    assert any("bse_status=disabled" in r.message for r in caplog.records)


def test_bse_failure_caught_run_continues(tmp_path, monkeypatch, caplog):
    """BSE raising during fetch -> WARNING + ERROR logged, run completes."""
    import update_isin_db

    baseline = [
        SchemeRow(
            id=1,
            name="Existing Fund",
            isin="INF000EXIST01",
            amfi_code="100",
            type="EQUITY",
            rta="CAMS",
            rta_code="EXIST",
            amc_code="01",
            last_seen="2024-01-15",
        )
    ]
    db_path = tmp_path / "isin.db"
    _populate_baseline(db_path, baseline, [])
    monkeypatch.setattr(update_isin_db, "ISIN_DB_PATH", db_path)
    monkeypatch.setattr(update_isin_db, "ISIN_META_PATH", db_path.with_suffix(".db.meta"))
    _stub_amfi_and_isin(monkeypatch, update_isin_db)
    monkeypatch.delenv("CASPARSER_ISIN_TOOLS_NO_BSE", raising=False)

    def _raise_bse(_session):
        raise RuntimeError("simulated BSE WAF interstitial")

    monkeypatch.setattr(update_isin_db, "fetch_bse_master_data", _raise_bse)

    # INFO level captures the merge-stats line; WARNING and ERROR for the
    # exception path. Set to INFO so all three are in caplog.records.
    with caplog.at_level(logging.INFO, logger="cptools"):
        update_isin_db.run(no_upload=True)

    # Baseline still in the DB.
    assert _scheme_count(db_path) >= 1
    # The WARNING log captured the exception.
    assert any(
        "BSE fetch failed" in r.message and r.levelno == logging.WARNING for r in caplog.records
    )
    # The follow-on ERROR is emitted so cron mail flags it.
    assert any(
        "BSE fetch failed this run" in r.message and r.levelno == logging.ERROR
        for r in caplog.records
    )
    # bse_status=failed surfaces in the merge-stats line.
    assert any("bse_status=failed" in r.message for r in caplog.records)


def test_bse_failure_does_not_trip_row_count_guard(tmp_path, monkeypatch):
    """The row-count guard must pass even when BSE contributes zero rows.

    With BSE absent, the candidate scheme count equals baseline + new
    franklin/AMFI rows -- always >= baseline. Append-only invariant holds.
    """
    import update_isin_db

    # Larger baseline to make sure the guard has something to compare against.
    baseline = [
        SchemeRow(
            id=i,
            name=f"Fund {i}",
            isin=f"INF{i:09d}",
            amfi_code=str(i),
            type="EQUITY",
            rta="CAMS",
            rta_code=f"T{i}",
            amc_code=str(i),
            last_seen="2024-01-15",
        )
        for i in range(1, 11)
    ]
    db_path = tmp_path / "isin.db"
    _populate_baseline(db_path, baseline, [])
    monkeypatch.setattr(update_isin_db, "ISIN_DB_PATH", db_path)
    monkeypatch.setattr(update_isin_db, "ISIN_META_PATH", db_path.with_suffix(".db.meta"))
    _stub_amfi_and_isin(monkeypatch, update_isin_db)
    monkeypatch.setattr(
        update_isin_db,
        "fetch_bse_master_data",
        lambda s: (_ for _ in ()).throw(RuntimeError("BSE down")),
    )
    monkeypatch.delenv("CASPARSER_ISIN_TOOLS_NO_BSE", raising=False)

    # Should not raise RowCountGuardError -- baseline is fully preserved.
    update_isin_db.run(no_upload=True)
    assert _scheme_count(db_path) >= len(baseline)
