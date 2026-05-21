from typing import NamedTuple

from .utils import DB


class ISINData(NamedTuple):
    isin: str
    name: str
    issuer: str
    type: str
    status: str


class ISINDb(DB):
    """ISIN database for all instruments."""

    def isin_lookup(self, isin: str) -> ISINData | None:
        """
        Lookup ISIN details from the database.

        :param isin: ISIN code
        :return: ISINData if found, else None.
        """
        sql = "SELECT isin, name, issuer, type, status FROM isin WHERE isin = :isin"
        row = self.run_query(sql, {"isin": isin}, fetchone=True)
        if row is not None:
            return ISINData(**row)
        return None
