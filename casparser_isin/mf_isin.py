import re
from decimal import Decimal
from typing import NamedTuple

from rapidfuzz import fuzz, process, utils

from .utils import DB

RTA_MAP = {
    "CAMS": "CAMS",
    "FTAMIL": "FRANKLIN",
    "FRANKLIN": "FRANKLIN",
    "KFINTECH": "KARVY",
    "KARVY": "KARVY",
}

# Scheme types use a token_sort_ratio match because RTA-emitted scheme names
# regularly reorder modifiers ("Direct Growth" vs "Growth - Direct Plan").
_FUZZ_SCORER = fuzz.token_sort_ratio


class SchemeData(NamedTuple):
    name: str
    isin: str
    amfi_code: str
    type: str
    score: int


class MFISINDb(DB):
    """ISIN database for (Indian) Mutual Funds."""

    def direct_isin_lookup(self, isin: str):
        """
        Lookup scheme data via ISIN code.

        :param isin: Fund ISIN
        :return: list of scheme rows matching the ISIN (may be empty).
        """
        sql = "SELECT name, isin, amfi_code, type FROM scheme WHERE isin = :isin ORDER BY id DESC"
        return self.run_query(sql, {"isin": isin})

    def scheme_lookup(self, rta: str, scheme_name: str, rta_code: str):
        """
        Lookup scheme details from the database.

        :param rta: RTA (CAMS, KARVY, FTAMIL)
        :param scheme_name: scheme name
        :param rta_code: RTA code for the scheme (must be a string; callers
            should reject None upstream)
        :return: list of scheme rows matching the query (may be empty).
        """
        rta_code = re.sub(r"\s+", "", rta_code)

        sql = "SELECT name, isin, amfi_code, type FROM scheme"
        where = ["rta = :rta"]

        if re.search(r"fti(\d+)", rta_code, re.I) and rta.upper() in ("CAMS", "FRANKLIN", "FTAMIL"):
            # Try searching db for Franklin schemes
            franklin_args = {"rta": "FRANKLIN", "rta_code": rta_code}
            franklin_sql = f"{sql} WHERE rta = :rta AND rta_code = :rta_code"
            results = self.run_query(franklin_sql, franklin_args)
            if len(results) != 0:
                return results

        args = {"rta": RTA_MAP.get(rta.upper(), ""), "rta_code": rta_code}

        if "hdfc" in scheme_name.lower():
            # HDFC special-casing: RTA codes for HDFC are prefixes (suffix
            # encodes plan/option), so use LIKE with a bound parameter rather
            # than `=`. Plan/option filters are applied via additional LIKE
            # patterns — all bound, never string-interpolated.
            if re.search("direct", scheme_name, re.I):
                where.append("name LIKE :direct_pattern")
                args["direct_pattern"] = "%direct%"
            else:
                where.append("name NOT LIKE :direct_pattern")
                args["direct_pattern"] = "%direct%"

            if re.search("dividend|idcw", scheme_name, re.I):
                where.append("name LIKE :payout_pattern")
                if re.search("re-*invest", scheme_name, re.I):
                    args["payout_pattern"] = "%reinvest%"
                else:
                    args["payout_pattern"] = "%payout%"
            where.append("rta_code LIKE :rta_code_d")
            args["rta_code_d"] = f"{rta_code}%"
        else:
            where.append("rta_code = :rta_code")

        sql_statement = f"{sql} WHERE {' AND '.join(where)} ORDER BY id DESC"
        results = self.run_query(sql_statement, args)
        if len(results) == 0 and "rta_code" in args and args["rta_code"]:
            # Retry once after trimming the last character of the RTA code
            # (covers off-by-one suffixes seen in older CAMS statements).
            args["rta_code"] = args["rta_code"][:-1]
            results = self.run_query(sql_statement, args)
        return results

    def isin_lookup(
        self,
        scheme_name: str,
        rta: str,
        rta_code: str,
        isin: str | None = None,
        min_score: int = 60,
    ) -> SchemeData:
        """
        Return the closest matching scheme from MF isin database.

        :param scheme_name: Scheme Name
        :param rta: RTA (CAMS, KARVY, KFINTECH)
        :param rta_code: Scheme RTA code
        :param isin: Fund ISIN
        :param min_score: Minimum score (out of 100) required from the fuzzy match algorithm

        :return: isin and amfi_code code for matching scheme.
        :rtype: SchemeData
        :raises TypeError: if any of scheme_name/rta/rta_code is not a string.
        :raises ValueError: if rta is unknown or no scheme is found in the database.
        """

        if not (
            isinstance(scheme_name, str) and isinstance(rta, str) and isinstance(rta_code, str)
        ):
            raise TypeError("Invalid input")
        if rta.upper() not in RTA_MAP:
            raise ValueError(f"Invalid RTA : {rta}")
        results = []
        if isin is not None:
            results = self.direct_isin_lookup(isin)
        if len(results) == 0:
            results = self.scheme_lookup(rta, scheme_name, rta_code)
        if len(results) == 1:
            result = results[0]
            return SchemeData(
                name=result["name"],
                isin=result["isin"],
                amfi_code=result["amfi_code"],
                type=result["type"],
                score=100,
            )
        elif len(results) > 1:
            schemes = {
                x["name"]: (x["name"], x["isin"], x["amfi_code"], x["type"]) for x in results
            }
            key, score, _ = process.extractOne(
                scheme_name,
                schemes.keys(),
                processor=utils.default_process,
                scorer=_FUZZ_SCORER,
            )
            if score >= min_score:
                name, isin, amfi_code, scheme_type = schemes[key]
                return SchemeData(
                    name=name, isin=isin, amfi_code=amfi_code, type=scheme_type, score=score
                )
        raise ValueError("No schemes found")

    def nav_lookup(self, isin: str) -> Decimal | None:
        """
        Return the NAV of the fund on 31st Jan 2018. used for LTCG computations
        :param isin: Fund ISIN
        :return: nav value as a Decimal if available, else return None
        """
        sql = """SELECT nav FROM nav20180131 where isin = :isin"""
        result = self.run_query(sql, {"isin": isin}, fetchone=True)
        if result is not None:
            return Decimal(result["nav"])
