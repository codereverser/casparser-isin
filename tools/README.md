# `tools/` — isin.db build pipeline

This directory holds the offline tooling that produces the bundled
`casparser_isin/isin.db` SQLite database. It is **not** shipped in the
wheel — it lives here so the database can be refreshed reproducibly from
upstream sources.

## Quick start

```bash
# Install the tools dependency group
uv sync --group tools

# Local dry-run (fetches everything, builds in memory, writes to repo, no upload)
uv run python tools/update_isin_db.py --no-upload

# Production cron (fresh container, no cache, uploads to Backblaze B2)
CASPARSER_ISIN_TOOLS_NO_CACHE=1 \
  B2_APP_ID=...    \
  B2_APP_KEY=...   \
  B2_BUCKET=...    \
  uv run python tools/update_isin_db.py
```

Exit codes:

| Code | Meaning |
|-----:|---------|
| `0`  | No change — built DB matches current `isin.db` |
| `1`  | New database published (uploaded to B2 unless `--no-upload`) |
| `2`  | Error (bad upstream, row-count guard tripped, etc.) |

## Layout

```
tools/
├── cptools/                # implementation
│   ├── settings.py         # paths + env-var resolution + logger
│   ├── http.py             # cached HTTP session factory
│   ├── builder.py          # pure functions: read_baseline, merge_rows, ...
│   ├── b2.py               # Backblaze upload with size verification
│   ├── constants.py        # source URLs
│   └── fetchers/
│       ├── amfi.py         # AMFI NAVAll.txt parser (live + frozen 2018)
│       ├── bse.py          # BSE StarMF scheme master scraper
│       └── isin.py         # captn3m0/india-isin-data CSV
├── files/                  # static reference data (DO NOT REGENERATE)
│   ├── AMFI_NAV_31Jan2018.txt    # frozen NAV for LTCG carry-forward
│   └── franklin_funds.csv         # hand-curated Franklin RTA-code mapping
└── update_isin_db.py       # orchestrator (entry point)
```

## The append-only contract

The pipeline is **append-only by design**. Every baseline row whose type
is in scope must appear in the output of every subsequent build. This
property is what lets us parse historical CAS files: a scheme that
existed in 2014 but was merged in 2020 must still resolve today.

Enforcement lives at three layers:

1. **`merge_rows` (in `cptools/builder.py`)** — preserves every baseline
   row, treats matching live rows as re-confirmations (bumps `last_seen`,
   refreshes metadata), inserts non-matching rows as new.
2. **Row-count guard (in `update_isin_db.py`)** — refuses to publish if
   any table's in-scope row count drops vs. baseline. For the `isin`
   table the comparison uses the canonical type set (`KEEP_TYPES`) so
   the one-time cleanup of out-of-scope rows doesn't trip the guard.
3. **End-to-end invariant tests (`tests/tools/test_invariants.py`)** —
   exercise the full pipeline with stubbed fetchers and assert that
   every baseline `(scheme.id, isin)` survives.

