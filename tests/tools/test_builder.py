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
    sebi_category_to_tax_type,
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
            last_seen="2024-01-15",  # last live confirmation
        )
    }
    merged = merge_rows(baseline, {}, {}, today="2026-05-22")
    assert len(merged) == 1
    assert merged[0].isin == "INF000000099"
    # No live source touched this row, so last_seen MUST stay frozen at
    # whatever the baseline DB had.
    assert merged[0].last_seen == "2024-01-15"


class TestMergeRowsLastSeen:
    """Live-source confirmation bumps last_seen; baseline-only rows freeze."""

    def test_baseline_only_row_freezes_last_seen(self):
        baseline = {
            1: SchemeRow(
                id=1,
                name="Frozen Fund",
                isin="INF000FROZEN1",
                amfi_code="100",
                type="EQUITY",
                rta="CAMS",
                rta_code="FRZ",
                amc_code="01",
                last_seen=None,  # never confirmed yet
            )
        }
        merged = merge_rows(baseline, {}, {}, today="2026-05-22")
        # First run with new schema: no live source covers this ISIN.
        # last_seen MUST remain NULL -- "never re-confirmed since migration".
        assert merged[0].last_seen is None

    def test_baseline_row_reconfirmed_by_bse_bumps_last_seen(self):
        baseline = {
            1: SchemeRow(
                id=1,
                name="Active Fund",
                isin="INF000ACTIVE1",
                amfi_code="200",
                type="EQUITY",
                rta="CAMS",
                rta_code="ACT",
                amc_code="02",
                last_seen=None,
            )
        }
        # BSE delivers a row with the same dedupe_key (re-confirmation).
        bse = {
            500: SchemeRow(
                id=500,
                name="Active Fund",
                isin="INF000ACTIVE1",
                amfi_code="200",
                type="EQUITY",
                rta="CAMS",
                rta_code="ACT",
                amc_code="02",
                last_seen="2026-05-22",  # arbitrary; merge sets its own
            )
        }
        merged = merge_rows(baseline, bse, {}, today="2026-05-22")
        # One row, kept under the baseline id, with last_seen bumped to today.
        assert len(merged) == 1
        assert merged[0].id == 1  # baseline id preserved (not BSE's 500)
        assert merged[0].last_seen == "2026-05-22"

    def test_new_bse_row_stamped_with_today(self):
        # Brand-new scheme that wasn't in baseline. last_seen=today.
        bse = {
            500: SchemeRow(
                id=500,
                name="New Fund",
                isin="INF000NEW0001",
                amfi_code="300",
                type="EQUITY",
                rta="CAMS",
                rta_code="NEW",
                amc_code="03",
                last_seen=None,
            )
        }
        merged = merge_rows({}, bse, {}, today="2026-05-22")
        assert len(merged) == 1
        assert merged[0].last_seen == "2026-05-22"

    def test_reconfirmation_inherits_sebi_category_when_baseline_missing(self):
        # Baseline row has no sebi_category (older schema). BSE row arrives
        # with one from AMFI. After merge, the kept row should pick up the
        # new category without losing its baseline id.
        baseline = {
            1: SchemeRow(
                id=1,
                name="Reclassified Fund",
                isin="INF000RECLAS1",
                amfi_code="400",
                type="EQUITY",
                rta="CAMS",
                rta_code="RC",
                amc_code="04",
                sebi_category=None,
                last_seen=None,
            )
        }
        bse = {
            500: SchemeRow(
                id=500,
                name="Reclassified Fund",
                isin="INF000RECLAS1",
                amfi_code="400",
                type="EQUITY",
                rta="CAMS",
                rta_code="RC",
                amc_code="04",
                sebi_category="Equity Scheme - Large Cap Fund",  # newly available
                last_seen=None,
            )
        }
        merged = merge_rows(baseline, bse, {}, today="2026-05-22")
        assert len(merged) == 1
        assert merged[0].id == 1
        assert merged[0].sebi_category == "Equity Scheme - Large Cap Fund"
        assert merged[0].last_seen == "2026-05-22"

    def test_reconfirmation_preserves_existing_sebi_category(self):
        # Baseline ALREADY has a sebi_category (e.g., from yesterday's run).
        # Even if BSE row has a different/None category, baseline wins for
        # this field -- AMFI feed wobble shouldn't flip a categorised row
        # back to NULL.
        baseline = {
            1: SchemeRow(
                id=1,
                name="Stable Fund",
                isin="INF000STABLE1",
                amfi_code="500",
                type="EQUITY",
                rta="CAMS",
                rta_code="ST",
                amc_code="05",
                sebi_category="Equity Scheme - Large Cap Fund",  # from prior run
                last_seen="2026-05-21",
            )
        }
        bse = {
            500: SchemeRow(
                id=500,
                name="Stable Fund",
                isin="INF000STABLE1",
                amfi_code="500",
                type="EQUITY",
                rta="CAMS",
                rta_code="ST",
                amc_code="05",
                sebi_category=None,  # AMFI didn't cover this ISIN today
                last_seen=None,
            )
        }
        merged = merge_rows(baseline, bse, {}, today="2026-05-22")
        assert merged[0].sebi_category == "Equity Scheme - Large Cap Fund"
        assert merged[0].last_seen == "2026-05-22"


