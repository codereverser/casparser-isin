import logging

from casparser_isin.utils import INTERNAL_ISIN_DB_PATH, get_isin_db_path


class TestUtils:
    """Test util functions"""

    def test_env(self, monkeypatch, tmp_path):
        test_isin_db = "isin_test.db"

        tmp_isin_path = tmp_path / test_isin_db
        tmp_isin_path.write_text("test")

        monkeypatch.setenv("CASPARSER_ISIN_DB", str(tmp_isin_path))
        out = get_isin_db_path()
        assert out.name == test_isin_db

    def test_bad_env_missing_file_warns(self, monkeypatch, tmp_path, caplog):
        """A non-existent CASPARSER_ISIN_DB should log a warning, not silently fall back."""
        test_isin_db = "isin_test.db"

        tmp_isin_path = tmp_path / test_isin_db
        monkeypatch.setenv("CASPARSER_ISIN_DB", str(tmp_isin_path))

        with caplog.at_level(logging.WARNING, logger="casparser_isin.utils"):
            out = get_isin_db_path()
        assert out == INTERNAL_ISIN_DB_PATH
        assert any(
            "CASPARSER_ISIN_DB" in record.message and str(tmp_isin_path) in record.message
            for record in caplog.records
        )

    def test_bad_env_directory_falls_back(self, monkeypatch, tmp_path, caplog):
        """A directory at CASPARSER_ISIN_DB should not be treated as a DB."""
        monkeypatch.setenv("CASPARSER_ISIN_DB", str(tmp_path))
        with caplog.at_level(logging.WARNING, logger="casparser_isin.utils"):
            out = get_isin_db_path()
        assert out == INTERNAL_ISIN_DB_PATH
        assert caplog.records, "Expected a warning when env var points at a directory"

    def test_unset_env_returns_bundled(self, monkeypatch):
        monkeypatch.delenv("CASPARSER_ISIN_DB", raising=False)
        assert get_isin_db_path() == INTERNAL_ISIN_DB_PATH
