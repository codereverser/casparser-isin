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
            "Open Ended Schemes(Equity)",
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
    data = parse_amfi_nav_text(text)

    # Payout + reinvest pair => first isin is_reinvest=False, second True
    assert data["INF209K01ABC"] == ("100001", False)
    assert data["INF209K01DEF"] == ("100001", True)
    # Solo isin -> is_reinvest=None
    assert data["INF209K01GHI"] == ("100002", None)
    # Reinvest-only -> is_reinvest=True
    assert data["INF209K01XYZ"] == ("100003", True)


def test_parse_amfi_nav_text_skips_section_headers_and_blank_lines():
    text = (
        "Open Ended Schemes\n"
        "\n"
        "Some AMC Mutual Fund\n"
        "Scheme Code;ISIN1;ISIN2;name;nav;date\n"  # header has non-digit first col
    )
    assert parse_amfi_nav_text(text) == {}


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