class TestSebiCategoryToTaxType:
    """Tax-type derivation from AMFI section-header SEBI category strings."""

    @pytest.mark.parametrize(
        "category,expected",
        [
            # Equity family -> EQUITY via prefix match
            ("Equity Scheme - Large Cap Fund", "EQUITY"),
            ("Equity Scheme - Mid Cap Fund", "EQUITY"),
            ("Equity Scheme - Small Cap Fund", "EQUITY"),
            ("Equity Scheme - ELSS", "EQUITY"),
            ("Equity Scheme - Sectoral/ Thematic", "EQUITY"),
            ("Equity Scheme - Flexi Cap Fund", "EQUITY"),
            ("Equity Scheme", "EQUITY"),  # close-ended bare label
            # Debt family -> DEBT
            ("Debt Scheme - Banking and PSU Fund", "DEBT"),
            ("Debt Scheme - Liquid Fund", "DEBT"),
            ("Debt Scheme - Overnight Fund", "DEBT"),
            ("Debt Scheme - Gilt Fund", "DEBT"),
            ("Debt Scheme", "DEBT"),  # close-ended bare label
            # Hybrid: per sub-category
            ("Hybrid Scheme - Aggressive Hybrid Fund", "EQUITY"),
            ("Hybrid Scheme - Arbitrage Fund", "EQUITY"),
            ("Hybrid Scheme - Balanced Hybrid Fund", "DEBT"),
            ("Hybrid Scheme - Conservative Hybrid Fund", "DEBT"),
            ("Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage", "EQUITY"),
            ("Hybrid Scheme - Equity Savings", "EQUITY"),
            ("Hybrid Scheme - Multi Asset Allocation", "EQUITY"),
            # Solution Oriented family -> EQUITY via prefix match
            ("Solution Oriented Scheme - Retirement Fund", "EQUITY"),
            ("Solution Oriented Scheme - Children's Fund", "EQUITY"),
            # Other Scheme: per sub-category
            ("Other Scheme - FoF Domestic", "EQUITY"),
            ("Other Scheme - FoF Overseas", "DEBT"),
            ("Other Scheme - Gold ETF", "DEBT"),
            ("Other Scheme - Index Funds", "EQUITY"),
            ("Other Scheme - Other ETFs", "EQUITY"),
            ("Other Scheme - Other  ETFs", "EQUITY"),  # double-space variant in feed
            # Legacy (pre-2018) bare labels
            ("Income", "DEBT"),
            ("Growth", "EQUITY"),
            ("Gilt", "DEBT"),
            ("ELSS", "EQUITY"),
        ],
    )
    def test_known_categories_map_correctly(self, category, expected):
        assert sebi_category_to_tax_type(category) == expected

    def test_none_input_returns_none(self):
        assert sebi_category_to_tax_type(None) is None

    def test_unknown_category_returns_none(self):
        # A category AMFI starts publishing tomorrow that we don't yet handle
        # should return None so the caller falls back to BSE-derived type.
        # Critically: it must NOT raise.
        assert sebi_category_to_tax_type("Cryptocurrency Scheme - Bitcoin Fund") is None
        assert sebi_category_to_tax_type("Some Other New Category") is None


