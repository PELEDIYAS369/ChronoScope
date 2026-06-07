# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""Unit tests for src/chronoscope/labels/geomagnetic.py (no network)."""

from __future__ import annotations

from datetime import datetime, timezone

import pyarrow.parquet as pq
import pytest

from src.chronoscope.labels.geomagnetic import (
    GFZ_MISSING,
    derive_g_scale,
    join_kp_ap,
    parse_gfz_json,
    rows_to_table,
    write_kp_labels,
)


class TestDeriveGScale:
    @pytest.mark.parametrize(
        "kp,expected",
        [
            (0.0, 0),
            (3.667, 0),
            (4.333, 0),   # 4+ -> no storm
            (4.667, 1),   # 5- rounds to Kp level 5 -> G1 (the subtle case)
            (5.0, 1),     # 5o -> G1
            (5.333, 1),   # 5+ -> G1
            (5.667, 2),   # 6- -> G2
            (6.0, 2),
            (7.0, 3),     # G3
            (8.0, 4),     # G4
            (8.333, 4),
            (8.667, 5),   # 9- rounds to 9 -> G5
            (9.0, 5),     # G5
        ],
    )
    def test_mapping(self, kp, expected):
        assert derive_g_scale(kp) == expected

    def test_g_scale_capped_at_5(self):
        # Even an out-of-range high Kp can't exceed G5.
        assert derive_g_scale(10.0) == 5


class TestParseGfzJson:
    def _payload(self):
        return {
            "Kp": [2.0, 4.667, 8.0],
            "datetime": [
                "2017-09-07T00:00:00Z",
                "2017-09-07T12:00:00Z",
                "2017-09-08T00:00:00Z",
            ],
            "status": ["def", "def", "def"],
            "meta": {"license": "CC BY 4.0", "source": "GFZ Potsdam"},
        }

    def test_parses_parallel_arrays(self):
        by_time = parse_gfz_json(self._payload(), "Kp")
        assert len(by_time) == 3

    def test_timestamps_are_utc_aware(self):
        by_time = parse_gfz_json(self._payload(), "Kp")
        ts = next(iter(by_time))
        assert ts.tzinfo is not None
        assert ts.utcoffset().total_seconds() == 0

    def test_missing_index_key_raises(self):
        with pytest.raises(ValueError):
            parse_gfz_json({"datetime": []}, "Kp")

    def test_length_mismatch_raises(self):
        bad = {"Kp": [1.0, 2.0], "datetime": ["2017-09-07T00:00:00Z"]}
        with pytest.raises(ValueError):
            parse_gfz_json(bad, "Kp")


class TestJoinKpAp:
    def _make(self):
        times = [
            datetime(2017, 9, 7, 0, tzinfo=timezone.utc),
            datetime(2017, 9, 7, 12, tzinfo=timezone.utc),
            datetime(2017, 9, 8, 0, tzinfo=timezone.utc),
            datetime(2017, 9, 8, 3, tzinfo=timezone.utc),
        ]
        kp = dict(zip(times, [2.0, 4.667, 8.0, GFZ_MISSING]))
        ap = dict(zip(times, [7, 39, 207, GFZ_MISSING]))
        return times, kp, ap

    def test_missing_kp_row_dropped(self):
        _, kp, ap = self._make()
        rows = join_kp_ap(kp, ap)
        assert len(rows) == 3  # the -1 Kp interval is dropped

    def test_rows_sorted_by_time(self):
        _, kp, ap = self._make()
        rows = join_kp_ap(kp, ap)
        ts = [r["timestamp"] for r in rows]
        assert ts == sorted(ts)

    def test_g_scale_and_ap_attached(self):
        _, kp, ap = self._make()
        rows = join_kp_ap(kp, ap)
        g4 = [r for r in rows if r["kp"] == 8.0][0]
        assert g4["g_scale"] == 4
        assert g4["ap"] == 207

    def test_missing_ap_kept_with_sentinel(self):
        t = datetime(2017, 9, 7, 0, tzinfo=timezone.utc)
        rows = join_kp_ap({t: 5.0}, {t: GFZ_MISSING})
        assert len(rows) == 1
        assert rows[0]["ap"] == GFZ_MISSING
        assert rows[0]["g_scale"] == 1  # Kp still drives g_scale


class TestWriteKpLabels:
    def test_schema_and_roundtrip(self, tmp_path):
        t = datetime(2017, 9, 8, 0, tzinfo=timezone.utc)
        rows = join_kp_ap({t: 8.0}, {t: 207})
        out = write_kp_labels(rows, tmp_path)
        assert out.exists()
        table = pq.read_table(out)
        assert table.num_rows == 1
        assert table.column("g_scale").to_pylist() == [4]
        assert set(table.schema.names) == {
            "timestamp", "kp", "ap", "g_scale", "status"
        }

    def test_empty_rows_writes_empty_table(self, tmp_path):
        out = write_kp_labels([], tmp_path)
        assert out.exists()
        assert pq.read_table(out).num_rows == 0
