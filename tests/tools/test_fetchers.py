"""Unit tests for the pure-parsing pieces of the fetchers.

We don't exercise the HTTP layer here -- network is mocked or skipped at
the orchestrator level.
"""

from __future__ import annotations

import textwrap

from cptools.fetchers.amfi import parse_2018_nav_file, parse_amfi_nav_text


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
