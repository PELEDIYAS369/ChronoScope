# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""Unit tests for src/chronoscope/labels/icme.py (no network)."""

from __future__ import annotations

from datetime import datetime, timezone

import pyarrow.parquet as pq
import pytest

from src.chronoscope.labels.icme import (
    clean_float,
    clean_int,
    clean_str,
    html_to_rows,
    is_header_row,
    parse_icme_rows,
    parse_rc_datetime,
    write_icme_labels,
)

UTC = timezone.utc


class TestParseRcDatetime:
    def test_basic(self):
        assert parse_rc_datetime("1996/05/27 1500") == datetime(1996, 5, 27, 15, 0, tzinfo=UTC)

    def test_zero_padded_minutes(self):
        assert parse_rc_datetime("1997/01/10 0104") == datetime(1997, 1, 10, 1, 4, tzinfo=UTC)

    def test_missing(self):
        assert parse_rc_datetime("...") is None
        assert parse_rc_datetime("") is None
        assert parse_rc_datetime(None) is None

    def test_extracts_from_lasco_noise(self):
        # data-gap prefix and parentheses
        assert parse_rc_datetime("dg (1997/11/19 1700)") == datetime(1997, 11, 19, 17, 0, tzinfo=UTC)

    def test_extracts_with_halo_flag(self):
        assert parse_rc_datetime("1996/12/19 1630 H") == datetime(1996, 12, 19, 16, 30, tzinfo=UTC)

    def test_invalid_date_returns_none(self):
        assert parse_rc_datetime("1996/13/45 9999") is None


class TestCleaners:
    def test_clean_float_strips_suffix(self):
        assert clean_float("100 S") == 100.0
        assert clean_float("370") == 370.0
        assert clean_float("-33") == -33.0

    def test_clean_float_missing(self):
        assert clean_float("...") is None
        assert clean_float(None) is None

    def test_clean_int_strips_letter(self):
        assert clean_int("2H") == 2
        assert clean_int("0") == 0

    def test_clean_int_missing(self):
        assert clean_int("...") is None

    def test_clean_str(self):
        assert clean_str("Y") == "Y"
        assert clean_str("...") == ""


class TestIsHeaderRow:
    def test_detects_header(self):
        assert is_header_row("Disturbance Y/M/D (UT) (a)")
        assert is_header_row("something Y/M/D else")

    def test_data_row_not_header(self):
        assert not is_header_row("1996/05/27 1500")


def _r18(disturbance, start, end, qual, dv, v_icme, v_max, b, mc, dst, vt, lasco):
    """Build one 18-cell HTML row in the real column order."""
    cells = [disturbance, start, end, "...", "...", "0", "0", "N", "...",
             qual, dv, v_icme, v_max, b, mc, dst, vt, lasco]
    assert len(cells) == 18
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


_HDR = "<tr>" + "".join(
    f"<th>{h}</th>"
    for h in ["Disturbance", "S", "E", "cs", "ce", "ms", "me", "BDE", "BIF",
              "Qual", "dV", "V_ICME", "V_max", "B", "MC", "Dst", "Vt", "LASCO"]
) + "</tr>"


def _sample_html():
    return (
        "<table>" + _HDR
        + _r18("1996/05/27 1500", "1996/05/27 1500", "1996/05/29 0300",
               "2", "0", "370", "400", "9", "2", "-33", "...", "")
        + _r18("1997/01/10 0104", "1997/01/10 0400", "1997/01/11 0200",
               "1", "100 S", "450", "460", "14", "2", "-78", "507",
               "1997/01/06 1510 H")
        + _HDR  # repeated header mid-table
        + _r18("...", "2024/05/10 1700", "2024/05/12 0000",
               "1", "...", "700 S", "1100", "60", "2", "-412", "...",
               "dg (2024/05/08 1300)")
        + _r18("bad", "...", "...", "1", "...", "...", "...", "...", "...",
               "...", "...", "")  # no interval -> dropped
        + "</table>"
    )


