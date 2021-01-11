import pathlib
import re
import sqlite3

from rapidfuzz import process


BASE_DIR = pathlib.Path(__file__).resolve().parent
ISIN_DB_PATH = BASE_DIR / 'isin.db'
RTA_MAP = {"CAMS": "CAMS", "FTAMIL": "FRANKLIN", "FRANKLIN": "FRANKLIN", "KFINTECH": "KARVY", "KARVY": "KARVY"}


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


class MFISINDb:
    connection = None
    cursor = None
    table = 'scheme'

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def initialize(self):
        self.connection = sqlite3.connect(ISIN_DB_PATH)
        self.connection.row_factory = dict_factory
        self.cursor = self.connection.cursor()

    def close(self):
        if self.cursor is not None:
            self.cursor.close()
        if self.connection is not None:
            self.connection.close()

    def scheme_lookup(self, rta, scheme_name, rta_code=None, amc_code=None):
        self_initialized = False
        if self.connection is None:
            self.initialize()
            self_initialized = True
        try:

            sql = """SELECT name, isin, amfi_code from scheme WHERE rta = ?"""
            args = [RTA_MAP[rta.upper()], ]

            if rta_code is None and amc_code is None:
                raise ValueError("Either of rta_code or amc_code should be provided.")
            if rta_code is not None:
                rta_code = re.sub(r"\s+", "", rta_code)

            if amc_code is not None:
                sql += """ AND amc_code = ?"""
                args.append(amc_code)
            else:
                sql += """ AND rta_code = ?"""
                args.append(rta_code)

            if "reinvest" in scheme_name:
                sql += """ AND name LIKE '%reinvest%' """
            else:
                sql += """ AND name NOT LIKE '%reinvest%' """
            self.cursor.execute(sql, tuple(args))
            results = self.cursor.fetchall()
            if len(results) == 0 and rta_code is not None:
                args[1] = rta_code[:-1]
                self.cursor.execute(sql, tuple(args))
                results = self.cursor.fetchall()
            return results
        finally:
            if self_initialized:
                self.close()

    def get_scheme_isin(self, scheme_name, rta, rta_code):
        amc_code = None
        if match := re.search(r"fti(\d+)", rta_code, re.I):
            amc_code = match.group(1)
        results = self.scheme_lookup(rta, scheme_name, rta_code=rta_code, amc_code=amc_code)
        if len(results) == 0:
            raise ValueError("No schemes found")
        schemes = {x['name']: (x['isin'], x['amfi_code']) for x in results}
        key, _ = process.extractOne(scheme_name, schemes.keys())
        return schemes[key]