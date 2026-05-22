import socket
from http.client import HTTPResponse
from urllib import error, request

from casparser_isin import cli


class MockResponse(HTTPResponse):
    """Fake HTTPResponse for unit tests.

    Mimics a real response closely enough that streaming code paths
    (``while True: chunk = resp.read(N)``) terminate naturally: ``read``
    honours the ``amt`` argument and returns ``b""`` after the payload is
    fully drained.
    """

    def __init__(
        self,
        mock_request: request.Request,
        remote_db_version="2000.01.01",
        remote_dbformat="2",
        fail_on_urls=None,
        db_payload=b"mock_data",
    ):
        self.__mock_request = mock_request
        self.__remote_db_version = remote_db_version
        self.__remote_dbformat = remote_dbformat
        if not isinstance(fail_on_urls, list):
            fail_on_urls = [fail_on_urls]
        self.__mock_fail_on_urls = fail_on_urls
        self.__db_payload = db_payload
        self.__buffer = None  # Lazily populated on first read.
        sock = socket.socket()
        super().__init__(sock)

    def _payload(self) -> bytes:
        if self.__mock_request.full_url == cli.META_URL:
            return (
                f"version={self.__remote_db_version}\ndbformat={self.__remote_dbformat}\n"
            ).encode()
        if self.__mock_request.full_url == cli.DB_URL:
            return self.__db_payload
        return b""

    def read(self, amt=None):
        if self.__mock_request.full_url in self.__mock_fail_on_urls:
            raise error.HTTPError(self.__mock_request.full_url, 400, "Mock HTTP Error", {}, fp=None)
        if self.__buffer is None:
            self.__buffer = self._payload()
        if amt is None:
            data = self.__buffer
            self.__buffer = b""
            return data
        data = self.__buffer[:amt]
        self.__buffer = self.__buffer[amt:]
        return data
