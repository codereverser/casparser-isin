from collections import namedtuple
from decimal import Decimal
import re
import sqlite3
from typing import Optional

from rapidfuzz import process

from .utils import get_isin_db_path

RTA_MAP = {
    "CAMS": "CAMS",
    "FTAMIL": "FRANKLIN",
    "FRANKLIN": "FRANKLIN",
    "KFINTECH": "KARVY",
    "KARVY": "KARVY",
}

SchemeData = namedtuple("SchemeData", "name isin amfi_code type score")


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


class MFISINDb:
    """ISIN database for (Indian) Mutual Funds."""

    connection = None
    cursor = None

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def initialize(self):
        """Initialize database."""
        self.connection = sqlite3.connect(get_isin_db_path())
        self.connection.row_factory = dict_factory
        self.cursor = self.connection.cursor()

    def close(self):
        """Close database connection."""
        if self.cursor is not None:
            self.cursor.close()
            self.cursor = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def run_query(self, sql, arguments, fetchone=False):
        self_initialized = False
        if self.connection is None:
            self.initialize()
            self_initialized = True
        try:
            self.cursor.execute(sql, arguments)
            if fetchone:
                return self.cursor.fetchone()
            return self.cursor.fetchall()
        finally:
            if self_initialized:
                self.close()

    def scheme_lookup(self, rta: str, scheme_name: str, rta_code: str):
        """
        Lookup scheme details from the database
        :param rta: RTA (CAMS, KARVY, FTAMIL)
        :param scheme_name: scheme name
        :param rta_code: RTA code for the scheme
        :return:
        """
        if rta_code is not None:
            rta_code = re.sub(r"\s+", "", rta_code)

        sql = """SELECT name, isin, amfi_code, type from scheme"""
        where = ["rta = :rta"]
        args = {"rta": RTA_MAP.get(str(rta).upper(), "")}

        if re.search("re-*invest", scheme_name, re.I):
            where.append("name LIKE '%reinvest%'")
        else:
            where.append("name NOT LIKE '%reinvest%'")

        if match := re.search(r"fti(\d+)", rta_code, re.I):
            amc_code = match.group(1)
            where.append("amc_code = :amc_code")
            args.update(amc_code=amc_code)
        else:
            where.append("rta_code = :rta_code")
            args.update(rta_code=rta_code)

        sql_statement = "{} WHERE {}".format(sql, " AND ".join(where))
        results = self.run_query(sql_statement, args)

        if len(results) == 0 and "rta_code" in args:
            args["rta_code"] = args["rta_code"][:-1]
            results = self.run_query(sql_statement, args)
        return results

    def isin_lookup(
        self, scheme_name: str, rta: str, rta_code: str, min_score: int = 75
    ) -> SchemeData:
        """
        Return the closest matching scheme from MF isin database.

        :param scheme_name: Scheme Name
        :param rta: RTA (CAMS, KARVY, KFINTECH)
        :param rta_code: Scheme RTA code
        :param min_score: Minimum score (out of 100) required from the fuzzy match algorithm

        :return: isin and amfi_code code for matching scheme.
        :rtype: SchemeData
        :raises: ValueError if no scheme is found in the database.
        """

        if not (
            isinstance(scheme_name, str) and isinstance(rta, str) and isinstance(rta_code, str)
        ):
            raise TypeError("Invalid input")
        if rta.upper() not in RTA_MAP:
            raise ValueError(f"Invalid RTA : {rta}")
        results = self.scheme_lookup(rta, scheme_name, rta_code)
        if len(results) > 0:
            schemes = {
                x["name"]: (x["name"], x["isin"], x["amfi_code"], x["type"]) for x in results
            }
            key, score, _ = process.extractOne(scheme_name, schemes.keys())
            if score >= min_score or len(results) == 1:
                name, isin, amfi_code, scheme_type = schemes[key]
                return SchemeData(
                    name=name, isin=isin, amfi_code=amfi_code, type=scheme_type, score=score
                )
        raise ValueError("No schemes found")

    def nav_lookup(self, isin: str) -> Optional[Decimal]:
        """
        Return the NAV of the fund on 31st Jan 2018. used for LTCG computations
        :param isin: Fund ISIN
        :return: nav value as a Decimal if available, else return None
        """
        sql = """SELECT nav FROM nav20180131 where isin = :isin"""
        result = self.run_query(sql, {"isin": isin}, fetchone=True)
        if result is not None:
            return Decimal(result["nav"])
