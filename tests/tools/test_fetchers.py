"""Unit tests for the pure-parsing pieces of the fetchers.

We don't exercise the HTTP layer here -- network is mocked or skipped at
the orchestrator level.
"""

from __future__ import annotations

import textwrap

from cptools.fetchers.amfi import parse_2018_nav_file, parse_amfi_nav_text
from cptools.fetchers.isin import KEEP_TYPES, parse_isin_csv


def test_parse_amfi_nav_text_extracts_isin_and_reinvest_flag():
    text = "\n".join(
        [
            "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            "",
            "Aditya Birla Sun Life Mutual Fund",
            "",
            (
                "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
                "Scheme Name;Net Asset Value;Date"
            ),
            "100001;INF209K01ABC;INF209K01DEF;Sample Fund - Growth;50.12;20-May-2026",
            "100002;INF209K01GHI;-;Sample Fund - Direct Growth;75.0;20-May-2026",
            "100003;-;INF209K01XYZ;Sample Fund - Reinvest Only;30.0;20-May-2026",
            "",
        ]
    )
    codes, _categories = parse_amfi_nav_text(text)

    # Payout + reinvest pair => first isin is_reinvest=False, second True
    assert codes["INF209K01ABC"] == ("100001", False)
    assert codes["INF209K01DEF"] == ("100001", True)
    # Solo isin -> is_reinvest=None
    assert codes["INF209K01GHI"] == ("100002", None)
    # Reinvest-only -> is_reinvest=True
    assert codes["INF209K01XYZ"] == ("100003", True)


def test_parse_amfi_nav_text_carries_sebi_category_via_section_headers():
    """Section headers should classify every ISIN until the next header arrives."""
    text = "\n".join(
        [
            "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            "",
            "Aditya Birla Sun Life Mutual Fund",
            "",
            "100001;INF000EQUITY01;-;Big Equity Fund;50.12;20-May-2026",
            "100002;INF000EQUITY02;-;Another Equity Fund;25.50;20-May-2026",
            "",
            "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)",
            "",
            "Aditya Birla Sun Life Mutual Fund",
            "",
            "200001;INF000DEBT001;-;Banking PSU Fund;104.38;20-May-2026",
            "",
            "Open Ended Schemes(Hybrid Scheme - Aggressive Hybrid Fund)",
            "",
            "300001;INF000HYBRID1;INF000HYBRID2;Hybrid Aggro Fund;15.0;20-May-2026",
            "",
            "Close Ended Schemes(Equity Scheme)",  # sub-category omitted
            "",
            "400001;INF000CLOSED1;-;Some Closed Fund;12.0;20-May-2026",
            "",
            "Interval Fund Schemes(Debt Scheme - Liquid Fund)",
            "",
            "500001;INF000INTERV1;-;Interval Liquid Fund;10.5;20-May-2026",
        ]
    )
    codes, categories = parse_amfi_nav_text(text)

    # Each ISIN inherits the most-recent section header.
    assert categories["INF000EQUITY01"] == "Equity Scheme - Large Cap Fund"
    assert categories["INF000EQUITY02"] == "Equity Scheme - Large Cap Fund"
    assert categories["INF000DEBT001"] == "Debt Scheme - Banking and PSU Fund"
    # Both isins of the same row carry the category.
    assert categories["INF000HYBRID1"] == "Hybrid Scheme - Aggressive Hybrid Fund"
    assert categories["INF000HYBRID2"] == "Hybrid Scheme - Aggressive Hybrid Fund"
    # Close-ended without a sub-category just exposes the bare label.
    assert categories["INF000CLOSED1"] == "Equity Scheme"
    # Interval Fund Schemes(...) header is also recognised.
    assert categories["INF000INTERV1"] == "Debt Scheme - Liquid Fund"
    # codes still populated for every ISIN.
    assert len(codes) == 7


def test_parse_amfi_nav_text_ignores_unknown_section_header_shapes():
    """A header shape AMFI hasn't used should not silently mis-classify rows."""
    text = "\n".join(
        [
            "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            "",
            "100001;INF000EQUITY01;-;Equity Fund;50.0;20-May-2026",
            "",
            "Some Mysterious Header Without Parens",  # not a header shape we know
            "",
            # The next ISIN should still carry the LAST recognised category,
            # because we deliberately leave current_category unchanged when a
            # header shape doesn't match. Better to keep a slightly-wrong
            # category than to flip everyone to NULL on a wording tweak.
            "100002;INF000EQUITY02;-;Another Equity Fund;25.0;20-May-2026",
        ]
    )
    _codes, categories = parse_amfi_nav_text(text)
    assert categories["INF000EQUITY01"] == "Equity Scheme - Large Cap Fund"
    assert categories["INF000EQUITY02"] == "Equity Scheme - Large Cap Fund"


