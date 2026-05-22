# Changelog

## 2026.5.1
- **Python**: drop support for Python 3.9 / 3.10; require Python 3.11+.
- **`dbformat` bumped to `2`.** The shipped DB gains two new nullable
  columns (`scheme.sebi_category`, `scheme.last_seen`, `isin.last_seen`).
  Older CLI versions calling `casparser-isin --update` will detect the
  format mismatch and skip the download — they continue to use the DB
  bundled with their installed version. Upgrade the package to pick up
  the new format.
- **Dependencies**: refresh runtime deps (rapidfuzz ≥ 3.14, packaging ≥ 24)
  and dev/tooling deps to current versions.
- **Lookup priority** (`MFISINDb.isin_lookup`): documented and locked
  down as ISIN-first → `(rta, rta_code)` → fuzzy on scheme name. When an
  ISIN is supplied and matches a unique row, the rta_code path is no
  longer consulted. Empirical audit across 8 years of CAS files showed
  100% ISIN coverage, so the rta_code path is now explicitly a fallback
  for older statements or parse-failure edge cases.
- **Library safety fixes**:
  - `ISINDb.isin_lookup`: explicit column list (no more `SELECT *`).
  - `MFISINDb.scheme_lookup`: HDFC special-case `LIKE` patterns are
    bound parameters (not string-interpolated literals).
  - `get_isin_db_path()`: logs a warning when `CASPARSER_ISIN_DB` is set
    but points at a non-readable file, instead of silently falling back.
- **CLI hardening**: `casparser-isin --update` now streams the download
  in 1 MiB chunks, writes to a temp file, and atomically `os.replace`s
  into the destination. The remote meta file can optionally carry a
  `sha256` field which is verified post-download. The User-Agent header
  is now properly named (was `"User Agent"` with a space).
- **Updated `isin.db`** (refreshed from current AMFI, BSE, captn3m0
  data):
  - New nullable `scheme.sebi_category` column carrying the verbatim
    SEBI category from AMFI's NAV-file section headers (e.g.
    `"Equity Scheme - Large Cap Fund"`). EQUITY/DEBT classification is
    now derived from this column when available; the BSE-derived
    `MAIN_CATEGORY_MAP` is the fallback.
  - New nullable `last_seen` column on both `scheme` and `isin` tables:
    ISO date of the most recent build that re-confirmed the row from a
    live feed. Diagnostic only — never used to delete rows.
  - `isin` table scoped to retail-relevant instrument types (equities,
    debentures, bonds, AIF/InvIT/REIT, government securities, SGB,
    rights/warrants, IDR). Commercial paper, T-bills, certificates of
    deposit, and securitised-instrument rows are no longer carried.
  - Three unused indexes dropped (`idx_scheme_name`, `idx_scheme_amc`,
    `idx_scheme_rta`) — ~3 MB smaller without any runtime query change.

## 2023.9.10
- Fallback to old lookup when direct isin search fails
- update database

## 2023.9.3
- Lookup scheme via isin
- update database

## 2023.8.18
- fix issues with hdfc mutual fund lookups
- update database

## 2023.1.16
- DB updates

## 2021.7.21 - 2021-07-21
- better support for Franklin Templeton funds
- support new CAS pdf files after migration of funds from FTAMIL RTA to CAMS

## 2021.7.1 - 2021-07-01
- add scheme type (`EQUITY`/`DEBT`) to `SchemeData`
- add nav table for looking up scheme nav for 31-Jan-2018

## 2021.6.1 - 2021-06-01
- support for using custom isin database via `CASPARSER_ISIN_DB` environment variable.
- updated isin.db
- packaging fixes

## 2021.5.1 - 2021-03-02
- DB updates
  - Essel mutual funds have been renamed to NAVI
  - Dividend options of funds renamed as IDCW

## 2021.4.1 - 2021-04-01
- updated isin.db
- updated dependent package versions

## 2021.3.1 - 2021-03-02
- Switch to calendar versioning
- Fix bugs with version comparison in cli update tool
- DB files are hosted in CDN for more frequent updates via CLI. [pypi releases will be limited to major changes in codebase]
