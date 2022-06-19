import csv
from decimal import Decimal
from pathlib import Path

# noinspection PyPackageRequirements
import pytest

from casparser_isin import MFISINDb

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
