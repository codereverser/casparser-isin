import csv
from decimal import Decimal
from pathlib import Path

# noinspection PyPackageRequirements
import pytest

from casparser_isin import ISINDb, MFISINDb

BASE_DIR = Path(__file__).resolve().parent
FIXTURES_PATH = BASE_DIR / "fixtures.csv"


class TestISINSearch:
    """Common test cases for all available parsers."""

    def test_isin_search(self):
        with MFISINDb() as db, open(FIXTURES_PATH) as fp:
            reader = csv.reader(fp)
            next(reader)
            for row in reader:
                name, rta, rta_code, _, isin, amfi = row
                scheme_data = db.isin_lookup(name, rta, rta_code)
                assert isin == scheme_data.isin
                assert amfi == (scheme_data.amfi_code or "")

                direct_isin_lookup_result = db.isin_lookup(name, rta, rta_code, isin=isin)
                assert scheme_data.isin == direct_isin_lookup_result.isin
                assert scheme_data.amfi_code == direct_isin_lookup_result.amfi_code

        assert db.connection is None

    def test_bad_isin(self):
        with MFISINDb() as db:
            with pytest.raises(ValueError):
                db.isin_lookup("", "", "")
            with pytest.raises(TypeError):
                # noinspection PyTypeChecker
                db.isin_lookup(None, "", "")
            with pytest.raises(ValueError):
                db.isin_lookup("", "KARVY-OLD", "")
            with pytest.raises(ValueError):
                db.isin_lookup("", "KARVY", "")

    def test_without_ctx(self):
        db = MFISINDb()
        assert db.connection is None

        data = db.isin_lookup("ICICI Prudential MIP - Direct Plan growth", "CAMS", "P8024")
        assert data.isin == "INF109K01U35"
        assert data.type == "DEBT"

        assert db.connection is None

    def test_nav_lookup(self):
        with MFISINDb() as db:
            for isin, actual_nav in (
                ("INF209K01BS7", Decimal("151.06")),
                ("INF090I01635", Decimal("15.7036")),
            ):
                nav = db.nav_lookup(isin)
                assert nav == actual_nav

            nav = db.nav_lookup("invalid_isin")
            assert nav is None

    def test_isin(self):
        with ISINDb() as db:
            for isin in ("INF209K01BS7", "INF090I01635", "INE009A01021"):
                assert db.isin_lookup(isin) is not None

            for isin in ("invalid_isin", "INF090I0163"):
                assert db.isin_lookup(isin) is None

    def test_isin_returns_full_record(self):
        """ISINDb.isin_lookup must populate every ISINData field, not just isin/name."""
        with ISINDb() as db:
            data = db.isin_lookup("INE009A01021")
        assert data is not None
        assert data.isin == "INE009A01021"
        # The remaining fields must be present and non-empty for a real ISIN.
        assert data.name
        assert data.issuer
        assert data.type
        assert data.status


class TestHDFCLookup:
    """The HDFC branch in scheme_lookup has the most special-cased logic.

    Covers: direct vs regular, IDCW payout vs reinvest, and that the LIKE
    patterns bind via parameters (not string interpolation).
    """

    def test_hdfc_regular_growth(self):
        with MFISINDb() as db:
            data = db.isin_lookup(
                "HDFC ARBITRAGE FUND - RETAIL PLAN - GROWTH OPTION", "CAMS", "HAFRG"
            )
        assert data.isin == "INF179K01319"
        assert "direct" not in data.name.lower()

    def test_hdfc_direct_plan_idcw_payout(self):
        with MFISINDb() as db:
            data = db.isin_lookup(
                "HDFC ARBITRAGE FUND - RETAIL PLAN - DIRECT PLAN - QUARTERLY IDCW PAYOUT",
                "CAMS",
                "HAFDQT",
            )
        assert data.isin == "INF179K01UV6"
        assert "direct" in data.name.lower()
        assert "payout" in data.name.lower()

    def test_hdfc_direct_plan_idcw_reinvest(self):
        with MFISINDb() as db:
            data = db.isin_lookup(
                "HDFC ARBITRAGE FUND - RETAIL PLAN - DIRECT PLAN - "
                "QUARTERLY IDCW REINVESTMENT OPTION",
                "CAMS",
                "HAFDRT",
            )
        assert data.isin == "INF179K01UW4"
        assert "direct" in data.name.lower()
        assert "reinvest" in data.name.lower()


class TestFranklinLookup:
    """Covers the ``fti\\d+`` early-return path in scheme_lookup."""

    def test_franklin_via_cams_rta_with_fti_code(self):
        # Old CAS files sometimes report Franklin schemes with rta="CAMS" but
        # an FTI rta_code. scheme_lookup must detect this and short-circuit
        # to the FRANKLIN rta.
        with MFISINDb() as db:
            data = db.isin_lookup("Franklin India Prima Fund - IDCW - Payout", "CAMS", "FTI001")
        assert data.isin == "INF090I01726"

    def test_franklin_native_rta(self):
        with MFISINDb() as db:
            data = db.isin_lookup("Franklin India Prima Fund - IDCW - Payout", "FRANKLIN", "FTI001")
        assert data.isin == "INF090I01726"


class TestDirectIsinLookup:
    """Behaviour when an ISIN is supplied directly."""

    def test_direct_isin_lookup_multi_row_picks_via_fuzzy_match(self):
        # INF044D01583 has two scheme-table rows with very similar names; the
        # fuzzy matcher must pick the closest to the supplied scheme_name.
        with MFISINDb() as db:
            data = db.isin_lookup(
                "TAURUS SHORT TERM INCOME FUND REGULAR PLAN IDCW PAYOUT",
                "KARVY",
                "104LBDP",
                isin="INF044D01583",
            )
        assert data.isin == "INF044D01583"
        # Match should be regular-plan variant, not the unqualified one.
        assert "regular" in data.name.lower()

    def test_direct_isin_lookup_falls_back_to_scheme_lookup_on_miss(self):
        # When direct ISIN search returns nothing (bogus ISIN), the function
        # must fall back to scheme_lookup using rta/rta_code/name.
        with MFISINDb() as db:
            data = db.isin_lookup(
                "HDFC ARBITRAGE FUND - RETAIL PLAN - GROWTH OPTION",
                "CAMS",
                "HAFRG",
                isin="ZZZZZZZZZZZZ",
            )
        assert data.isin == "INF179K01319"
