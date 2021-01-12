import csv
from pathlib import Path
import sqlite3

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
                _, isin_match, amfi_match = db.get_scheme_isin(name, rta, rta_code)
                assert isin == isin_match
                assert amfi == amfi_match

        # Check if db is closed after exit
        with pytest.raises(sqlite3.ProgrammingError):
            db.connection.cursor()

    def test_without_ctx(self):
        db = MFISINDb()
        _, isin, amfi = db.get_scheme_isin(
            "Axis Long Term Equity Fund - Regular Growth", "KFINTECH", "128TSGPG"
        )
        assert isin == "INF846K01131"
        assert amfi == "112323"

        # Check if db is closed automatically.
        with pytest.raises(sqlite3.ProgrammingError):
            db.connection.cursor()