class TestBuildRowsFromBseWithSebi:
    """SEBI category overrides BSE-derived type when both are present."""

    def test_sebi_equity_overrides_bse_debt(self, caplog):
        # BSE labels something "debt" but AMFI section header says it's an
        # Equity Scheme. AMFI wins because SEBI categorisation is the
        # authoritative source for tax classification.
        csv_text = _bse_csv(
            {
                "Unique No": "800",
                "Scheme Code": "MIS-A",
                "AMC Scheme Code": "1",
                "ISIN": "INF000MIS001",
                "AMC Code": "Anything_MF",
                "Scheme Type": "Debt",  # BSE says debt
                "Scheme Name": "Misclassified Fund",
                "RTA Agent Code": "CAMS",
                "Channel Partner Code": "MIS",
            }
        )
        rows, _, _ = build_rows_from_bse(
            [csv_text],
            amfi_mapping={},
            fallback_amfi_map={},
            sebi_categories={"INF000MIS001": "Equity Scheme - Mid Cap Fund"},
        )
        assert rows[800].type == "EQUITY"
        assert rows[800].sebi_category == "Equity Scheme - Mid Cap Fund"

    def test_sebi_unknown_falls_back_to_bse(self):
        # AMFI gave us a category string we don't recognise -- builder must
        # not crash, must fall back to BSE-derived type, must still record
        # the raw SEBI category so we can study it later.
        csv_text = _bse_csv(
            {
                "Unique No": "801",
                "Scheme Code": "UNK-A",
                "AMC Scheme Code": "1",
                "ISIN": "INF000UNK001",
                "AMC Code": "Anything_MF",
                "Scheme Type": "Equity",
                "Scheme Name": "Future Asset Class Fund",
                "RTA Agent Code": "CAMS",
                "Channel Partner Code": "UNK",
            }
        )
        rows, _, _ = build_rows_from_bse(
            [csv_text],
            amfi_mapping={},
            fallback_amfi_map={},
            sebi_categories={"INF000UNK001": "Quantum Entangled Scheme - Mystery"},
        )
        assert rows[801].type == "EQUITY"  # fell back to BSE
        # We still preserve the raw category so future iterations can extend the map.
        assert rows[801].sebi_category == "Quantum Entangled Scheme - Mystery"

    def test_no_sebi_category_keeps_bse_type(self):
        # When AMFI has no entry for the ISIN (e.g., retired scheme not in
        # the live feed), the BSE-derived type is used and sebi_category
        # stays None -- same behaviour as before SEBI integration.
        csv_text = _bse_csv(
            {
                "Unique No": "802",
                "Scheme Code": "OLD-A",
                "AMC Scheme Code": "1",
                "ISIN": "INF000OLD001",
                "AMC Code": "Anything_MF",
                "Scheme Type": "Equity",
                "Scheme Name": "Old Fund",
                "RTA Agent Code": "CAMS",
                "Channel Partner Code": "OLD",
            }
        )
        rows, _, _ = build_rows_from_bse(
            [csv_text],
            amfi_mapping={},
            fallback_amfi_map={},
            sebi_categories={},  # AMFI has nothing for this ISIN
        )
        assert rows[802].type == "EQUITY"
        assert rows[802].sebi_category is None

    def test_default_no_sebi_arg_keeps_prior_behavior(self):
        # Backward-compat: callers that don't pass sebi_categories still work.
        csv_text = _bse_csv(
            {
                "Unique No": "803",
                "Scheme Code": "NOSEBI-A",
                "AMC Scheme Code": "1",
                "ISIN": "INF000NOS001",
                "AMC Code": "Anything_MF",
                "Scheme Type": "Equity",
                "Scheme Name": "No-SEBI Fund",
                "RTA Agent Code": "CAMS",
                "Channel Partner Code": "NOS",
            }
        )
        rows, _, _ = build_rows_from_bse([csv_text], {}, {})
        assert rows[803].type == "EQUITY"
        assert rows[803].sebi_category is None
