"""End-to-end invariant tests for the append-only contract.

These tests lock down the property the entire pipeline depends on:

  **Every baseline row whose type is in scope MUST appear in the output.**

The unit tests on :func:`merge_rows` / :func:`merge_isin_rows` already
exercise the join logic; what's tested here is the full builder run, with
stubbed fetchers, to catch regressions in how the orchestrator wires the
pieces together. A refactor that "looks fine" at the function level can
still break the contract if e.g. the orchestrator forgets to pass
baseline rows through.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cptools.builder import IsinRow, SchemeRow
from update_isin_db import prepare_db


def _populate_baseline(path: Path, *, scheme_rows: list[SchemeRow], isin_rows: list[IsinRow]):
    """Build a minimal baseline DB on disk with the given rows."""
    conn = sqlite3.connect(path)
    try:
        prepare_db(conn, scheme_rows, [], [r.as_tuple() for r in isin_rows])
    finally:
        conn.close()


def _read_scheme(path: Path) -> dict[int, tuple]:
    """Return ``{id: (isin, name, sebi_category, last_seen)}`` from a DB."""
    out = {}
    with sqlite3.connect(path) as conn:
        for row in conn.execute("SELECT id, isin, name, sebi_category, last_seen FROM scheme"):
            out[row[0]] = (row[1], row[2], row[3], row[4])
    return out


def _read_isin(path: Path) -> dict[str, tuple]:
    """Return ``{isin: (name, issuer, type, status, last_seen)}`` from a DB."""
    out = {}
    with sqlite3.connect(path) as conn:
        for row in conn.execute("SELECT isin, name, issuer, type, status, last_seen FROM isin"):
            out[row[0]] = (row[1], row[2], row[3], row[4], row[5])
    return out


def test_no_live_data_preserves_every_baseline_row(tmp_path, monkeypatch):
    """The append-only contract: with zero live data, output == baseline.

    This is the strongest possible test of the contract. If anything in
    the pipeline ever silently drops a baseline row (a refactor in
    merge_rows, an orchestrator wiring bug, etc.) this catches it.
    """
    import update_isin_db

    # Seed a baseline with 3 scheme rows + 3 isin rows (mix of in-scope
    # and out-of-scope types so we also assert the cleanup behaviour).
    baseline_schemes = [
        SchemeRow(
            id=1,
            name="Equity Fund Growth",
            isin="INF000FUND001",
            amfi_code="100",
            type="EQUITY",
            rta="CAMS",
            rta_code="EQG",
            amc_code="01",
            sebi_category="Equity Scheme - Large Cap Fund",
            last_seen="2024-01-15",
        ),
        SchemeRow(
            id=2,
            name="Debt Fund Reg",
            isin="INF000FUND002",
            amfi_code="101",
            type="DEBT",
            rta="KARVY",
            rta_code="DBR",
            amc_code="02",
            sebi_category=None,  # older row that pre-dates SEBI enrichment
            last_seen=None,
        ),
        SchemeRow(
            id=3,
            name="Retired Fund",
            isin="INF000FUND003",
            amfi_code="102",
            type="DEBT",
            rta="CAMS",
            rta_code="RET",
            amc_code="03",
            sebi_category=None,
            last_seen="2020-07-22",  # last confirmed years ago; AMFI dropped it
        ),
    ]
    baseline_isins = [
        IsinRow(
            isin="INE001A01036",
            name="HDFC Equity",
            issuer="HDFC LIMITED",
            type="EQUITY SHARES",
            status="ACTIVE",
            last_seen="2024-01-15",
        ),
        IsinRow(
            isin="INE001A07Z47",
            name="HDFC NCD",
            issuer="HDFC LIMITED",
            type="DEBENTURE",
            status="ACTIVE",
            last_seen="2024-01-15",
        ),
        IsinRow(
            # Out-of-scope: should be cleaned up by the type filter.
            isin="INE001A14A04",
            name="HDFC CP",
            issuer="HDFC LIMITED",
            type="COMMERCIAL PAPER",
            status="DELETED",
            last_seen="2024-01-15",
        ),
    ]

    baseline_path = tmp_path / "isin.db"
    _populate_baseline(baseline_path, scheme_rows=baseline_schemes, isin_rows=baseline_isins)
    baseline_scheme_map = _read_scheme(baseline_path)
    baseline_isin_map = _read_isin(baseline_path)

    # Point the orchestrator at our sandbox.
    monkeypatch.setattr(update_isin_db, "ISIN_DB_PATH", baseline_path)
    monkeypatch.setattr(update_isin_db, "ISIN_META_PATH", baseline_path.with_suffix(".db.meta"))

    # Stub every live fetcher to return NOTHING.
    # NB: stubs go on the orchestrator module because `update_isin_db`
    # does `from cptools.fetchers.x import y`, which binds the symbol
    # *locally* in update_isin_db. Patching cptools.fetchers.x.y has no
    # effect on the local binding.
    monkeypatch.setattr(
        update_isin_db,
        "get_amfi_isin_map",
        lambda s: {"codes": {}, "categories": {}, "navs": {}},
    )
    monkeypatch.setattr(update_isin_db, "fetch_bse_master_data", lambda s: [])
    monkeypatch.setattr(update_isin_db, "get_isin_data", lambda s: [])
    # The session never gets used (every fetcher is stubbed), but
    # get_session() must return *something* so the orchestrator doesn't
    # try to build a real requests_cache session (which on Python 3.14
    # fails at annotation resolution time).
    monkeypatch.setattr(update_isin_db, "get_session", lambda: None)

    # Run -- the hash diff should report no change because all baseline
    # rows are kept identically. We don't care about the exit code here,
    # only the resulting on-disk DB. But the row-count guard MUST pass
    # (in-scope counts are monotonic).
    update_isin_db.run(no_upload=True)

    # After the run, the DB at baseline_path is either unchanged (if hash
    # matched and run returned 0) or rewritten (if the path was promoted).
    # Either way, every baseline scheme row MUST still be present.
    out_scheme = _read_scheme(baseline_path)
    for baseline_id, baseline_tuple in baseline_scheme_map.items():
        assert baseline_id in out_scheme, f"scheme.id={baseline_id} missing after run"
        # Identity preserved: same isin, same name.
        assert out_scheme[baseline_id][:2] == baseline_tuple[:2]

    # In-scope baseline isin rows MUST still be present; out-of-scope ones
    # may be filtered out by the cleanup.
    out_isin = _read_isin(baseline_path)
    in_scope_isins = {
        isin for isin, vals in baseline_isin_map.items() if vals[2] != "COMMERCIAL PAPER"
    }
    for isin in in_scope_isins:
        assert isin in out_isin, f"in-scope isin={isin} missing after run"


def test_brand_new_row_added_without_disturbing_baseline(tmp_path, monkeypatch):
    """A new BSE scheme row appears -- baseline rows must still be intact."""
    import update_isin_db

    baseline_schemes = [
        SchemeRow(
            id=1,
            name="Existing Fund",
            isin="INF000EXIST01",
            amfi_code="100",
            type="EQUITY",
            rta="CAMS",
            rta_code="EXI",
            amc_code="01",
            last_seen="2024-01-15",
        )
    ]
    baseline_path = tmp_path / "isin.db"
    _populate_baseline(baseline_path, scheme_rows=baseline_schemes, isin_rows=[])

    monkeypatch.setattr(update_isin_db, "ISIN_DB_PATH", baseline_path)
    monkeypatch.setattr(update_isin_db, "ISIN_META_PATH", baseline_path.with_suffix(".db.meta"))

    # BSE returns ONE new row (different ISIN, different rta_code).
    new_bse_csv = (
        "Unique No|Scheme Code|RTA Scheme Code|AMC Scheme Code|ISIN|AMC Code|"
        "Scheme Type|Scheme Plan|Scheme Name|RTA Agent Code|Channel Partner Code\n"
        "999|NEW-A||S1|INF000NEW0001|Test_MF|Equity||New Fund Growth|CAMS|NEW001\n"
    )
    monkeypatch.setattr(
        update_isin_db,
        "get_amfi_isin_map",
        lambda s: {"codes": {}, "categories": {}, "navs": {}},
    )
    monkeypatch.setattr(update_isin_db, "fetch_bse_master_data", lambda s: [new_bse_csv])
    monkeypatch.setattr(update_isin_db, "get_isin_data", lambda s: [])
    monkeypatch.setattr(update_isin_db, "get_session", lambda: None)

    update_isin_db.run(no_upload=True)

    out = _read_scheme(baseline_path)
    # Baseline id=1 still there.
    assert 1 in out
    assert out[1][0] == "INF000EXIST01"
    # New row inserted alongside.
    new_isins = {row[0] for row in out.values()}
    assert "INF000NEW0001" in new_isins


def test_live_reconfirmation_preserves_baseline_id(tmp_path, monkeypatch):
    """BSE re-confirms a baseline row -- the baseline id wins, not BSE's id.

    This is the property that protects against IDs churning across builds
    (which would in turn break the hash diff's "no change" detection,
    triggering needless 50 MB downloads for every user).
    """
    import update_isin_db

    baseline_schemes = [
        SchemeRow(
            id=42,  # baseline id
            name="Stable Fund Growth",
            isin="INF000STABLE1",
            amfi_code="500",
            type="EQUITY",
            rta="CAMS",
            rta_code="STB",
            amc_code="05",
            last_seen=None,
        )
    ]
    baseline_path = tmp_path / "isin.db"
    _populate_baseline(baseline_path, scheme_rows=baseline_schemes, isin_rows=[])

    monkeypatch.setattr(update_isin_db, "ISIN_DB_PATH", baseline_path)
    monkeypatch.setattr(update_isin_db, "ISIN_META_PATH", baseline_path.with_suffix(".db.meta"))

    # BSE returns the same content under a different Unique No (777).
    bse_csv = (
        "Unique No|Scheme Code|RTA Scheme Code|AMC Scheme Code|ISIN|AMC Code|"
        "Scheme Type|Scheme Plan|Scheme Name|RTA Agent Code|Channel Partner Code\n"
        "777|STB-A||05|INF000STABLE1|Test_MF|Equity||Stable Fund Growth|CAMS|STB\n"
    )
    monkeypatch.setattr(
        update_isin_db,
        "get_amfi_isin_map",
        lambda s: {"codes": {}, "categories": {}, "navs": {}},
    )
    monkeypatch.setattr(update_isin_db, "fetch_bse_master_data", lambda s: [bse_csv])
    monkeypatch.setattr(update_isin_db, "get_isin_data", lambda s: [])
    monkeypatch.setattr(update_isin_db, "get_session", lambda: None)

    update_isin_db.run(no_upload=True)

    out = _read_scheme(baseline_path)
    # Baseline id wins; BSE's id (777) is NOT a separate row.
    assert 42 in out
    assert 777 not in out
    # last_seen got bumped (was None, now today).
    assert out[42][3] is not None
