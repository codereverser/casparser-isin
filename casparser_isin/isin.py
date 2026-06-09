import sqlite3
from typing import NamedTuple

from .utils import DB


class ISINData(NamedTuple):
    isin: str
    name: str
    issuer: str
    type: str
    status: str
    # Exchange trading symbol for listed equities (e.g. ``RELIANCE`` / ``NSE``),
    # joined in from the NSE equity list at build time. ``None`` for instruments
    # with no listed symbol, and for any row read from a pre-symbol database
    # (the lookup falls back to the legacy column set in that case).
    symbol: str | None = None
    exchange: str | None = None


class ISINDb(DB):
    """ISIN database for all instruments."""

    _SELECT_COLS = "isin, name, issuer, type, status, symbol, exchange"
    _SELECT_COLS_LEGACY = "isin, name, issuer, type, status"

    def _query_one(self, isin: str) -> dict | None:
        """Fetch one isin row, tolerating a database built before the
        ``symbol`` / ``exchange`` columns existed."""
        try:
            return self.run_query(
                f"SELECT {self._SELECT_COLS} FROM isin WHERE isin = :isin",
                {"isin": isin},
                fetchone=True,
            )
        except sqlite3.OperationalError:
            return self.run_query(
                f"SELECT {self._SELECT_COLS_LEGACY} FROM isin WHERE isin = :isin",
                {"isin": isin},
                fetchone=True,
            )

    def isin_lookup(self, isin: str) -> ISINData | None:
        """
        Lookup ISIN details from the database.

        :param isin: ISIN code
        :return: ISINData if found, else None.
        """
        row = self._query_one(isin)
        if row is not None:
            return ISINData(**row)
        return None

    def batch_isin_lookup(self, isins) -> dict[str, ISINData]:
        """Resolve many ISINs in a single DB session.

        Mirrors :meth:`MFISINDb.direct_isin_lookup`'s batching intent: open one
        connection for the whole set rather than reconnecting per ISIN. Used to
        enrich demat (NSDL/CDSL) equity holdings -- identified only by ISIN in
        the statement -- with their exchange trading symbol.

        Falsy / duplicate ISINs are ignored. Unresolvable ISINs are simply
        absent from the returned mapping.
        """
        result: dict[str, ISINData] = {}
        unique = {isin for isin in isins if isin}
        if not unique:
            return result
        opened = self.connection is None
        if opened:
            self.initialize()
        try:
            for isin in unique:
                row = self._query_one(isin)
                if row is not None:
                    result[isin] = ISINData(**row)
        finally:
            if opened:
                self.close()
        return result
