"""Pure functions that transform fetched data into ``scheme`` table rows.

Splitting this out of the orchestrator makes the join logic testable without
hitting any network. Each function takes data in and returns data out --
side effects (DB writes, HTTP) live in :mod:`cptools.update_isin_db`.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from .settings import logger


def today_iso() -> str:
    """Return today's date as ``YYYY-MM-DD``. Stand-alone for monkeypatching in tests."""
    return datetime.date.today().isoformat()


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


# SEBI category -> EQUITY/DEBT for tax classification.
#
# Source of truth for what AMFI publishes is the section header in NAVAll.txt,
# parsed in cptools.fetchers.amfi. The categories observed in production fall
# into five families:
#
#   "Equity Scheme - ..."        => EQUITY (12 sub-categories)
#   "Debt Scheme - ..."          => DEBT (13 sub-categories)
#   "Hybrid Scheme - ..."        => depends on sub-category (see below)
#   "Solution Oriented Scheme - ..." => EQUITY (Retirement / Children's funds)
#   "Other Scheme - ..."         => varies (ETF / Index / Gold / FoF)
#
# Plus legacy single-word labels still appearing in the feed for old schemes
# AMFI never re-categorised: "Income", "Growth", "Gilt", "ELSS".
#
# Hybrid tax treatment is the trickiest:
#   - Aggressive Hybrid Fund     >=65% equity by SEBI mandate -> EQUITY
#   - Arbitrage Fund             debt-like returns, taxed as equity by IT Act
#   - Equity Savings Fund        target equity exposure >=65% (inc. arbitrage)
#   - Multi Asset Allocation     mandate min 10% in 3+ classes; most run
#                                >=65% equity for tax efficiency
#   - Dynamic Asset Allocation / Balanced Advantage
#                                most run >=65% equity (via arbitrage) for tax efficiency
#   - Balanced Hybrid Fund       ~50/50 equity:debt -> DEBT (conservative)
#   - Conservative Hybrid Fund   <35% equity -> DEBT
#
# Gold ETF and Overseas FoF were re-categorised as debt slab post Finance
# Act 2023 amendments to Section 50AA. Domestic FoFs and equity index funds
# remain equity if their underlying is equity-heavy (the common case).
_SEBI_TAX_TYPE_EXACT: dict[str, str] = {
    # Hybrid Scheme - per sub-category (no prefix match works here)
    "Hybrid Scheme - Aggressive Hybrid Fund": "EQUITY",
    "Hybrid Scheme - Arbitrage Fund": "EQUITY",
    "Hybrid Scheme - Balanced Hybrid Fund": "DEBT",
    "Hybrid Scheme - Conservative Hybrid Fund": "DEBT",
    "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage": "EQUITY",
    "Hybrid Scheme - Equity Savings": "EQUITY",
    "Hybrid Scheme - Multi Asset Allocation": "EQUITY",
    # Other Scheme - per sub-category
    "Other Scheme - FoF Domestic": "EQUITY",
    "Other Scheme - FoF Overseas": "DEBT",
    "Other Scheme - Gold ETF": "DEBT",
    "Other Scheme - Index Funds": "EQUITY",
    "Other Scheme - Other ETFs": "EQUITY",
    "Other Scheme - Other  ETFs": "EQUITY",  # AMFI feed has double-space here
    # Legacy labels (pre-2018 SEBI categorisation)
    "Income": "DEBT",
    "Growth": "EQUITY",
    "Gilt": "DEBT",
    "ELSS": "EQUITY",
    # Close-Ended bare labels (no sub-category in the header)
    "Hybrid Scheme": "EQUITY",  # most close-ended hybrids are equity-oriented
}


def sebi_category_to_tax_type(category: str | None) -> str | None:
    """Map an AMFI section-header category to EQUITY / DEBT for tax purposes.

    Returns ``None`` when the category is unknown -- callers should fall
    back to whatever classification they had before (typically the
    BSE-derived ``MAIN_CATEGORY_MAP`` result).
    """
    if category is None:
        return None
    if category in _SEBI_TAX_TYPE_EXACT:
        return _SEBI_TAX_TYPE_EXACT[category]
    # Catch-all prefixes for the well-behaved families.
    if category.startswith("Equity Scheme"):
        return "EQUITY"
    if category.startswith("Debt Scheme"):
        return "DEBT"
    if category.startswith("Solution Oriented"):
        return "EQUITY"
    return None


