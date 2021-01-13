import csv
from pathlib import Path

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
                assert amfi == scheme_data.amfi_code
                assert scheme_data.score >= 75

        assert db.connection is None

    def test_bad_isin(self):
        with MFISINDb() as db:
            with pytest.raises(ValueError):
                db.isin_lookup("", "", "")
            with pytest.raises(TypeError):
                db.isin_lookup(None, "", "")
            with pytest.raises(ValueError):
                db.isin_lookup("", "KARVY-OLD", "")
            with pytest.raises(ValueError):
                db.isin_lookup("", "KARVY", "")

    def test_without_ctx(self):
        db = MFISINDb()
        assert db.connection is None
        with pytest.raises(ValueError):
            db.isin_lookup("sbi magnum midcap", "KFINTECH", "128TSGPG")  # score < min_score

        assert db.connection is None
