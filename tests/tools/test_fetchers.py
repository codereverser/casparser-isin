"""Unit tests for the pure-parsing pieces of the fetchers.

We don't exercise the HTTP layer here -- network is mocked or skipped at
the orchestrator level.
"""

from __future__ import annotations

import sqlite3 as _sqlite3
import textwrap
from contextlib import closing

from cptools.fetchers.amfi import parse_2018_nav_file, parse_amfi_nav_text
from cptools.fetchers.isin import KEEP_TYPES, parse_isin_db
from cptools.fetchers.nse import parse_nse_equity_csv


def test_parse_amfi_nav_text_extracts_isin_and_reinvest_flag():
    text = "\n".join(
        [
            "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            "",
            "Aditya Birla Sun Life Mutual Fund",
            "",
            (
                "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
                "Scheme Name;Net Asset Value;Date"
            ),
            "100001;INF209K01ABC;INF209K01DEF;Sample Fund - Growth;50.12;20-May-2026",
            "100002;INF209K01GHI;-;Sample Fund - Direct Growth;75.0;20-May-2026",
            "100003;-;INF209K01XYZ;Sample Fund - Reinvest Only;30.0;20-May-2026",
            "",
        ]
    )
    codes, _categories = parse_amfi_nav_text(text)

    # Payout + reinvest pair => first isin is_reinvest=False, second True
    assert codes["INF209K01ABC"] == ("100001", False)
    assert codes["INF209K01DEF"] == ("100001", True)
    # Solo isin -> is_reinvest=None
    assert codes["INF209K01GHI"] == ("100002", None)
    # Reinvest-only -> is_reinvest=True
    assert codes["INF209K01XYZ"] == ("100003", True)


def test_parse_amfi_nav_text_carries_sebi_category_via_section_headers():
    """Section headers should classify every ISIN until the next header arrives."""
    text = "\n".join(
        [
            "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            "",
            "Aditya Birla Sun Life Mutual Fund",
            "",
            "100001;INF000EQUITY01;-;Big Equity Fund;50.12;20-May-2026",
            "100002;INF000EQUITY02;-;Another Equity Fund;25.50;20-May-2026",
            "",
            "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)",
            "",
            "Aditya Birla Sun Life Mutual Fund",
            "",
            "200001;INF000DEBT001;-;Banking PSU Fund;104.38;20-May-2026",
            "",
            "Open Ended Schemes(Hybrid Scheme - Aggressive Hybrid Fund)",
            "",
            "300001;INF000HYBRID1;INF000HYBRID2;Hybrid Aggro Fund;15.0;20-May-2026",
            "",
            "Close Ended Schemes(Equity Scheme)",  # sub-category omitted
            "",
            "400001;INF000CLOSED1;-;Some Closed Fund;12.0;20-May-2026",
            "",
            "Interval Fund Schemes(Debt Scheme - Liquid Fund)",
            "",
            "500001;INF000INTERV1;-;Interval Liquid Fund;10.5;20-May-2026",
        ]
    )
    codes, categories = parse_amfi_nav_text(text)

    # Each ISIN inherits the most-recent section header.
    assert categories["INF000EQUITY01"] == "Equity Scheme - Large Cap Fund"
    assert categories["INF000EQUITY02"] == "Equity Scheme - Large Cap Fund"
    assert categories["INF000DEBT001"] == "Debt Scheme - Banking and PSU Fund"
    # Both isins of the same row carry the category.
    assert categories["INF000HYBRID1"] == "Hybrid Scheme - Aggressive Hybrid Fund"
    assert categories["INF000HYBRID2"] == "Hybrid Scheme - Aggressive Hybrid Fund"
    # Close-ended without a sub-category just exposes the bare label.
    assert categories["INF000CLOSED1"] == "Equity Scheme"
    # Interval Fund Schemes(...) header is also recognised.
    assert categories["INF000INTERV1"] == "Debt Scheme - Liquid Fund"
    # codes still populated for every ISIN.
    assert len(codes) == 7


