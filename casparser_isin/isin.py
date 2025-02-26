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

    def isin_lookup(self, isin: str):
        """
        Lookup ISIN details from the database
        :param isin: ISIN code
        :return:
        """
        sql = """SELECT * from isin WHERE isin = :isin"""
        row = self.run_query(sql, {"isin": isin}, fetchone=True)
        if row is not None:
            return ISINData(**row)
        return None