def test_parse_amfi_nav_text_handles_no_header_before_first_row():
    """Data rows that precede any header should have no category."""
    text = "\n".join(
        [
            "100001;INF000NOHEAD1;-;Orphan Fund;10.0;20-May-2026",
            "",
            "Open Ended Schemes(Equity Scheme - Mid Cap Fund)",
            "",
            "100002;INF000MIDCAP1;-;Mid Cap Fund;20.0;20-May-2026",
        ]
    )
    codes, categories = parse_amfi_nav_text(text)
    assert "INF000NOHEAD1" in codes
    assert "INF000NOHEAD1" not in categories
    assert categories["INF000MIDCAP1"] == "Equity Scheme - Mid Cap Fund"


def test_parse_amfi_nav_text_skips_blank_lines_and_amc_names():
    """AMC names and column headers should be ignored, not crash."""
    text = (
        "Open Ended Schemes(Equity Scheme - Flexi Cap Fund)\n"
        "\n"
        "Some AMC Mutual Fund\n"
        "Scheme Code;ISIN1;ISIN2;name;nav;date\n"  # column header has non-digit first col
    )
    codes, categories = parse_amfi_nav_text(text)
    assert codes == {}
    assert categories == {}


def test_parse_2018_nav_file(tmp_path):
    path = tmp_path / "amfi2018.txt"
    path.write_text(
        textwrap.dedent(
            """\
            Open Ended Schemes(Equity)

            100100;INF000000001;INF000000002;Fund A;120;-;-;31-Jan-2018
            100101;INF000000003;-;Fund B;55;-;-;31-Jan-2018
            100102;INF000000004;INF000000005;Fund C - wrong date;99;-;-;15-Feb-2018
            """
        )
    )
    out = parse_2018_nav_file(path=path)
    # Wrong-date rows skipped
    assert "INF000000004" not in out["codes"]
    # Payout flag on isin1, reinvest on isin2
    assert out["codes"]["INF000000001"] == ("100100", False)
    assert out["codes"]["INF000000002"] == ("100100", True)
    # NAV captured for both isins of the same row
    assert out["navs"]["INF000000001"] == "120"
    assert out["navs"]["INF000000002"] == "120"
    # Single-isin row keeps single nav
    assert out["navs"]["INF000000003"] == "55"


# -----------------------------------------------------------------------------
# captn3m0 ISIN CSV: type filter + casing normalisation
# -----------------------------------------------------------------------------


def _isin_csv(*rows: dict[str, str]) -> str:
    """Render rows as a CSV matching the captn3m0/india-isin-data format."""
    header = "ISIN,Description,Issuer,Type,Status"
    lines = [header]
    for row in rows:
        lines.append(
            ",".join(
                [
                    row["ISIN"],
                    row["Description"],
                    row["Issuer"],
                    row["Type"],
                    row["Status"],
                ]
            )
        )
    return "\n".join(lines)


def test_parse_isin_csv_keeps_in_scope_types():
    csv_text = _isin_csv(
        {
            "ISIN": "INE001A01036",
            "Description": "HDFC LIMITED EQ FV RS 2",
            "Issuer": "HDFC LIMITED",
            "Type": "EQUITY SHARES",
            "Status": "ACTIVE",
        },
        {
            "ISIN": "INE001A07Z47",
            "Description": "HDFC NCD",
            "Issuer": "HDFC LIMITED",
            "Type": "DEBENTURE",
            "Status": "ACTIVE",
        },
        {
            "ISIN": "INE131A04G64",
            "Description": "Some Sovereign Gold Bond",
            "Issuer": "RBI",
            "Type": "SOVEREIGN GOLD BOND",
            "Status": "ACTIVE",
        },
    )
    kept, dropped = parse_isin_csv(csv_text)
    assert len(kept) == 3
    assert dropped == {}


def test_parse_isin_csv_drops_out_of_scope_types():
    csv_text = _isin_csv(
        {
            "ISIN": "INE001A14A04",
            "Description": "HDFC CP",
            "Issuer": "HDFC LIMITED",
            "Type": "COMMERCIAL PAPER",
            "Status": "DELETED",
        },
        {
            "ISIN": "INE001A02XYZ",
            "Description": "Some Bank CD",
            "Issuer": "SOME BANK",
            "Type": "CERTIFICATE OF DEPOSIT",
            "Status": "ACTIVE",
        },
        {
            "ISIN": "INE001A05TBL",
            "Description": "T-Bill 91D",
            "Issuer": "GOI",
            "Type": "TREASURY BILLS",
            "Status": "DELETED",
        },
        {
            "ISIN": "INF001S22001",
            "Description": "Some MF Unit",
            "Issuer": "Some AMC",
            "Type": "MUTUAL FUND UNIT",
            "Status": "ACTIVE",
        },
        {
            "ISIN": "INE099B07001",
            "Description": "Some PTC",
            "Issuer": "Securitisation Trust",
            "Type": "SECURITISED INSTRUMENT",
            "Status": "DELETED",
        },
    )
    kept, dropped = parse_isin_csv(csv_text)
    assert kept == []
    # Drop counts grouped by raw (pre-normalisation) type.
    assert dropped == {
        "COMMERCIAL PAPER": 1,
        "CERTIFICATE OF DEPOSIT": 1,
        "TREASURY BILLS": 1,
        "MUTUAL FUND UNIT": 1,
        "SECURITISED INSTRUMENT": 1,
    }


