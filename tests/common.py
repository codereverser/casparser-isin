from http.client import HTTPResponse
import socket
from urllib import error, request

from casparser_isin import cli


class MockResponse(HTTPResponse):
    def __init__(
        self, mock_request: request.Request, remote_db_version="2000.01.01", fail_on_urls=None
    ):
        self.__mock_request = mock_request
        self.__remote_db_version = remote_db_version
        if not isinstance(fail_on_urls, list):
            fail_on_urls = [fail_on_urls]
        self.__mock_fail_on_urls = fail_on_urls
        sock = socket.socket()
        super().__init__(sock)

    def read(self, amt=None):
        if self.__mock_request.full_url in self.__mock_fail_on_urls:
            raise error.HTTPError(self.__mock_request.full_url, 400, "Mock HTTP Error", {}, fp=None)
        elif self.__mock_request.full_url == cli.META_URL:
            return f"version={self.__remote_db_version}\ndbformat=1".encode()
        elif self.__mock_request.full_url == cli.DB_URL:
            return b"mock_data"