@dataclass(frozen=True, slots=True)
class SchemeRow:
    """One row destined for the ``scheme`` table.

    ``sebi_category`` is the verbatim SEBI category string parsed from the
    AMFI NAV file's section header, e.g. ``"Equity Scheme - Large Cap Fund"``.
    It's ``None`` for rows that pre-date this enrichment (carried forward
    from a baseline DB without the column) and for rows whose ISIN isn't in
    AMFI's live feed (closed-end / Franklin / retired schemes).

    ``last_seen`` is an ISO date (``YYYY-MM-DD``) of the most recent build
    that re-confirmed this row's existence from a live source (BSE, AMFI,
    Franklin CSV). ``None`` means "carried forward from an earlier baseline
    without ever being re-confirmed under the new schema" -- a diagnostic
    signal, never a deletion trigger.
    """

    id: int
    name: str
    isin: str
    amfi_code: str | None
    type: str
    rta: str
    rta_code: str
    amc_code: str
    sebi_category: str | None = None
    last_seen: str | None = None

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
            self.sebi_category,
            self.last_seen,
        )

    def dedupe_key(self) -> tuple:
        # sebi_category and last_seen are metadata, NOT identity. Two rows
        # that differ only in category or last_seen date are the same
        # scheme -- e.g. one carried forward from an older baseline (NULL
        # values) and one re-confirmed today. Including them here would
        # split historical rows from their refreshed counterparts.
        return (self.name, self.isin, self.amfi_code, self.rta, self.rta_code, self.amc_code)


# Columns that may or may not exist in a baseline DB depending on its
# generation. We probe for presence rather than alter-tabling so the
# pipeline tolerates running against older DBs (e.g. ones built before
# the sebi_category / last_seen columns existed) unchanged.
_SCHEME_BASE_COLS = ("id", "name", "isin", "amfi_code", "type", "rta", "rta_code", "amc_code")
_SCHEME_OPTIONAL_COLS = ("sebi_category", "last_seen")


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def read_baseline(db_path: Path) -> tuple[dict[int, SchemeRow], dict[str, str]]:
    """Read the existing ``scheme`` table to drive carry-forward.

    Returns ``(rows_by_id, amfi_map)``. ``amfi_map`` is ``isin -> amfi_code``
    for rows where ``amfi_code IS NOT NULL`` -- used as a fallback when the
    live AMFI feed has dropped an entry but the scheme still trades.

    A missing baseline file is not fatal: returns empty mappings. This is
    what makes the pipeline safe to run from a clean working tree (e.g. a
    stateless CI container).

    Tolerates baseline DBs that pre-date the ``sebi_category`` column: any
    optional column not present is filled with ``None`` on the resulting
    :class:`SchemeRow`.
    """
    if not db_path.exists():
        logger.warning("No baseline DB at %s; starting from empty state", db_path)
        return {}, {}

    rows: dict[int, SchemeRow] = {}
    amfi_map: dict[str, str] = {}
    with sqlite3.connect(db_path) as conn:
        present = _existing_columns(conn, "scheme")
        select_cols = list(_SCHEME_BASE_COLS) + [
            col for col in _SCHEME_OPTIONAL_COLS if col in present
        ]
        cur = conn.execute(f"SELECT {', '.join(select_cols)} FROM scheme")
        for row in cur:
            # Pad missing optional columns with None.
            kw = dict(zip(select_cols, row, strict=True))
            scheme = SchemeRow(
                id=kw["id"],
                name=kw["name"],
                isin=kw["isin"],
                amfi_code=kw["amfi_code"],
                type=kw["type"],
                rta=kw["rta"],
                rta_code=kw["rta_code"],
                amc_code=kw["amc_code"],
                sebi_category=kw.get("sebi_category"),
                last_seen=kw.get("last_seen"),
            )
            rows[scheme.id] = scheme
            if scheme.amfi_code is not None:
                amfi_map[scheme.isin] = scheme.amfi_code

    logger.info("Baseline: %d scheme rows, %d isin->amfi_code mappings", len(rows), len(amfi_map))
    return rows, amfi_map


@dataclass(frozen=True, slots=True)
class IsinRow:
    """One row destined for the ``isin`` table (generic security metadata).

    Used for NSDL/CDSL CAS support -- maps an equity / bond / AIF ISIN to a
    human-readable name, the issuer, instrument type, and lifecycle status.
    ``last_seen`` behaves the same as on :class:`SchemeRow`: ISO date of the
    most recent live confirmation, or ``None`` for un-touched baseline rows.
    """

    isin: str
    name: str | None
    issuer: str | None
    type: str
    status: str | None
    last_seen: str | None = None

    def as_tuple(self) -> tuple:
        return (self.isin, self.name, self.issuer, self.type, self.status, self.last_seen)