If you ever need to *break* the append-only contract (e.g. to drop a
column, move rows between tables, or change a value's interpretation),
bump `DBFORMAT` in `update_isin_db.py` and coordinate a release of the
library at the same time. The library's `dbformat=N` gate prevents old
clients from auto-downloading a schema-incompatible DB.

## Sources

| Source | URL | Provides |
|---|---|---|
| AMFI NAV portal | https://portal.amfiindia.com/spages/NAVAll.txt | ISIN ↔ amfi_code, NAV, SEBI category (via section headers) |
| BSE StarMF | https://bsestarmf.in/RptSchemeMaster.aspx | RTA, rta_code, AMC code (HTML-scraped form) |
| captn3m0/india-isin-data | https://github.com/captn3m0/india-isin-data | Generic ISIN registry (equities, debt, AIF, REIT, etc.) |
| AMFI 2018 (frozen) | `files/AMFI_NAV_31Jan2018.txt` | 31-Jan-2018 NAV for LTCG grandfathering |
| Franklin static | `files/franklin_funds.csv` | Hand-curated post-migration RTA-code mapping |

## Scope of the `isin` table

The captn3m0 source covers 26+ instrument types. We ship only the ones
that appear in retail NSDL/CDSL CAS files:

**Kept**: `EQUITY SHARES`, `PREFERENCE SHARES`, `DEBENTURE`, `BOND`,
`SOVEREIGN GOLD BOND`, `MUNICIPAL BOND`, `DEEP DISCOUNT BOND`,
`REGULAR RETURN BOND`, `FLOATING RATE BOND`, `GOVERNMENT SECURITIES`,
`INFRASTRUCTURE INVESTMENT TRUST`, `REAL ESTATE INVESTMENT TRUSTS`,
`ALTERNATIVE INVESTMENT FUND`, `RIGHTS ENTITLEMENT`, `WARRANT`,
`INDIAN DEPOSITORY RECEIPT`.

**Dropped**: `COMMERCIAL PAPER`, `CERTIFICATE OF DEPOSIT`,
`TREASURY BILLS`, `SECURITISED INSTRUMENT`, and `MUTUAL FUND UNIT*`
(those belong in the `scheme` table). These are institutional-only or
duplicated elsewhere.

The set lives in `KEEP_TYPES` in `cptools/fetchers/isin.py`. If a new
instrument starts appearing in CAS files, add it there.

## BSE is optional

BSE StarMF is the source for the `rta_code` mapping. The library's
lookup code is ISIN-first since v1.0 — every modern CAS file carries an
ISIN on every holding (empirical audit: 100% across 8 years of
statements, 707 rows from CAMS / Kfintech / NSDL / CDSL). The
BSE-derived `rta_code` table is a fallback path used only for older
statements or rare parse-failure edge cases.

This makes BSE *optional* at build time:

- **A failed BSE fetch does not abort the run.** The orchestrator catches
  any exception, logs it at WARNING, and continues with empty `bse_rows`.
  Baseline carry-forward keeps the existing `rta_code` data alive until
  BSE is healthy again.
- **`CASPARSER_ISIN_TOOLS_NO_BSE=1`** explicitly skips the BSE fetch.
  Useful when the BSE form layout changes, the upstream is unreachable,
  or you simply want a build that depends only on AMFI + captn3m0.

### Building without BSE

Shipping a build without BSE is a small operation:

1. Set `CASPARSER_ISIN_TOOLS_NO_BSE=1` in the cron secret store.
2. Trigger a manual build (`workflow_dispatch` with `--no-upload` if
   available) to validate end-to-end. The merge-stats log line should
   show `bse_status=disabled`.
3. Re-run without `--no-upload` so the resulting DB is published.
   Existing `rta_code` entries persist (baseline carry-forward); they
   just stop refreshing.
4. Cut a `casparser-isin` release noting the change. The library API
   is unchanged; users who never used the `rta_code` fallback see no
   difference.
5. Optional cleanup (separate change, requires `dbformat=2` bump):
   drop the `rta`, `rta_code`, `amc_code` columns from the scheme table.

The library continues to work — the ISIN-first lookup path covers every
modern CAS file. Only the fallback `(rta, rta_code)` lookups (used for
very old / parse-failure cases) gradually go stale.

## Environment variables

| Variable | Purpose |
|---|---|
| `CASPARSER_ISIN_TOOLS_CACHE` | Override HTTP cache directory (default: `$XDG_CACHE_HOME/casparser-isin-tools` or `~/.cache/casparser-isin-tools`) |
| `CASPARSER_ISIN_TOOLS_NO_CACHE` | Set to `1` to disable the HTTP cache (recommended for cron) |
| `CASPARSER_ISIN_TOOLS_NO_BSE` | Set to `1` to skip the BSE scrape entirely |
| `B2_APP_ID`, `B2_APP_KEY`, `B2_BUCKET` | Backblaze credentials for the meta + db upload step |

## Tests

```bash
# Full suite (skips tools tests if the tools dep group isn't installed)
uv run pytest

# Tools tests only (golden + invariants + guards)
uv run pytest tests/tools/ -v
```

The tools subtree uses `pytest.importorskip("lxml")` in
`tests/tools/conftest.py` so contributors without the tools group still
see the library tests pass.

## Cronjob deployment

Recommended target: GitHub Actions `schedule:` workflow. Stateless
runners force the pipeline to handle a missing baseline DB
gracefully (which `read_baseline` already does), and secrets are
first-class.

Sample cron expression: `30 21 * * *` (03:00 IST daily; AMFI publishes
the day's NAV by 23:00 IST so this gives a comfortable buffer).

## Telemetry to monitor

Each run logs structured per-source counts at `INFO`:

```
BSE source: parsed=N skipped=N kept=N                         # only when bse_status=ok
scheme merge: N rows total | baseline_kept=N bse_status=ok    \
              bse_new=N bse_reconfirmed=N franklin_new=N      \
              franklin_reconfirmed=N baseline_dropped=N
isin merge:   N rows total | baseline_kept=N live_new=N live_reconfirmed=N \
              baseline_cleanup=N in_scope_dropped=N
Row-count guard OK: scheme N->N, isin N (in-scope baseline)->N, nav N->N
```

`bse_status` is one of:

- `ok` — BSE was consulted and rows were produced
- `disabled` — `CASPARSER_ISIN_TOOLS_NO_BSE=1` was set
- `failed` — BSE raised an exception; run continued without it (an
  ERROR-level "BSE fetch failed this run" log accompanies this)

Alert conditions (anything at `ERROR`):

- `baseline_dropped != 0` for the scheme merge — append-only violation
- `in_scope_dropped != 0` for the isin merge — append-only violation
- `bse_status=failed` — BSE went sideways; not fatal, but the operator
  should look at the captured exception
- `RowCountGuardError` raised — feed degraded; investigate before re-running
