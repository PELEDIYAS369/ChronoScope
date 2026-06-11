"""Unit tests for the labeled training feature-matrix builder."""

from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.chronoscope.corpus.storage import write_partitioned_parquet
from src.chronoscope.corpus.training import build_hourly_features, _summary
from src.chronoscope.domain.models import PacketType, TelemetryPacket
from src.chronoscope.labels.icme import parse_icme_rows, write_icme_labels

UTC = timezone.utc


def _mag(ts, bz, bt):
    return TelemetryPacket.create(
        spacecraft_id="DSCOVR", packet_type=PacketType.TELEMETRY, apid=0x65,
        sequence_count=0, raw_bytes=b"0" * 16, source="t", timestamp=ts,
        parameters={"bx_gse_nt": 1.0, "by_gse_nt": 1.0, "bz_gse_nt": bz, "bt_nt": bt,
                    "data_type": "magnetic", "data_level": "definitive",
                    "archive_dataset": "DSCOVR_H0_MAG"},
    )


def _plasma(ts, speed, dens, temp):
    return TelemetryPacket.create(
        spacecraft_id="DSCOVR", packet_type=PacketType.TELEMETRY, apid=0x66,
        sequence_count=0, raw_bytes=b"0" * 16, source="t", timestamp=ts,
        parameters={"proton_density_n_cc": dens, "bulk_speed_km_s": speed,
                    "ion_temperature_k": temp, "vx_gse_km_s": -speed, "vy_gse_km_s": 0.0,
                    "vz_gse_km_s": 0.0, "thermal_speed_km_s": 30.0, "data_quality_flag": 0,
                    "data_type": "plasma", "data_level": "definitive",
                    "archive_dataset": "DSCOVR_H1_FC"},
    )


def _write_kp(root):
    """3-hourly Kp: 00:00 block = Kp 8 (G4), 03:00 block = Kp 2 (quiet)."""
    d = root / "labels" / "geomagnetic"
    d.mkdir(parents=True, exist_ok=True)
    tbl = pa.table({
        "timestamp": pa.array(
            [datetime(2017, 9, 8, 0, tzinfo=UTC), datetime(2017, 9, 8, 3, tzinfo=UTC)],
            type=pa.timestamp("us", tz="UTC"),
        ),
        "kp": pa.array([8.0, 2.0], type=pa.float64()),
        "ap": pa.array([207, 7], type=pa.int32()),
        "g_scale": pa.array([4, 0], type=pa.int8()),
        "status": pa.array(["def", "def"]),
    })
    pq.write_table(tbl, d / "kp_ap.parquet")


def _write_icme(root):
    """One ICME interval 00:30..01:30 (covers the 01:00 bucket only)."""
    nan = float("nan")
    row = ["2017/09/08 0030", "2017/09/08 0030", "2017/09/08 0130", "...", "...",
           "0", "0", "Y", "...", "1", "...", 700.0, 800.0, 30.0, "2", "-142", "...",
           nan, nan]
    write_icme_labels(parse_icme_rows([row]), root)


def _build_labeled_corpus(root, *, with_kp=True, with_icme=True):
    pkts = []
    # populated hours 00,01,02 (southward) and 03,05 (quiet); 04 left as a gap
    for h, bz in [(0, -30), (1, -25), (2, -20), (3, -3), (5, -1)]:
        pkts += [_mag(datetime(2017, 9, 8, h, 0, 0, tzinfo=UTC), bz, abs(bz) + 2),
                 _mag(datetime(2017, 9, 8, h, 0, 1, tzinfo=UTC), bz + 2, abs(bz) + 1)]
    # plasma only in hour 00
    pkts += [_plasma(datetime(2017, 9, 8, 0, 0, 0, tzinfo=UTC), 600, 10, 1e5),
             _plasma(datetime(2017, 9, 8, 0, 0, 30, tzinfo=UTC), 620, 12, 1.1e5)]
    write_partitioned_parquet(pkts, root)
    if with_kp:
        _write_kp(root)
    if with_icme:
        _write_icme(root)


def _load(out):
    return pq.read_table(out).to_pandas().sort_values("timestamp").reset_index(drop=True)


class TestBuildHourlyFeatures:
    def test_schema(self, tmp_path):
        _build_labeled_corpus(tmp_path)
        df = _load(build_hourly_features(tmp_path))
        assert set(df.columns) == {
            "timestamp", "bz_mean", "bz_min", "bt_mean", "bt_max", "mag_n",
            "sw_speed_mean", "sw_speed_max", "density_mean", "temp_mean", "plasma_n",
            "kp", "g_scale", "in_icme", "icme_mc_flag", "icme_dst_min",
        }

    def test_spine_is_regular_including_gap(self, tmp_path):
        _build_labeled_corpus(tmp_path)
        df = _load(build_hourly_features(tmp_path))
        # spine spans 00:00..05:00 = 6 hourly rows even though 04:00 has no telemetry
        assert len(df) == 6
        gap = df[df["timestamp"].dt.hour == 4].iloc[0]
        assert gap["mag_n"] == 0
        assert gap["bz_min"] != gap["bz_min"]  # NaN

    def test_asof_kp_fills_every_hour(self, tmp_path):
        _build_labeled_corpus(tmp_path)
        df = _load(build_hourly_features(tmp_path))
        by_hour = {int(t.hour): r for t, r in zip(df["timestamp"], df.to_dict("records"))}
        assert by_hour[0]["kp"] == 8.0 and by_hour[2]["kp"] == 8.0
        assert by_hour[3]["kp"] == 2.0
        assert by_hour[4]["kp"] == 2.0  # gap hour still labeled via ASOF

    def test_in_icme_flag_and_attributes(self, tmp_path):
        _build_labeled_corpus(tmp_path)
        df = _load(build_hourly_features(tmp_path))
        by_hour = {int(t.hour): r for t, r in zip(df["timestamp"], df.to_dict("records"))}
        assert by_hour[1]["in_icme"] is True or by_hour[1]["in_icme"] == True  # noqa: E712
        assert by_hour[1]["icme_dst_min"] == -142.0
        assert by_hour[1]["icme_mc_flag"] == 2
        assert by_hour[0]["in_icme"] == False  # noqa: E712
        assert by_hour[2]["in_icme"] == False  # noqa: E712

    def test_plasma_present_only_where_data_exists(self, tmp_path):
        _build_labeled_corpus(tmp_path)
        df = _load(build_hourly_features(tmp_path))
        by_hour = {int(t.hour): r for t, r in zip(df["timestamp"], df.to_dict("records"))}
        assert by_hour[0]["plasma_n"] == 2
        assert abs(by_hour[0]["sw_speed_mean"] - 610.0) < 1e-6
        assert by_hour[1]["plasma_n"] == 0
        assert by_hour[1]["sw_speed_mean"] != by_hour[1]["sw_speed_mean"]  # NaN

    def test_southward_bz_correlates_negatively_with_kp(self, tmp_path):
        _build_labeled_corpus(tmp_path)
        out = build_hourly_features(tmp_path)
        s = _summary(tmp_path, out)
        assert s["corr_bzmin_kp"] < -0.8

    def test_requires_both_label_sets(self, tmp_path):
        _build_labeled_corpus(tmp_path, with_kp=False, with_icme=False)
        with pytest.raises(RuntimeError, match="kp"):
            build_hourly_features(tmp_path)

    def test_invalid_cadence_rejected(self, tmp_path):
        _build_labeled_corpus(tmp_path)
        with pytest.raises(ValueError, match="cadence"):
            build_hourly_features(tmp_path, cadence="fortnight")