_ISIN_BASE_COLS = ("isin", "name", "issuer", "type", "status")
_ISIN_OPTIONAL_COLS = ("last_seen",)


def read_baseline_isin(db_path: Path) -> dict[str, IsinRow]:
    """Read the existing ``isin`` table to drive carry-forward.

    Returns ``isin -> IsinRow``. Tolerates baseline DBs missing the table
    entirely (returns empty) or missing the ``last_seen`` column (fills
    with ``None``).
    """
    if not db_path.exists():
        logger.warning("No baseline DB at %s; isin table will start from empty state", db_path)
        return {}

    rows: dict[str, IsinRow] = {}
    with sqlite3.connect(db_path) as conn:
        if "isin" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            logger.warning("Baseline DB has no isin table; starting empty")
            return {}

        present = _existing_columns(conn, "isin")
        select_cols = list(_ISIN_BASE_COLS) + [col for col in _ISIN_OPTIONAL_COLS if col in present]
        cur = conn.execute(f"SELECT {', '.join(select_cols)} FROM isin")
        for row in cur:
            kw = dict(zip(select_cols, row, strict=True))
            rows[kw["isin"]] = IsinRow(
                isin=kw["isin"],
                name=kw["name"],
                issuer=kw["issuer"],
                type=kw["type"],
                status=kw["status"],
                last_seen=kw.get("last_seen"),
            )

    logger.info("Baseline: %d isin rows", len(rows))
    return rows


def merge_isin_rows(
    baseline: dict[str, IsinRow],
    new_rows: list[list[str]],
    keep_types: frozenset[str],
    today: str | None = None,
) -> list[IsinRow]:
    """Merge baseline isin rows with fresh captn3m0 rows. **Append-only.**

    Contract -- mirrors :func:`merge_rows` for the ``scheme`` table:

    - Baseline rows whose ``type`` is in ``keep_types`` are preserved
      unconditionally. Out-of-scope baseline types (COMMERCIAL PAPER,
      CERTIFICATE OF DEPOSIT, TREASURY BILLS, SECURITISED INSTRUMENT,
      MUTUAL FUND UNIT*) are filtered out -- they fall outside the
      KEEP_TYPES scope and won't appear in any subsequent build either.
      The very first run under this rule effectively cleans them up.
    - A live row whose ISIN matches a kept baseline row is treated as a
      *re-confirmation*: ``last_seen`` is bumped to ``today``, and any
      metadata field (name / issuer / type / status) that the live source
      provides overrides the baseline value. The baseline value is kept
      only if the live source has dropped it (None / empty).
    - A live row whose ISIN isn't in baseline is inserted fresh with
      ``last_seen=today``.

    ``new_rows`` items are 5-tuples of ``[isin, name, issuer, type, status]``
    -- the shape produced by :func:`cptools.fetchers.isin.parse_isin_csv`,
    which already applies the type filter and casing normalisation.
    """
    today = today or today_iso()
    out: dict[str, IsinRow] = {}

    # 1. Baseline carry-forward, filtered to keep_types.
    for isin, row in baseline.items():
        if row.type in keep_types:
            out[isin] = row

    # 2. Live rows. Either re-confirm a baseline row or insert as new.
    for new_row in new_rows:
        isin, name, issuer, type_, status = new_row
        existing = out.get(isin)
        if existing is not None:
            out[isin] = replace(
                existing,
                # Live metadata wins where it's populated; baseline fills
                # gaps (e.g. captn3m0 dropped an issuer string we had).
                name=name if name else existing.name,
                issuer=issuer if issuer else existing.issuer,
                type=type_,  # live type already normalised + filtered
                status=status if status else existing.status,
                last_seen=today,
            )
            continue
        out[isin] = IsinRow(
            isin=isin,
            name=name or None,
            issuer=issuer or None,
            type=type_,
            status=status or None,
            last_seen=today,
        )

    return list(out.values())


