import functools
import logging
from urllib import request

import pytest

from casparser_isin import cli

from .common import MockResponse


class TestCLI:
    """Common test cases for all available parsers."""

    def test_version(self, capsys, version_cli):
        metadata = cli.get_metadata()
        expected_output = (
            f"cli-version : {metadata['cli-version']}\n"
            f"db-version  : {metadata['version']}\n"
            f"db-format   : {metadata['dbformat']}\n"
        )

        cli.main()
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == expected_output

    def test_help(self, capsys, help_cli):
        cli.main()

        captured = capsys.readouterr()
        assert captured.err == ""
        assert isinstance(captured.out, str) and captured.out.startswith("usage: casparser-isin")

    def test_check(self, caplog, check_cli, monkeypatch):
        def mock_urlopen(request_obj, db_version="3100.01.01", fail_on_urls=None):
            return MockResponse(
                request_obj, remote_db_version=db_version, fail_on_urls=fail_on_urls
            )

        caplog.set_level(logging.INFO)
        with monkeypatch.context() as m:
            m.setattr(request, "urlopen", mock_urlopen)
            with pytest.raises(SystemExit) as exc:
                cli.main()
            assert exc.type is SystemExit
            assert exc.value.code == 1
        assert "To update the database" in caplog.records[-1].message

        with monkeypatch.context() as m:
            m.setattr(request, "urlopen", functools.partial(mock_urlopen, db_version="1990.01.01"))
            with pytest.raises(SystemExit) as exc:
                cli.main()
            assert exc.type is SystemExit
            assert exc.value.code == 0

    def test_meta_fail(self, monkeypatch, update_cli, caplog, isolated_isin_db):
        def mock_urlopen(request_obj, db_version="3099.01.01", fail_on_urls=None):
            return MockResponse(
                request_obj, remote_db_version=db_version, fail_on_urls=fail_on_urls
            )

        caplog.set_level(logging.INFO)

        with monkeypatch.context() as m:
            m.setattr(
                request, "urlopen", functools.partial(mock_urlopen, fail_on_urls=cli.META_URL)
            )
            cli.main()
        expected_log_msgs = [
            "Fetching remote isin db metadata",
            "Received error from remote server :: Mock HTTP Error",
        ]
        assert [x.message for x in caplog.records] == expected_log_msgs

        with monkeypatch.context() as m:
            m.setattr(request, "urlopen", functools.partial(mock_urlopen, fail_on_urls=cli.DB_URL))
            cli.main()
            caplog_records = [x.message for x in caplog.records][-2:]
            assert caplog_records == [
                "Fetching database version :: 3099.1.1",
                "Error fetching isin database :: Mock HTTP Error",
            ]

    def test_update_success(self, monkeypatch, update_cli, caplog, isolated_isin_db):
        def mock_urlopen(request_obj, db_version="3099.01.01", fail_on_urls=None):
            return MockResponse(
                request_obj, remote_db_version=db_version, fail_on_urls=fail_on_urls
            )

        caplog.set_level(logging.INFO)

        with monkeypatch.context() as m:
            m.setattr(request, "urlopen", functools.partial(mock_urlopen, db_version="1990.01.01"))
            cli.main()
        assert caplog.records[-1].message == "casparser-isin database is already upto date"

        with monkeypatch.context() as m:
            m.setattr(request, "urlopen", mock_urlopen)
            cli.main()
        assert caplog.records[-1].message == "Updated casparser-isin database."
        # Atomic swap actually replaced the file with the mocked payload.
        assert isolated_isin_db.read_bytes() == b"mock_data"
        # And no stray *.tmp files were left behind.
        assert not list(isolated_isin_db.parent.glob("isin.db.*.tmp"))

    def test_dbformat_mismatch_skips_update(
        self, monkeypatch, update_cli, caplog, isolated_isin_db
    ):
        """Remote DB with a different ``dbformat`` must not be downloaded.

        Critical safety property: if we ever bump dbformat and a stale CLI
        sees the new meta, it must refuse the update rather than swap in an
        incompatible schema.
        """
        original_payload = isolated_isin_db.read_bytes()

        def mock_urlopen(request_obj):
            # Newer version but a different dbformat -> incompatible.
            return MockResponse(request_obj, remote_db_version="3099.01.01", remote_dbformat="9999")

        caplog.set_level(logging.INFO)
        with monkeypatch.context() as m:
            m.setattr(request, "urlopen", mock_urlopen)
            cli.main()

        assert caplog.records[-1].message == "casparser-isin database is already upto date"
        # The bundled DB must be untouched.
        assert isolated_isin_db.read_bytes() == original_payload

    def test_dbformat_mismatch_check_returns_zero(
        self, monkeypatch, check_cli, caplog, isolated_isin_db
    ):
        """``--check`` with a remote dbformat mismatch must exit 0, not 1.

        Otherwise CI scripts that key off the exit code would prompt users to
        update to an incompatible DB.
        """

        def mock_urlopen(request_obj):
            return MockResponse(request_obj, remote_db_version="3099.01.01", remote_dbformat="9999")

        caplog.set_level(logging.INFO)
        with monkeypatch.context() as m:
            m.setattr(request, "urlopen", mock_urlopen)
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 0
        assert "Local database is up to date." in caplog.records[-1].message

    def test_update_atomic_on_download_failure(
        self, monkeypatch, update_cli, caplog, isolated_isin_db
    ):
        """If the DB download fails after meta succeeded, the local DB must be untouched."""
        original_payload = isolated_isin_db.read_bytes()

        def mock_urlopen(request_obj):
            return MockResponse(
                request_obj, remote_db_version="3099.01.01", fail_on_urls=cli.DB_URL
            )

        caplog.set_level(logging.INFO)
        with monkeypatch.context() as m:
            m.setattr(request, "urlopen", mock_urlopen)
            cli.main()

        # Local DB preserved, no stray temp file.
        assert isolated_isin_db.read_bytes() == original_payload
        assert not list(isolated_isin_db.parent.glob("isin.db.*.tmp"))