class TestParseIcmeRows:
    def test_count_after_dropping_headers_and_no_interval(self):
        rows = parse_icme_rows(html_to_rows(_sample_html()))
        assert len(rows) == 3

    def test_columns_aligned(self):
        rows = parse_icme_rows(html_to_rows(_sample_html()))
        a = rows[0]
        assert a["v_icme_km_s"] == 370.0
        assert a["v_max_km_s"] == 400.0
        assert a["b_nt"] == 9.0
        assert a["mc_flag"] == 2
        assert a["dst_min_nt"] == -33.0

    def test_missing_shock_kept_when_interval_present(self):
        rows = parse_icme_rows(html_to_rows(_sample_html()))
        g = rows[2]
        assert g["disturbance_time"] is None
        assert g["icme_start"] == datetime(2024, 5, 10, 17, 0, tzinfo=UTC)
        assert g["dst_min_nt"] == -412.0

    def test_lasco_date_extracted_from_noise(self):
        rows = parse_icme_rows(html_to_rows(_sample_html()))
        g = rows[2]
        assert g["lasco_cme_time"] == datetime(2024, 5, 8, 13, 0, tzinfo=UTC)

    def test_source_row_is_sequential(self):
        rows = parse_icme_rows(html_to_rows(_sample_html()))
        assert [r["source_row"] for r in rows] == [1, 2, 3]


class TestWriteIcmeLabels:
    def test_schema_and_roundtrip(self, tmp_path):
        rows = parse_icme_rows(html_to_rows(_sample_html()))
        out = write_icme_labels(rows, tmp_path)
        assert out.exists()
        table = pq.read_table(out)
        assert table.num_rows == 3
        expected_cols = {
            "source_row", "disturbance_time", "icme_start", "icme_end",
            "quality", "v_icme_km_s", "v_max_km_s", "b_nt", "mc_flag",
            "dst_min_nt", "v_transit_km_s", "lasco_cme_time",
        }
        assert set(table.schema.names) == expected_cols

    def test_empty_rows_writes_empty_table(self, tmp_path):
        out = write_icme_labels([], tmp_path)
        assert pq.read_table(out).num_rows == 0


class TestRealLiveTableRows:
    """
    Regression test against the EXACT 19-column rows pandas.read_html produces
    from the live Caltech table (captured via --inspect on 2026-06-07). The
    live table has a trailing always-empty 19th column; LASCO is at index 17.
    """

    _NAN = float("nan")
    REAL_ROWS = [
        ["1996/05/27 1500", "1996/05/27 1500", "1996/05/29 0300", "...", "...",
         "0", "+4", "N", "...", "2", "0", 370.0, 400.0, 9.0, "2", "-33", "...",
         _NAN, _NAN],
        ["1996/12/23 1600", "1996/12/23 1700", "1996/12/25 1100", "...", "...",
         "+10", "0", "N", "...", "2", "20", 360.0, 420.0, 10.0, "2", "-18",
         "435", "1996/12/19 1630 H", _NAN],
        ["1997/01/10 0104", "1997/01/10 0400", "1997/01/11 0200", "...", "...",
         "0", "0", "Y", "...", "1", "100 S", 450.0, 460.0, 14.0, "2", "-78",
         "507", "1997/01/06 1510 H", _NAN],
    ]

    def test_all_real_rows_parse(self):
        rows = parse_icme_rows(self.REAL_ROWS)
        assert len(rows) == 3

    def test_first_row_columns_aligned(self):
        r = parse_icme_rows(self.REAL_ROWS)[0]
        assert r["v_icme_km_s"] == 370.0
        assert r["v_max_km_s"] == 400.0
        assert r["b_nt"] == 9.0
        assert r["mc_flag"] == 2
        assert r["dst_min_nt"] == -33.0
        # cells[16]='...' (no V_transit), cells[17]=nan (no LASCO)
        assert r["v_transit_km_s"] is None
        assert r["lasco_cme_time"] is None

    def test_lasco_and_transit_on_real_row(self):
        r = parse_icme_rows(self.REAL_ROWS)[1]
        assert r["v_transit_km_s"] == 435.0
        assert r["lasco_cme_time"] == datetime(1996, 12, 19, 16, 30, tzinfo=UTC)

    def test_trailing_nan_column_ignored(self):
        # 19-column rows parse fine; the extra trailing column is dropped.
        r = parse_icme_rows(self.REAL_ROWS)[2]
        assert r["dst_min_nt"] == -78.0
        assert r["lasco_cme_time"] == datetime(1997, 1, 6, 15, 10, tzinfo=UTC)