def build_rows_from_bse(
    bse_csvs: list[str],
    amfi_mapping: dict[str, tuple[str, bool | None]],
    fallback_amfi_map: dict[str, str],
    sebi_categories: dict[str, str] | None = None,
) -> tuple[dict[int, SchemeRow], int, int]:
    """Convert BSE master CSVs into :class:`SchemeRow` instances.

    Returns ``(rows_by_id, total_seen, skipped)``. ``skipped`` includes
    closed-end schemes (``-I`` / ``-L\\d`` suffix), unknown scheme types,
    and AMFI reinvest/payout mismatches.

    ``sebi_categories`` is the ``isin -> SEBI category`` map returned by
    :func:`cptools.fetchers.amfi.parse_amfi_nav_text`. When present and the
    category maps to a known EQUITY/DEBT tax class, it overrides the
    BSE-derived ``MAIN_CATEGORY_MAP`` value -- AMFI's classification is
    authoritative for tax purposes whereas BSE's ``Scheme Type`` is a rough
    trading-platform label. The raw SEBI category string is also stored on
    the resulting :class:`SchemeRow` for downstream consumers.
    """
    sebi_categories = sebi_categories or {}
    rows: dict[int, SchemeRow] = {}
    total = 0
    skipped = 0
    sebi_overrides = 0

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
            bse_category = MAIN_CATEGORY_MAP.get(scheme_type)
            if bse_category is None:
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

            # Prefer AMFI-derived tax classification when available; fall
            # back to BSE-derived only if AMFI didn't categorise the ISIN
            # or returned a category we don't recognise.
            sebi_category = sebi_categories.get(isin)
            sebi_tax_type = sebi_category_to_tax_type(sebi_category)
            if sebi_tax_type is not None:
                category = sebi_tax_type
                if sebi_tax_type != bse_category:
                    sebi_overrides += 1
                    logger.debug(
                        "SEBI override for %s: BSE=%s -> SEBI=%s (%r)",
                        isin,
                        bse_category,
                        sebi_tax_type,
                        sebi_category,
                    )
            else:
                category = bse_category
                if sebi_category is not None:
                    # AMFI gave us a category we don't recognise -- log so we
                    # can extend _SEBI_TAX_TYPE_EXACT next time it shifts.
                    logger.debug(
                        "Unmapped SEBI category %r for %s; falling back to BSE %s",
                        sebi_category,
                        isin,
                        bse_category,
                    )

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
                sebi_category=sebi_category,
            )

    if sebi_overrides:
        logger.info("SEBI category overrode BSE-derived type for %d rows", sebi_overrides)
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
    today: str | None = None,
) -> list[SchemeRow]:
    """Merge three row sources, deduping by content. **Append-only.**

    Contract -- DO NOT CHANGE WITHOUT BUMPING ``DBFORMAT``:

    - Every baseline row is preserved unconditionally. Old CAS files
      reference scheme ISINs that may no longer trade; deleting baseline
      rows would silently break historical lookups.
    - A BSE/Franklin row that shares a baseline row's dedupe_key
      (name+isin+amfi_code+rta+rta_code+amc_code) is treated as a
      *re-confirmation*: the kept baseline row gets its ``last_seen``
      bumped to ``today`` and, if AMFI added a SEBI category that the
      baseline lacked, it inherits that too.
    - A BSE/Franklin row that does not match any baseline dedupe_key is
      inserted as a new row with ``last_seen=today``.
    - Baseline rows with no live source confirmation retain their previous
      ``last_seen`` value. Under the first run with the new schema that
      means ``last_seen=NULL`` -- a useful diagnostic for "never
      re-confirmed since the migration."

    See ``tests/tools/test_invariants.py`` for the regression test
    that locks this contract down.

    ``today`` is injected for tests. In production it defaults to
    :func:`today_iso`.
    """
    today = today or today_iso()
    seen_keys: dict[tuple, int] = {}
    out: dict[int, SchemeRow] = {}

    # 1. Baseline carry-forward. Preserved unconditionally; last_seen left
    #    at whatever the baseline DB recorded (often NULL on first run).
    for r in baseline.values():
        seen_keys[r.dedupe_key()] = r.id
        out[r.id] = r

    # 2. Live sources. Either re-confirm a baseline row (bump last_seen)
    #    or insert as a brand-new row tagged with today's date.
    for source in (bse_rows, franklin_rows):
        for row_id, r in source.items():
            existing_id = seen_keys.get(r.dedupe_key())
            if existing_id is not None:
                # Re-confirmation. Bump last_seen; inherit sebi_category
                # if the baseline row didn't have one.
                existing = out[existing_id]
                out[existing_id] = replace(
                    existing,
                    last_seen=today,
                    sebi_category=existing.sebi_category
                    if existing.sebi_category is not None
                    else r.sebi_category,
                )
                continue
            # Brand-new row.
            seen_keys[r.dedupe_key()] = row_id
            out[row_id] = replace(r, last_seen=today)

    return list(out.values())
