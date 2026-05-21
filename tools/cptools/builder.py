"""Pure functions that transform fetched data into ``scheme`` table rows.

Splitting this out of the orchestrator makes the join logic testable without
hitting any network. Each function takes data in and returns data out --
side effects (DB writes, HTTP) live in :mod:`cptools.update_isin_db`.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .settings import logger

# AMC codes that ship without a Channel Partner Code in the BSE feed. The
# mapping reconstructs the rta_code that downstream consumers expect.
AMC_MAP = {
    "NipponIndiaMutualFund_MF": "RMF",
    "SUNDARAMMUTUALFUND_MF": "176",
    "SAHARAMUTUALFUND_MF": "113",
}

# BSE "Scheme Type" -> simplified category used by casparser tax logic.
# Returning None from this lookup logs a warning rather than crashing the
# whole daily run (the original code raised KeyError, which would silently
# break cron the day BSE introduced a new category).
MAIN_CATEGORY_MAP = {
    "balanced": "EQUITY",
    "bond": "DEBT",
    "debt": "DEBT",
    "elss": "EQUITY",
    "equity": "EQUITY",
    "fof": "DEBT",
    "gilt": "DEBT",
    "hybrid": "EQUITY",
    "hybrid (c)": "DEBT",
    "hybrid (nc)": "EQUITY",
    "income": "DEBT",
    "liquid": "DEBT",
    "mip": "DEBT",
    "stp": "DEBT",
    "overnight": "DEBT",
}

# Synthetic primary-key namespace for Franklin rows. BSE's "Unique No" caps
# well below this (currently ~600k), so 9_900_000+ won't collide. If BSE ever
# crosses this we'll re-key to a string-typed PK.
_FRANKLIN_ROW_START = 9_900_000


@dataclass(frozen=True, slots=True)
class SchemeRow:
    """One row destined for the ``scheme`` table."""

    id: int
    name: str
    isin: str
    amfi_code: str | None
    type: str
    rta: str
    rta_code: str
    amc_code: str

    def as_tuple(self) -> tuple:
        return (
            self.id,
            self.name,
            self.isin,
            self.amfi_code,
            self.type,
            self.rta,
            self.rta_code,
            self.amc_code,
        )

    def dedupe_key(self) -> tuple:
        return (self.name, self.isin, self.amfi_code, self.rta, self.rta_code, self.amc_code)


def read_baseline(db_path: Path) -> tuple[dict[int, SchemeRow], dict[str, str]]:
    """Read the existing ``scheme`` table to drive carry-forward.

    Returns ``(rows_by_id, amfi_map)``. ``amfi_map`` is ``isin -> amfi_code``
    for rows where ``amfi_code IS NOT NULL`` -- used as a fallback when the
    live AMFI feed has dropped an entry but the scheme still trades.

    A missing baseline file is not fatal: returns empty mappings. This is
    what makes the pipeline safe to run from a clean working tree (e.g. a
    stateless CI container).
    """
    if not db_path.exists():
        logger.warning("No baseline DB at %s; starting from empty state", db_path)
        return {}, {}

    rows: dict[int, SchemeRow] = {}
    amfi_map: dict[str, str] = {}
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT id, name, isin, amfi_code, type, rta, rta_code, amc_code FROM scheme"
        )
        for row in cur:
            scheme = SchemeRow(*row)
            rows[scheme.id] = scheme
            if scheme.amfi_code is not None:
                amfi_map[scheme.isin] = scheme.amfi_code

    logger.info("Baseline: %d scheme rows, %d isin->amfi_code mappings", len(rows), len(amfi_map))
    return rows, amfi_map


def build_rows_from_bse(
    bse_csvs: list[str],
    amfi_mapping: dict[str, tuple[str, bool | None]],
    fallback_amfi_map: dict[str, str],
) -> tuple[dict[int, SchemeRow], int, int]:
    """Convert BSE master CSVs into :class:`SchemeRow` instances.

    Returns ``(rows_by_id, total_seen, skipped)``. ``skipped`` includes
    closed-end schemes (``-I`` / ``-L\\d`` suffix), unknown scheme types,
    and AMFI reinvest/payout mismatches.
    """
    rows: dict[int, SchemeRow] = {}
    total = 0
    skipped = 0

    for csv_text in bse_csvs:
        with io.StringIO(csv_text) as fp:
            reader = csv.DictReader(fp, delimiter="|")
            csv_rows = list(reader)

        # Pre-compute "does this ISIN appear with both payout AND reinvest
        # variants?" so we can detect the AMFI mismatch case below.
        dups: dict[str, set[bool]] = defaultdict(set)
        for row in csv_rows:
            isin = row["ISIN"].strip()
            dups[isin].add(re.search("reinvest", row["Scheme Name"], re.I) is not None)

        for row in csv_rows:
            total += 1

            # Closed-end schemes -- last char is "I" or "L\d".
            if re.search(r"-(?:I|L\d)$", row["Scheme Code"], re.I):
                skipped += 1
                continue

            scheme_name = row["Scheme Name"].strip()
            scheme_type = row["Scheme Type"].lower().strip()
            category = MAIN_CATEGORY_MAP.get(scheme_type)
            if category is None:
                logger.warning(
                    "Unknown BSE scheme_type %r (scheme=%r); skipping",
                    scheme_type,
                    scheme_name,
                )
                skipped += 1
                continue

            isin = row["ISIN"].strip()
            amfi_code: str | None = None
            if isin in amfi_mapping:
                amfi_code, is_reinvest = amfi_mapping[isin]
                # If AMFI says "this ISIN is the reinvest variant" but the BSE
                # name says payout (or vice versa) -- and another row covers
                # the same ISIN with the opposite variant -- the AMFI
                # mapping is mis-assigned. Drop this row to avoid a wrong
                # amfi_code on a real scheme.
                dup_count = len(dups[isin])
                if dup_count > 1 and (
                    (is_reinvest is True and re.search("payout", scheme_name, re.I))
                    or (is_reinvest is False and re.search("reinvest", scheme_name, re.I))
                ):
                    logger.debug("[AMFI mismatch] ignoring %s :: %s", isin, scheme_name)
                    skipped += 1
                    continue

            if amfi_code is None:
                amfi_code = fallback_amfi_map.get(isin)

            rta = row["RTA Agent Code"].strip()
            rta_code = row["Channel Partner Code"].strip()
            amc_code = row["AMC Scheme Code"].strip()
            amc = row["AMC Code"].strip()
            if rta_code == "":
                if amc in AMC_MAP:
                    rta_code = f"{AMC_MAP[amc]}{amc_code}"
                else:
                    logger.warning("Empty rta_code for row: %s", row)
                    skipped += 1
                    continue
            elif rta_code == "BALCD":  # closed-scheme placeholder used by Aditya Birla
                if amc == "BirlaSunLifeMutualFund_MF":
                    rta_code = f"B{amc_code}"
                else:
                    raise ValueError(f'"BALCD" detected for unexpected AMC: {amc}')

            row_id = int(row["Unique No"])
            rows[row_id] = SchemeRow(
                id=row_id,
                name=scheme_name,
                isin=isin,
                amfi_code=amfi_code,
                type=category,
                rta=rta,
                rta_code=rta_code,
                amc_code=amc_code,
            )

    return rows, total, skipped


def build_rows_from_franklin(franklin_csv_path: Path) -> dict[int, SchemeRow]:
    """Read the static Franklin override CSV and return SchemeRow objects.

    The synthetic IDs start at :data:`_FRANKLIN_ROW_START` to avoid colliding
    with BSE Unique No values.
    """
    rows: dict[int, SchemeRow] = {}
    with open(franklin_csv_path) as fp:
        reader = csv.DictReader(fp)
        for idx, row in enumerate(reader):
            rta_code = (row.get("rta_code") or "").strip()
            if not rta_code:
                continue
            rta_code_int = int(rta_code)
            row_id = _FRANKLIN_ROW_START + idx

            amfi_code_str = (row.get("amfi_code") or "").strip() or None
            rows[row_id] = SchemeRow(
                id=row_id,
                name=row["json"].strip(),
                isin=row["isin"].strip(),
                amfi_code=amfi_code_str,
                type=row["category"].strip(),
                rta="FRANKLIN",
                rta_code=f"FTI{rta_code_int:03d}",
                amc_code=f"{rta_code_int:03d}",
            )
    return rows


def merge_rows(
    baseline: dict[int, SchemeRow],
    bse_rows: dict[int, SchemeRow],
    franklin_rows: dict[int, SchemeRow],
) -> list[SchemeRow]:
    """Merge three row sources, deduping by content.

    Baseline rows are preserved unless a BSE/Franklin row with the same
    natural key (name, isin, amfi_code, rta, rta_code, amc_code) appears --
    this matches the "keep deleted schemes around" property the original
    pipeline had (BSE drops closed schemes; we still want them queryable).
    """
    seen: set[tuple] = set()
    out: dict[int, SchemeRow] = {}

    for r in baseline.values():
        seen.add(r.dedupe_key())
        out[r.id] = r

    for source in (bse_rows, franklin_rows):
        for row_id, r in source.items():
            if r.dedupe_key() in seen:
                continue
            seen.add(r.dedupe_key())
            out[row_id] = r

    return list(out.values())