def test_parse_isin_csv_normalises_type_casing():
    # captn3m0 has a handful of lowercase variants like "Debenture" or
    # "Government Securities" mixed in with the uppercase rows. The fetcher
    # should collapse them to canonical UPPERCASE so the type filter and
    # downstream consumers see one form.
    csv_text = _isin_csv(
        {
            "ISIN": "INE001A07XYZ",
            "Description": "Some Debenture",
            "Issuer": "Some Issuer",
            "Type": "Debenture",  # lowercase variant in source
            "Status": "ACTIVE",
        },
        {
            "ISIN": "IN000125G018",
            "Description": "GOI 6.5% 2025",
            "Issuer": "GOI",
            "Type": "Government Securities",  # lowercase variant
            "Status": "ACTIVE",
        },
    )
    kept, dropped = parse_isin_csv(csv_text)
    assert len(kept) == 2
    assert dropped == {}
    # Both rows now share the canonical UPPERCASE form.
    types = {row[3] for row in kept}
    assert types == {"DEBENTURE", "GOVERNMENT SECURITIES"}


def test_parse_isin_csv_normalises_status_casing():
    csv_text = _isin_csv(
        {
            "ISIN": "INE001A01999",
            "Description": "Some Equity",
            "Issuer": "Some Issuer",
            "Type": "EQUITY SHARES",
            "Status": "Deleted",  # lowercase-y variant in source
        }
    )
    kept, _ = parse_isin_csv(csv_text)
    assert len(kept) == 1
    assert kept[0][4] == "DELETED"


def test_parse_isin_csv_drops_lowercase_out_of_scope_type():
    # "Securitised Instrument" (lowercase) should canonicalise then be
    # filtered out -- the dropped Counter should still see the raw form
    # so the operator can spot the casing-bug source.
    csv_text = _isin_csv(
        {
            "ISIN": "INE099B07999",
            "Description": "Some PTC",
            "Issuer": "Securitisation Trust",
            "Type": "Securitised Instrument",
            "Status": "Deleted",
        }
    )
    kept, dropped = parse_isin_csv(csv_text)
    assert kept == []
    assert dropped == {"Securitised Instrument": 1}


def test_parse_isin_csv_unknown_type_dropped_not_normalised():
    # A type we've never seen before (and isn't in KEEP_TYPES) should be
    # dropped without crashing. Counter records the raw type so the
    # operator can decide whether to add it to KEEP_TYPES or _TYPE_CANONICAL.
    csv_text = _isin_csv(
        {
            "ISIN": "INE001A99ZZZ",
            "Description": "Some Future Instrument",
            "Issuer": "Some Issuer",
            "Type": "QUANTUM ENTANGLED BOND",  # made up
            "Status": "ACTIVE",
        }
    )
    kept, dropped = parse_isin_csv(csv_text)
    assert kept == []
    assert dropped == {"QUANTUM ENTANGLED BOND": 1}


def test_keep_types_is_a_proper_subset_of_all_observed_types():
    # Lock the KEEP_TYPES set down so future edits to the filter list
    # surface in code review. The intent: equities + corporate bonds +
    # govt bonds + AIF/InvIT/REIT + rights/warrants/IDR. NOT money market.
    assert "EQUITY SHARES" in KEEP_TYPES
    assert "DEBENTURE" in KEEP_TYPES
    assert "PREFERENCE SHARES" in KEEP_TYPES
    assert "SOVEREIGN GOLD BOND" in KEEP_TYPES
    assert "ALTERNATIVE INVESTMENT FUND" in KEEP_TYPES
    assert "INFRASTRUCTURE INVESTMENT TRUST" in KEEP_TYPES
    assert "REAL ESTATE INVESTMENT TRUSTS" in KEEP_TYPES

    assert "COMMERCIAL PAPER" not in KEEP_TYPES
    assert "CERTIFICATE OF DEPOSIT" not in KEEP_TYPES
    assert "TREASURY BILLS" not in KEEP_TYPES
    assert "SECURITISED INSTRUMENT" not in KEEP_TYPES
    assert "MUTUAL FUND UNIT" not in KEEP_TYPES
    assert "MUTUAL FUND UNIT (TRASE)" not in KEEP_TYPES