def test_parse_amfi_nav_text_ignores_unknown_section_header_shapes():
    """A header shape AMFI hasn't used should not silently mis-classify rows."""
    text = "\n".join(
        [
            "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            "",
            "100001;INF000EQUITY01;-;Equity Fund;50.0;20-May-2026",
            "",
            "Some Mysterious Header Without Parens",  # not a header shape we know
            "",
            # The next ISIN should still carry the LAST recognised category,
            # because we deliberately leave current_category unchanged when a
            # header shape doesn't match. Better to keep a slightly-wrong
            # category than to flip everyone to NULL on a wording tweak.
            "100002;INF000EQUITY02;-;Another Equity Fund;25.0;20-May-2026",
        ]
    )
    _codes, categories = parse_amfi_nav_text(text)
    assert categories["INF000EQUITY01"] == "Equity Scheme - Large Cap Fund"
    assert categories["INF000EQUITY02"] == "Equity Scheme - Large Cap Fund"


def test_parse_amfi_nav_text_handles_no_header_before_first_row():
    """Data rows that precede any header should have no category."""
    text = "\n".join(
        [
            "100001;INF000NOHEAD1;-;Orphan Fund;10.0;20-May-2026",
            "",
            "Open Ended Schemes(Equity Scheme - Mid Cap Fund)",
            "",
            "100002;INF000MIDCAP1;-;Mid Cap Fund;20.0;20-May-2026",
        ]
    )
    codes, categories = parse_amfi_nav_text(text)
    assert "INF000NOHEAD1" in codes
    assert "INF000NOHEAD1" not in categories
    assert categories["INF000MIDCAP1"] == "Equity Scheme - Mid Cap Fund"


def test_parse_amfi_nav_text_skips_blank_lines_and_amc_names():
    """AMC names and column headers should be ignored, not crash."""
    text = (
        "Open Ended Schemes(Equity Scheme - Flexi Cap Fund)\n"
        "\n"
        "Some AMC Mutual Fund\n"
        "Scheme Code;ISIN1;ISIN2;name;nav;date\n"  # column header has non-digit first col
    )
    codes, categories = parse_amfi_nav_text(text)
    assert codes == {}
    assert categories == {}


def test_parse_2018_nav_file(tmp_path):
    path = tmp_path / "amfi2018.txt"
    path.write_text(
        textwrap.dedent(
            """\
            Open Ended Schemes(Equity)

            100100;INF000000001;INF000000002;Fund A;120;-;-;31-Jan-2018
            100101;INF000000003;-;Fund B;55;-;-;31-Jan-2018
            100102;INF000000004;INF000000005;Fund C - wrong date;99;-;-;15-Feb-2018
            """
        )
    )
    out = parse_2018_nav_file(path=path)
    # Wrong-date rows skipped
    assert "INF000000004" not in out["codes"]
    # Payout flag on isin1, reinvest on isin2
    assert out["codes"]["INF000000001"] == ("100100", False)
    assert out["codes"]["INF000000002"] == ("100100", True)
    # NAV captured for both isins of the same row
    assert out["navs"]["INF000000001"] == "120"
    assert out["navs"]["INF000000002"] == "120"
    # Single-isin row keeps single nav
    assert out["navs"]["INF000000003"] == "55"


# -----------------------------------------------------------------------------
# captn3m0 isin.db: type filter + casing normalisation
# -----------------------------------------------------------------------------
#
# Upstream switched from a CSV-in-main-branch to a SQLite database
# published as a versioned GitHub release asset. Fixture DBs below mirror
# that schema and use the same upstream column names.


