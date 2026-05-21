"""Golden tests for the row-builder pieces.

Network is never hit. All fixtures are inline strings / CSV blobs mimicking
the shape of the real BSE / AMFI / Franklin sources. These are the most
defect-prone parts of the pipeline (joins, dedupe, special-case AMC codes)
so they get the most coverage.
"""

from __future__ import annotations

import pytest
from cptools.builder import (
    _FRANKLIN_ROW_START,
    SchemeRow,
    build_rows_from_bse,
    build_rows_from_franklin,
    merge_rows,
)

# Minimal subset of BSE master columns. We don't model the full ~30-column
# layout because the builder only touches the columns named here; csv.DictReader
# is happy with any column set so long as the names match.
_BSE_COLUMNS = (
    "Unique No",
    "Scheme Code",
    "AMC Scheme Code",
    "ISIN",
    "AMC Code",
    "Scheme Type",
    "Scheme Name",
    "RTA Agent Code",
    "Channel Partner Code",
)


def _bse_csv(*rows: dict[str, str]) -> str:
    """Render rows as a pipe-delimited CSV matching the BSE master format."""
    header = "|".join(_BSE_COLUMNS)
    lines = [header]
    for row in rows:
        lines.append("|".join(row.get(col, "") for col in _BSE_COLUMNS))
    return "\n".join(lines)


def test_build_rows_from_bse_basic_happy_path():
    csv_text = _bse_csv(
        {
            "Unique No": "100",
            "Scheme Code": "HDFC-A",
            "AMC Scheme Code": "HAFRG",
            "ISIN": "INF179K01319",
            "AMC Code": "HDFCMutualFund_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "HDFC Arbitrage Fund - Growth",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "HAFRG",
        }
    )
    rows, total, skipped = build_rows_from_bse([csv_text], amfi_mapping={}, fallback_amfi_map={})
    assert total == 1
    assert skipped == 0
    assert len(rows) == 1
    row = rows[100]
    assert row.isin == "INF179K01319"
    assert row.rta == "CAMS"
    assert row.rta_code == "HAFRG"
    assert row.type == "EQUITY"


def test_build_rows_skips_closed_end_schemes():
    csv_text = _bse_csv(
        {
            "Unique No": "200",
            "Scheme Code": "CLOSED-I",  # -I suffix => closed-end
            "AMC Scheme Code": "X",
            "ISIN": "INF000000001",
            "AMC Code": "Anything_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "Closed Scheme",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "ZZZ",
        },
        {
            "Unique No": "201",
            "Scheme Code": "CLOSED-L1",  # -L\d suffix => closed-end
            "AMC Scheme Code": "Y",
            "ISIN": "INF000000002",
            "AMC Code": "Anything_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "Closed Scheme L",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "ZZZ",
        },
    )
    rows, total, skipped = build_rows_from_bse([csv_text], {}, {})
    assert total == 2
    assert skipped == 2
    assert rows == {}


def test_build_rows_unknown_scheme_type_skips_with_warning(caplog):
    csv_text = _bse_csv(
        {
            "Unique No": "300",
            "Scheme Code": "NEW-A",
            "AMC Scheme Code": "X",
            "ISIN": "INF000000003",
            "AMC Code": "Anything_MF",
            "Scheme Type": "QuantumDecoupled",  # not in MAIN_CATEGORY_MAP
            "Scheme Name": "Future Fund",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "QD1",
        }
    )
    with caplog.at_level("WARNING"):
        rows, total, skipped = build_rows_from_bse([csv_text], {}, {})
    assert total == 1
    assert skipped == 1
    assert rows == {}
    assert any("Unknown BSE scheme_type" in r.message for r in caplog.records)


def test_build_rows_synthesizes_rta_code_for_nippon():
    csv_text = _bse_csv(
        {
            "Unique No": "400",
            "Scheme Code": "NIP-A",
            "AMC Scheme Code": "NSF42",
            "ISIN": "INF000000004",
            "AMC Code": "NipponIndiaMutualFund_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "Nippon Sample Fund",
            "RTA Agent Code": "KARVY",
            "Channel Partner Code": "",  # empty -> rebuilt via AMC_MAP
        }
    )
    rows, _, _ = build_rows_from_bse([csv_text], {}, {})
    assert rows[400].rta_code == "RMFNSF42"


def test_build_rows_falls_back_to_baseline_amfi_code():
    csv_text = _bse_csv(
        {
            "Unique No": "500",
            "Scheme Code": "X-A",
            "AMC Scheme Code": "X",
            "ISIN": "INF000000005",
            "AMC Code": "Anything_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "X Fund",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "XFA",
        }
    )
    rows, _, _ = build_rows_from_bse(
        [csv_text], amfi_mapping={}, fallback_amfi_map={"INF000000005": "999111"}
    )
    assert rows[500].amfi_code == "999111"


