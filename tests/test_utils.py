from casparser_isin.utils import get_isin_db_path


class TestUtils:
    """Test util functions"""

    def test_env(self, monkeypatch, tmp_path):
        test_isin_db = "isin_test.db"

        tmp_isin_path = tmp_path / test_isin_db
        tmp_isin_path.write_text("test")

        monkeypatch.setenv("CASPARSER_ISIN_DB", str(tmp_isin_path))
        out = get_isin_db_path()
        assert out.name == test_isin_db

    def test_bad_env(self, monkeypatch, tmp_path):
        test_isin_db = "isin_test.db"

        tmp_isin_path = tmp_path / test_isin_db
        monkeypatch.setenv("CASPARSER_ISIN_DB", str(tmp_isin_path))

        out = get_isin_db_path()
        assert out.name == "isin.db"