def _make_isin_db(tmp_path, rows):
    """Build a fixture isin.db matching the captn3m0 schema (subset).

    ``rows`` is an iterable of dicts with keys: isin, description,
    issuer_name, security_type_name, status.
    """
    path = tmp_path / "captn3m0_fixture.db"
    with closing(_sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE isin ("
            "  isin TEXT PRIMARY KEY,"
            "  issuer_name TEXT,"
            "  description TEXT,"
            "  security_type_name TEXT,"
            "  status TEXT"
            ")"
        )
        conn.executemany(
            "INSERT INTO isin (isin, issuer_name, description, security_type_name, status) "
            "VALUES (:isin, :issuer_name, :description, :security_type_name, :status)",
            rows,
        )
    return path


def test_parse_isin_db_keeps_in_scope_types(tmp_path):
    db = _make_isin_db(
        tmp_path,
        [
            {
                "isin": "INE001A01036",
                "description": "HDFC LIMITED EQ FV RS 2",
                "issuer_name": "HDFC LIMITED",
                "security_type_name": "EQUITY SHARES",
                "status": "ACTIVE",
            },
            {
                "isin": "INE001A07Z47",
                "description": "HDFC NCD",
                "issuer_name": "HDFC LIMITED",
                "security_type_name": "DEBENTURE",
                "status": "ACTIVE",
            },
            {
                "isin": "INE131A04G64",
                "description": "Some Sovereign Gold Bond",
                "issuer_name": "RBI",
                "security_type_name": "SOVEREIGN GOLD BOND",
                "status": "ACTIVE",
            },
        ],
    )
    kept, dropped = parse_isin_db(db)
    assert len(kept) == 3
    assert dropped == {}


def test_parse_isin_db_drops_out_of_scope_types(tmp_path):
    db = _make_isin_db(
        tmp_path,
        [
            {
                "isin": "INE001A14A04",
                "description": "HDFC CP",
                "issuer_name": "HDFC LIMITED",
                "security_type_name": "COMMERCIAL PAPER",
                "status": "DELETED",
            },
            {
                "isin": "INE001A02XYZ",
                "description": "Some Bank CD",
                "issuer_name": "SOME BANK",
                "security_type_name": "CERTIFICATE OF DEPOSIT",
                "status": "ACTIVE",
            },
            {
                "isin": "INE001A05TBL",
                "description": "T-Bill 91D",
                "issuer_name": "GOI",
                "security_type_name": "TREASURY BILLS",
                "status": "DELETED",
            },
            {
                "isin": "INF001S22001",
                "description": "Some MF Unit",
                "issuer_name": "Some AMC",
                "security_type_name": "MUTUAL FUND UNIT",
                "status": "ACTIVE",
            },
            {
                "isin": "INE099B07001",
                "description": "Some PTC",
                "issuer_name": "Securitisation Trust",
                "security_type_name": "SECURITISED INSTRUMENT",
                "status": "DELETED",
            },
        ],
    )
    kept, dropped = parse_isin_db(db)
    assert kept == []
    assert dropped == {
        "COMMERCIAL PAPER": 1,
        "CERTIFICATE OF DEPOSIT": 1,
        "TREASURY BILLS": 1,
        "MUTUAL FUND UNIT": 1,
        "SECURITISED INSTRUMENT": 1,
    }


def test_parse_isin_db_normalises_type_casing(tmp_path):
    # The upstream has a handful of lowercase variants ("Debenture",
    # "Government Securities") mixed in with the uppercase rows. The
    # fetcher should collapse them to canonical UPPERCASE so the type
    # filter and downstream consumers see one form.
    db = _make_isin_db(
        tmp_path,
        [
            {
                "isin": "INE001A07XYZ",
                "description": "Some Debenture",
                "issuer_name": "Some Issuer",
                "security_type_name": "Debenture",  # lowercase variant
                "status": "ACTIVE",
            },
            {
                "isin": "IN000125G018",
                "description": "GOI 6.5% 2025",
                "issuer_name": "GOI",
                "security_type_name": "Government Securities",  # lowercase
                "status": "ACTIVE",
            },
        ],
    )
    kept, dropped = parse_isin_db(db)
    assert len(kept) == 2
    assert dropped == {}
    types = {row[3] for row in kept}
    assert types == {"DEBENTURE", "GOVERNMENT SECURITIES"}


def test_parse_isin_db_normalises_status_casing(tmp_path):
    db = _make_isin_db(
        tmp_path,
        [
            {
                "isin": "INE001A01999",
                "description": "Some Equity",
                "issuer_name": "Some Issuer",
                "security_type_name": "EQUITY SHARES",
                "status": "Deleted",  # lowercase-y variant
            }
        ],
    )
    kept, _ = parse_isin_db(db)
    assert len(kept) == 1
    assert kept[0][4] == "DELETED"


def test_parse_isin_db_drops_lowercase_out_of_scope_type(tmp_path):
    # "Securitised Instrument" (lowercase) canonicalises then is filtered
    # out. The dropped Counter records the raw form so the operator can
    # spot the casing-bug source.
    db = _make_isin_db(
        tmp_path,
        [
            {
                "isin": "INE099B07999",
                "description": "Some PTC",
                "issuer_name": "Securitisation Trust",
                "security_type_name": "Securitised Instrument",
                "status": "Deleted",
            }
        ],
    )
    kept, dropped = parse_isin_db(db)
    assert kept == []
    assert dropped == {"Securitised Instrument": 1}


def test_parse_isin_db_unknown_type_dropped_not_normalised(tmp_path):
    db = _make_isin_db(
        tmp_path,
        [
            {
                "isin": "INE001A99ZZZ",
                "description": "Some Future Instrument",
                "issuer_name": "Some Issuer",
                "security_type_name": "QUANTUM ENTANGLED BOND",  # made up
                "status": "ACTIVE",
            }
        ],
    )
    kept, dropped = parse_isin_db(db)
    assert kept == []
    assert dropped == {"QUANTUM ENTANGLED BOND": 1}


def test_parse_isin_db_empty_type_dropped_as_empty_sentinel(tmp_path):
    # Pending / placeholder rows in the upstream have an empty
    # security_type_name. They should be filtered out and tracked under
    # a clear "<empty>" sentinel so the drop counter is interpretable.
    db = _make_isin_db(
        tmp_path,
        [
            {
                "isin": "INF00XX30019",
                "description": "ITI MUTUAL FUND DIVINITI EQ LNGSHRTFD",
                "issuer_name": "ITI MUTUAL FUND",
                "security_type_name": "",
                "status": "ACTIVE",
            },
            {
                "isin": "INF00XX30027",
                "description": "Some other pending row",
                "issuer_name": "Some AMC",
                "security_type_name": None,
                "status": "ACTIVE",
            },
        ],
    )
    kept, dropped = parse_isin_db(db)
    assert kept == []
    assert dropped == {"<empty>": 2}


def test_keep_types_is_a_proper_subset_of_all_observed_types():
    # Lock the KEEP_TYPES set down so future edits to the filter list
    # surface in code review. The intent: equities + corporate bonds +
    # govt bonds + AIF/InvIT/REIT + rights/warrants/IDR. NOT money market.
    assert "EQUITY SHARES" in KEEP_TYPES
    assert "DEBENTURE" in KEEP_TYPES
    assert "PREFERENCE SHARES" in KEEP_TYPES
    assert "SOVEREIGN GOLD BOND" in KEEP_TYPES
    assert "ALTERNATIVE INVESTMENT FUND" in KEEP_TYPES
    assert "INFRASTRUCTURE INVESTMENT TRUST" in KEEP_TYPES
    assert "REAL ESTATE INVESTMENT TRUSTS" in KEEP_TYPES

    assert "COMMERCIAL PAPER" not in KEEP_TYPES
    assert "CERTIFICATE OF DEPOSIT" not in KEEP_TYPES
    assert "TREASURY BILLS" not in KEEP_TYPES
    assert "SECURITISED INSTRUMENT" not in KEEP_TYPES
    assert "MUTUAL FUND UNIT" not in KEEP_TYPES
    assert "MUTUAL FUND UNIT (TRASE)" not in KEEP_TYPES


class TestAssetCache:
    """The on-disk cache for the captn3m0 release asset (get_isin_data)."""

    def _setup(self, tmp_path, monkeypatch):
        """Point the cache dir at tmp_path and stub the network layer.

        Returns ``(isin_module, calls)`` where ``calls["n"]`` counts how many
        times an actual download happened.
        """
        import cptools.fetchers.isin as isin
        from cptools.settings import get_cache_dir

        monkeypatch.setenv("CASPARSER_ISIN_TOOLS_CACHE", str(tmp_path))
        monkeypatch.delenv("CASPARSER_ISIN_TOOLS_NO_CACHE", raising=False)

        def _make_db(dest):
            with closing(_sqlite3.connect(dest)) as c:
                c.execute(
                    "CREATE TABLE isin(isin, issuer_name, description, "
                    "security_type_name, status)"
                )
                c.executemany(
                    "INSERT INTO isin VALUES (?, ?, ?, ?, ?)",
                    [
                        (f"INE{i:06d}", "Iss", "Desc", "EQUITY SHARES", "ACTIVE")
                        for i in range(11000)
                    ],
                )
                c.commit()

        calls = {"n": 0}

        def _fake_stream(session, url, dest):
            calls["n"] += 1
            _make_db(dest)
            import os

            return os.path.getsize(dest)

        monkeypatch.setattr(
            isin, "_find_latest_release_asset", lambda s: ("v1", "http://x/isin.db")
        )
        monkeypatch.setattr(isin, "_stream_download", _fake_stream)
        return isin, calls, get_cache_dir

    def test_miss_then_hit_downloads_once(self, tmp_path, monkeypatch):
        isin, calls, _ = self._setup(tmp_path, monkeypatch)
        assert len(isin.get_isin_data(None)) == 11000
        assert calls["n"] == 1
        # Second call within the TTL is served from disk, no new download.
        assert len(isin.get_isin_data(None)) == 11000
        assert calls["n"] == 1

    def test_expired_cache_redownloads(self, tmp_path, monkeypatch):
        import os
        import time

        isin, calls, get_cache_dir = self._setup(tmp_path, monkeypatch)
        isin.get_isin_data(None)
        assert calls["n"] == 1
        # Age the cached asset past the 12h TTL.
        from cptools.cache import CACHE_TTL_SECONDS

        cached = get_cache_dir() / "captn3m0-isin.db"
        old = time.time() - (CACHE_TTL_SECONDS + 60)
        os.utime(cached, (old, old))
        isin.get_isin_data(None)
        assert calls["n"] == 2

    def test_no_cache_env_bypasses_disk_cache(self, tmp_path, monkeypatch):
        isin, calls, get_cache_dir = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("CASPARSER_ISIN_TOOLS_NO_CACHE", "1")
        isin.get_isin_data(None)
        isin.get_isin_data(None)
        # Every call re-downloads; nothing is persisted to the cache dir.
        assert calls["n"] == 2
        assert list(get_cache_dir().glob("captn3m0-*.db")) == []

    def test_cache_holds_a_single_file(self, tmp_path, monkeypatch):
        isin, calls, get_cache_dir = self._setup(tmp_path, monkeypatch)
        isin.get_isin_data(None)
        names = sorted(p.name for p in get_cache_dir().glob("captn3m0-*"))
        assert names == ["captn3m0-isin.db"]


class TestBuildSession:
    """build_session() picks the file cache, or none when disabled."""

    def test_cached_mode_uses_file_cache_session(self, tmp_path, monkeypatch):
        from cptools.cache import FileCacheSession
        from cptools.http import build_session

        monkeypatch.setenv("CASPARSER_ISIN_TOOLS_CACHE", str(tmp_path))
        monkeypatch.delenv("CASPARSER_ISIN_TOOLS_NO_CACHE", raising=False)
        assert isinstance(build_session(), FileCacheSession)

    def test_disabled_mode_is_plain_session(self, tmp_path, monkeypatch):
        import requests
        from cptools.cache import FileCacheSession
        from cptools.http import build_session

        monkeypatch.setenv("CASPARSER_ISIN_TOOLS_CACHE", str(tmp_path))
        monkeypatch.setenv("CASPARSER_ISIN_TOOLS_NO_CACHE", "1")
        s = build_session()
        assert isinstance(s, requests.Session)
        assert not isinstance(s, FileCacheSession)


class TestFileCacheSession:
    """The shared on-disk HTTP cache (replaces requests-cache)."""

    def _patch_network(self, monkeypatch, body=b"hello"):
        """Stub the underlying transport; return a call counter."""
        import requests

        calls = {"n": 0}

        def _fake(self, method, url, **kwargs):  # noqa: ARG001
            calls["n"] += 1
            r = requests.Response()
            r.status_code = 200
            r._content = body
            r.url = url
            r.encoding = "utf-8"
            return r

        monkeypatch.setattr(requests.Session, "request", _fake)
        return calls

    def test_get_is_cached_and_reused(self, tmp_path, monkeypatch):
        from cptools.cache import FileCacheSession

        calls = self._patch_network(monkeypatch)
        s = FileCacheSession(tmp_path)
        assert s.get("http://x/a").text == "hello"
        assert s.get("http://x/a").text == "hello"
        assert calls["n"] == 1  # second call served from disk

    def test_distinct_urls_cache_separately(self, tmp_path, monkeypatch):
        from cptools.cache import FileCacheSession

        calls = self._patch_network(monkeypatch)
        s = FileCacheSession(tmp_path)
        s.get("http://x/a")
        s.get("http://x/b")
        assert calls["n"] == 2

    def test_post_body_is_part_of_key(self, tmp_path, monkeypatch):
        from cptools.cache import FileCacheSession

        calls = self._patch_network(monkeypatch)
        s = FileCacheSession(tmp_path)
        s.post("http://x", data={"f": "1"})
        s.post("http://x", data={"f": "1"})  # hit
        s.post("http://x", data={"f": "2"})  # miss: different body
        assert calls["n"] == 2

    def test_expired_entry_refetches(self, tmp_path, monkeypatch):
        import os
        import time

        from cptools.cache import CACHE_TTL_SECONDS, FileCacheSession

        calls = self._patch_network(monkeypatch)
        s = FileCacheSession(tmp_path)
        s.get("http://x/a")
        cached = next(tmp_path.glob("http-*.cache"))
        old = time.time() - (CACHE_TTL_SECONDS + 60)
        os.utime(cached, (old, old))
        s.get("http://x/a")
        assert calls["n"] == 2

    def test_stream_bypasses_cache(self, tmp_path, monkeypatch):
        from cptools.cache import FileCacheSession

        calls = self._patch_network(monkeypatch)
        s = FileCacheSession(tmp_path)
        s.get("http://x/a", stream=True)
        s.get("http://x/a", stream=True)
        assert calls["n"] == 2
        assert list(tmp_path.glob("http-*.cache")) == []

    def test_non_200_not_cached(self, tmp_path, monkeypatch):
        import requests
        from cptools.cache import FileCacheSession

        calls = {"n": 0}

        def _fake(self, method, url, **kwargs):  # noqa: ARG001
            calls["n"] += 1
            r = requests.Response()
            r.status_code = 500
            r._content = b"err"
            r.url = url
            return r

        monkeypatch.setattr(requests.Session, "request", _fake)
        s = FileCacheSession(tmp_path)
        s.get("http://x/a")
        s.get("http://x/a")
        assert calls["n"] == 2


class TestParseNSEEquityCsv:
    """The NSE EQUITY_L.csv parser -- ISIN -> (symbol, exchange)."""

    def test_basic_mapping_and_header_space_stripping(self):
        # NSE's real header carries leading spaces on every column after the
        # first; the parser must strip keys before lookup.
        text = (
            "SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE,"
            " MARKET LOT, ISIN NUMBER, FACE VALUE\n"
            "RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995,10,1,INE002A01018,10\n"
            "INFY,Infosys Limited,EQ,08-FEB-1995,5,1,INE009A01021,5\n"
        )
        out = parse_nse_equity_csv(text)
        assert out["INE002A01018"] == ("RELIANCE", "NSE")
        assert out["INE009A01021"] == ("INFY", "NSE")

    def test_skips_rows_without_valid_isin_or_symbol(self):
        text = (
            "SYMBOL, NAME OF COMPANY, SERIES,  ISIN NUMBER\n"
            "GOOD,Good Co,EQ,INE002A01018\n"
            "BADISIN,Bad Isin Co,EQ,NOTANISIN\n"  # 9 chars, not 12
            ",Missing Symbol Co,EQ,INE009A01021\n"  # empty symbol
        )
        out = parse_nse_equity_csv(text)
        assert out == {"INE002A01018": ("GOOD", "NSE")}

    def test_eq_series_wins_over_surveillance_series(self):
        # Same ISIN under EQ and BE (surveillance) -- EQ must win regardless
        # of row order.
        eq_first = (
            "SYMBOL, NAME OF COMPANY, SERIES,  ISIN NUMBER\n"
            "FOO,Foo Ltd,EQ,INE002A01018\n"
            "FOO,Foo Ltd,BE,INE002A01018\n"
        )
        be_first = (
            "SYMBOL, NAME OF COMPANY, SERIES,  ISIN NUMBER\n"
            "FOO,Foo Ltd,BE,INE002A01018\n"
            "FOO,Foo Ltd,EQ,INE002A01018\n"
        )
        assert parse_nse_equity_csv(eq_first)["INE002A01018"] == ("FOO", "NSE")
        assert parse_nse_equity_csv(be_first)["INE002A01018"] == ("FOO", "NSE")

    def test_symbol_and_isin_uppercased(self):
        text = "SYMBOL, NAME OF COMPANY, SERIES,  ISIN NUMBER\n" "foo,Foo Ltd,EQ,ine002a01018\n"
        out = parse_nse_equity_csv(text)
        assert out == {"INE002A01018": ("FOO", "NSE")}