def test_build_rows_amfi_reinvest_payout_mismatch_drops_row():
    # Two BSE rows for the same ISIN -- payout AND reinvest variants. AMFI
    # mapping declares the ISIN as the reinvest variant (is_reinvest=True);
    # the row whose name says "Payout" is the mismatch and must be dropped.
    csv_text = _bse_csv(
        {
            "Unique No": "600",
            "Scheme Code": "X-A",
            "AMC Scheme Code": "X",
            "ISIN": "INF000000006",
            "AMC Code": "Anything_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "X Fund Payout",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "XFP",
        },
        {
            "Unique No": "601",
            "Scheme Code": "X-B",
            "AMC Scheme Code": "X",
            "ISIN": "INF000000006",
            "AMC Code": "Anything_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "X Fund Reinvest",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "XFR",
        },
    )
    rows, total, skipped = build_rows_from_bse(
        [csv_text],
        amfi_mapping={"INF000000006": ("123456", True)},
        fallback_amfi_map={},
    )
    assert total == 2
    assert skipped == 1
    # The "Payout" row should have been dropped; the "Reinvest" row kept.
    assert 600 not in rows
    assert rows[601].name.endswith("Reinvest")


def test_build_rows_balcd_special_case():
    csv_text = _bse_csv(
        {
            "Unique No": "700",
            "Scheme Code": "BSL-A",
            "AMC Scheme Code": "13",
            "ISIN": "INF000000007",
            "AMC Code": "BirlaSunLifeMutualFund_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "Birla Closed Scheme",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "BALCD",
        }
    )
    rows, _, _ = build_rows_from_bse([csv_text], {}, {})
    assert rows[700].rta_code == "B13"


def test_build_rows_balcd_unexpected_amc_raises():
    csv_text = _bse_csv(
        {
            "Unique No": "701",
            "Scheme Code": "OTHER-A",
            "AMC Scheme Code": "1",
            "ISIN": "INF000000008",
            "AMC Code": "SomeOtherAMC_MF",
            "Scheme Type": "Equity",
            "Scheme Name": "Surprise Scheme",
            "RTA Agent Code": "CAMS",
            "Channel Partner Code": "BALCD",
        }
    )
    with pytest.raises(ValueError, match="BALCD"):
        build_rows_from_bse([csv_text], {}, {})


def test_build_rows_from_franklin(tmp_path):
    p = tmp_path / "franklin.csv"
    # Each line is a real CSV record; intentionally long. We build via list
    # join so each Python source line stays under the ruff line-length cap.
    csv_lines = [
        "isin,fund,amfi,json,category,rta_code,amfi_code,type",
        (
            "INF090I01726,Franklin India PRIMA - Div Payout,Franklin Prima IDCW,"
            "Franklin India Prima Fund - IDCW - Payout,EQUITY,1,100472,payout/growth"
        ),
        (
            "INF090I01734,Franklin India PRIMA - Reinvest,Franklin Prima IDCW,"
            "Franklin India Prima Fund - IDCW - Reinvestment,EQUITY,1,100472,reinvest"
        ),
        "INF000NOTHING,,,Empty rta_code row,EQUITY,,,",
        "",
    ]
    p.write_text("\n".join(csv_lines))
    rows = build_rows_from_franklin(p)
    # Empty-rta_code row should be silently skipped.
    assert len(rows) == 2
    first = rows[_FRANKLIN_ROW_START]
    assert first.rta == "FRANKLIN"
    assert first.rta_code == "FTI001"
    assert first.amc_code == "001"
    assert first.amfi_code == "100472"
    assert first.isin == "INF090I01726"


def test_merge_rows_dedupes_against_baseline():
    baseline = {
        1: SchemeRow(
            id=1,
            name="Existing Scheme",
            isin="INF000000010",
            amfi_code="100",
            type="EQUITY",
            rta="CAMS",
            rta_code="EXIST",
            amc_code="01",
        )
    }
    # BSE row with exactly the same content but a different id -> dedupe.
    bse_rows = {
        500: SchemeRow(
            id=500,
            name="Existing Scheme",
            isin="INF000000010",
            amfi_code="100",
            type="EQUITY",
            rta="CAMS",
            rta_code="EXIST",
            amc_code="01",
        ),
        501: SchemeRow(
            id=501,
            name="New Scheme",
            isin="INF000000011",
            amfi_code="101",
            type="DEBT",
            rta="KARVY",
            rta_code="NEW",
            amc_code="02",
        ),
    }
    merged = merge_rows(baseline, bse_rows, {})
    isins = sorted(r.isin for r in merged)
    assert isins == ["INF000000010", "INF000000011"]


def test_merge_rows_keeps_baseline_when_bse_drops_it():
    # If a scheme was in the baseline but BSE no longer lists it (closed,
    # retired, etc.), we still want it in the published DB so historical
    # statements can resolve.
    baseline = {
        1: SchemeRow(
            id=1,
            name="Retired Fund",
            isin="INF000000099",
            amfi_code="900",
            type="DEBT",
            rta="CAMS",
            rta_code="RETIRED",
            amc_code="99",
        )
    }
    merged = merge_rows(baseline, {}, {})
    assert len(merged) == 1
    assert merged[0].isin == "INF000000099"
