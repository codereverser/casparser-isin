import functools
import logging
from urllib import request

from casparser_isin import cli

from .common import MockResponse, mockopen, update_cli, version_cli, help_cli


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

    def test_meta_fail(self, monkeypatch, update_cli, caplog, mockopen):
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

    def test_update_success(self, monkeypatch, update_cli, caplog, mockopen):
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
