"""Tests for the row-count guard in :mod:`update_isin_db`.

The guard is what stops a bad upstream day (BSE returns partial CSV, AMFI
serves an interstitial, captn3m0 ships a truncated dump) from publishing
a degraded DB. Each test sets up a baseline DB on disk and a candidate
DB on disk, then calls ``_check_row_counts`` directly so we exercise the
real comparison logic without rebuilding the whole pipeline.
"""

from __future__ import annotations

import sqlite3

import pytest
from update_isin_db import RowCountGuardError, _check_row_counts


def _make_db(path, *, scheme_rows=0, isin_rows=None, nav_rows=0):
    """Build a minimal DB on disk shaped like a real isin.db.

    ``isin_rows`` is an iterable of ``(isin, type)`` -- name/issuer/status
    are filled with placeholders. The guard only looks at row counts and
    types, so that's all we need.
    """
    isin_rows = list(isin_rows or [])
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE scheme(id INTEGER PRIMARY KEY, name, isin, "
            "amfi_code, type, rta, rta_code, amc_code)"
        )
        conn.execute("CREATE TABLE isin(isin NOT NULL PRIMARY KEY, name, issuer, type, status)")
        conn.execute("CREATE TABLE nav20180131(isin NOT NULL PRIMARY KEY, nav)")
        conn.executemany(
            "INSERT INTO scheme VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (i, f"Fund {i}", f"INF{i:09d}", str(i), "EQUITY", "CAMS", f"T{i}", str(i))
                for i in range(1, scheme_rows + 1)
            ],
        )
        conn.executemany(
            "INSERT INTO isin VALUES (?, ?, ?, ?, ?)",
            [
                (isin, f"Sec {idx}", "Issuer", type_, "ACTIVE")
                for idx, (isin, type_) in enumerate(isin_rows)
            ],
        )
        conn.executemany(
            "INSERT INTO nav20180131 VALUES (?, ?)",
            [(f"INF{i:09d}", str(10.0 + i)) for i in range(1, nav_rows + 1)],
        )


def test_guard_no_baseline_is_noop(tmp_path):
    candidate = tmp_path / "candidate.db"
    _make_db(candidate, scheme_rows=100, isin_rows=[("INE001", "EQUITY SHARES")], nav_rows=5)
    # Baseline path doesn't exist -> guard quietly skips.
    _check_row_counts(tmp_path / "nonexistent.db", candidate)


def test_guard_passes_when_counts_grow(tmp_path):
    baseline = tmp_path / "baseline.db"
    candidate = tmp_path / "candidate.db"
    _make_db(baseline, scheme_rows=100, isin_rows=[("INE001", "EQUITY SHARES")], nav_rows=5)
    _make_db(
        candidate,
        scheme_rows=110,
        isin_rows=[("INE001", "EQUITY SHARES"), ("INE002", "EQUITY SHARES")],
        nav_rows=5,
    )
    _check_row_counts(baseline, candidate)  # no exception


def test_guard_passes_when_counts_stay_equal(tmp_path):
    baseline = tmp_path / "baseline.db"
    candidate = tmp_path / "candidate.db"
    _make_db(baseline, scheme_rows=100, isin_rows=[("INE001", "EQUITY SHARES")], nav_rows=5)
    _make_db(candidate, scheme_rows=100, isin_rows=[("INE001", "EQUITY SHARES")], nav_rows=5)
    _check_row_counts(baseline, candidate)


def test_guard_fails_on_scheme_drop(tmp_path):
    baseline = tmp_path / "baseline.db"
    candidate = tmp_path / "candidate.db"
    _make_db(baseline, scheme_rows=100, isin_rows=[("INE001", "EQUITY SHARES")], nav_rows=5)
    _make_db(candidate, scheme_rows=99, isin_rows=[("INE001", "EQUITY SHARES")], nav_rows=5)
    with pytest.raises(RowCountGuardError, match="scheme row count dropped"):
        _check_row_counts(baseline, candidate)


def test_guard_fails_on_nav_drop(tmp_path):
    baseline = tmp_path / "baseline.db"
    candidate = tmp_path / "candidate.db"
    _make_db(baseline, scheme_rows=100, isin_rows=[("INE001", "EQUITY SHARES")], nav_rows=10)
    _make_db(candidate, scheme_rows=100, isin_rows=[("INE001", "EQUITY SHARES")], nav_rows=5)
    with pytest.raises(RowCountGuardError, match="nav20180131 row count dropped"):
        _check_row_counts(baseline, candidate)


def test_guard_allows_out_of_scope_isin_cleanup(tmp_path):
    """The very first run with the new schema drops baseline CP/CD/etc.

    The guard MUST not interpret that as a degraded feed -- those rows are
    intentionally out of scope per the KEEP_TYPES contract.
    """
    baseline = tmp_path / "baseline.db"
    candidate = tmp_path / "candidate.db"
    # Baseline: 1 in-scope + 3 out-of-scope rows.
    _make_db(
        baseline,
        scheme_rows=100,
        isin_rows=[
            ("INE001", "EQUITY SHARES"),
            ("INE002", "COMMERCIAL PAPER"),
            ("INE003", "CERTIFICATE OF DEPOSIT"),
            ("INE004", "TREASURY BILLS"),
        ],
        nav_rows=5,
    )
    # Candidate: only the 1 in-scope row survives the filter -- this is
    # the intended "one-time cleanup" behavior.
    _make_db(
        candidate,
        scheme_rows=100,
        isin_rows=[("INE001", "EQUITY SHARES")],
        nav_rows=5,
    )
    _check_row_counts(baseline, candidate)  # no exception


def test_guard_normalises_baseline_type_casing(tmp_path):
    """Baseline rows with lowercase 'Debenture' MUST count as in-scope.

    Without canonicalisation the guard would see "Debenture" not in
    KEEP_TYPES and undercount the baseline -- which would let real
    drops slip through unnoticed (silent failure mode).
    """
    baseline = tmp_path / "baseline.db"
    candidate = tmp_path / "candidate.db"
    _make_db(
        baseline,
        scheme_rows=100,
        # 2 in-scope baseline rows, one with lowercase casing-bug variant.
        isin_rows=[
            ("INE001", "EQUITY SHARES"),
            ("INE002", "Debenture"),  # legacy lowercase variant
        ],
        nav_rows=5,
    )
    _make_db(
        candidate,
        scheme_rows=100,
        # Candidate only kept 1 of those 2 -- a real drop.
        isin_rows=[("INE001", "EQUITY SHARES")],
        nav_rows=5,
    )
    with pytest.raises(RowCountGuardError, match="isin in-scope row count dropped"):
        _check_row_counts(baseline, candidate)


def test_guard_fails_on_in_scope_isin_drop(tmp_path):
    """A real drop in equity ISINs (e.g. captn3m0 ships truncated CSV)
    must abort the build."""
    baseline = tmp_path / "baseline.db"
    candidate = tmp_path / "candidate.db"
    _make_db(
        baseline,
        scheme_rows=100,
        isin_rows=[
            ("INE001", "EQUITY SHARES"),
            ("INE002", "EQUITY SHARES"),
            ("INE003", "EQUITY SHARES"),
        ],
        nav_rows=5,
    )
    _make_db(
        candidate,
        scheme_rows=100,
        isin_rows=[("INE001", "EQUITY SHARES")],
        nav_rows=5,
    )
    with pytest.raises(RowCountGuardError, match="isin in-scope row count dropped"):
        _check_row_counts(baseline, candidate)
