"""
Unit tests for the corpus storage layer.

Covers:
  * Schema correctness (mag vs plasma have distinct schemas)
  * Round-trip integrity: what we write is what we read back
  * Filtering policy: HAPI fill values dropped, bad DQF dropped
  * Partitioning: rows land in the right year/month directories
  * Idempotency: re-writing the same data doesn't duplicate or corrupt
  * Reader convenience methods (count, time_range)
  * Empty-corpus reader behavior (no files yet → zero rows, no crash)

Uses real pyarrow + duckdb on a tmp directory. These dependencies are
mandatory for the storage layer, so mocking them would defeat the point.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from src.chronoscope.corpus.storage import (
    HAPI_FILL_SENTINEL,
    INSTRUMENT_MAG,
    INSTRUMENT_PLASMA,
    MAG_PARAMETER_KEYS,
    PLASMA_PARAMETER_KEYS,
    CorpusReader,
    WriteReport,
    _schema_for,
    iter_partition_files,
    write_partitioned_parquet,
)
from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR
from src.chronoscope.domain.models import PacketType, TelemetryPacket


# ---------------------------------------------------------------------------
# Packet factories — produce realistic archive packets matching what the
# noaa_dscovr_archive ingester would emit.
# ---------------------------------------------------------------------------


def _make_mag_packet(
    ts: datetime,
    *,
    bx: float = -2.1,
    by: float = 1.3,
    bz: float = -0.8,
    bt: float = 2.6,
    sequence: int = 0,
) -> TelemetryPacket:
    return TelemetryPacket.create(
        spacecraft_id=SPACECRAFT_DSCOVR,
        packet_type=PacketType.TELEMETRY,
        apid=0x65,
        sequence_count=sequence,
        raw_bytes=b"\x00" * 16,
        parameters={
            "bx_gse_nt": bx,
            "by_gse_nt": by,
            "bz_gse_nt": bz,
            "bt_nt": bt,
            "data_type": "magnetic",
            "data_level": "definitive",
            "archive_dataset": "DSCOVR_H0_MAG",
        },
        source="noaa_dscovr_archive",
        timestamp=ts,
    )


def _make_plasma_packet(
    ts: datetime,
    *,
    density: float = 5.2,
    vx: float = -420.0,
    vy: float = 12.5,
    vz: float = -3.2,
    thermal_speed: float = 37.5,
    temperature: float = 85000.0,
    dqf: float = 0.0,
    sequence: int = 0,
) -> TelemetryPacket:
    bulk_speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    return TelemetryPacket.create(
        spacecraft_id=SPACECRAFT_DSCOVR,
        packet_type=PacketType.TELEMETRY,
        apid=0x64,
        sequence_count=sequence,
        raw_bytes=b"\x00" * 12,
        parameters={
            "proton_density_n_cc": density,
            "bulk_speed_km_s": bulk_speed,
            "ion_temperature_k": temperature,
            "vx_gse_km_s": vx,
            "vy_gse_km_s": vy,
            "vz_gse_km_s": vz,
            "thermal_speed_km_s": thermal_speed,
            "data_quality_flag": dqf,
            "data_type": "plasma",
            "data_level": "definitive",
            "archive_dataset": "DSCOVR_H1_FC",
        },
        source="noaa_dscovr_archive",
        timestamp=ts,
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:

    def test_mag_schema_has_expected_columns(self):
        schema = _schema_for(INSTRUMENT_MAG)
        names = set(schema.names)
        # Base columns
        for col in ("timestamp", "packet_id", "spacecraft_id", "apid",
                    "sequence_count", "source", "archive_dataset", "data_level"):
            assert col in names
        # Mag parameters
        for col in MAG_PARAMETER_KEYS:
            assert col in names

    def test_plasma_schema_has_expected_columns(self):
        schema = _schema_for(INSTRUMENT_PLASMA)
        names = set(schema.names)
        for col in PLASMA_PARAMETER_KEYS:
            assert col in names

    def test_mag_and_plasma_schemas_differ(self):
        mag = set(_schema_for(INSTRUMENT_MAG).names)
        plasma = set(_schema_for(INSTRUMENT_PLASMA).names)
        # They share the base columns but have distinct parameter columns
        assert mag != plasma
        assert "bx_gse_nt" in mag and "bx_gse_nt" not in plasma
        assert "proton_density_n_cc" in plasma and \
               "proton_density_n_cc" not in mag

    def test_unknown_instrument_raises(self):
        with pytest.raises(ValueError):
            _schema_for("not_an_instrument")


# ---------------------------------------------------------------------------
# Write basics
# ---------------------------------------------------------------------------


class TestWriteBasics:

    def test_write_empty_iterable_returns_zero_reports(self, tmp_path):
        reports = write_partitioned_parquet([], tmp_path)
        assert reports[INSTRUMENT_MAG].rows_written == 0
        assert reports[INSTRUMENT_PLASMA].rows_written == 0
        # No files should have been created
        assert not (tmp_path / "dscovr").exists()

    def test_write_one_mag_packet_creates_one_file(self, tmp_path):
        pkt = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        reports = write_partitioned_parquet([pkt], tmp_path)
        report = reports[INSTRUMENT_MAG]
        assert report.rows_seen == 1
        assert report.rows_written == 1
        assert len(report.files_written) == 1
        assert report.files_written[0].exists()
        assert report.files_written[0].suffix == ".parquet"

    def test_write_returns_both_reports_even_when_only_one_used(self, tmp_path):
        """Caller-side aggregation is simpler when both keys always exist."""
        pkt = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        reports = write_partitioned_parquet([pkt], tmp_path)
        assert INSTRUMENT_MAG in reports
        assert INSTRUMENT_PLASMA in reports
        assert reports[INSTRUMENT_PLASMA].rows_seen == 0


# ---------------------------------------------------------------------------
# Round-trip integrity
# ---------------------------------------------------------------------------


class TestRoundTrip:

    def test_mag_values_preserved_through_write_then_read(self, tmp_path):
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        pkt = _make_mag_packet(ts, bx=-3.14, by=2.71, bz=1.41, bt=4.5)
        write_partitioned_parquet([pkt], tmp_path)

        with CorpusReader(tmp_path) as reader:
            rows = reader.query("SELECT bx_gse_nt, by_gse_nt, bz_gse_nt, bt_nt FROM mag")
        assert len(rows) == 1
        bx, by, bz, bt = rows[0]
        assert bx == pytest.approx(-3.14)
        assert by == pytest.approx(2.71)
        assert bz == pytest.approx(1.41)
        assert bt == pytest.approx(4.5)

    def test_plasma_values_preserved_through_round_trip(self, tmp_path):
        ts = datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        pkt = _make_plasma_packet(
            ts, density=7.7, vx=-500.0, vy=10.0, vz=0.0,
            thermal_speed=45.0, temperature=100000.0, dqf=0.0,
        )
        write_partitioned_parquet([pkt], tmp_path)

        with CorpusReader(tmp_path) as reader:
            rows = reader.query(
                "SELECT proton_density_n_cc, ion_temperature_k, "
                "vx_gse_km_s, data_quality_flag FROM plasma"
            )
        assert len(rows) == 1
        density, temp, vx, dqf = rows[0]
        assert density == pytest.approx(7.7)
        assert temp == pytest.approx(100000.0)
        assert vx == pytest.approx(-500.0)
        assert dqf == 0.0

    def test_timestamp_round_trip_preserves_utc(self, tmp_path):
        ts = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        pkt = _make_mag_packet(ts)
        write_partitioned_parquet([pkt], tmp_path)

        with CorpusReader(tmp_path) as reader:
            rows = reader.query("SELECT timestamp FROM mag")
        # DuckDB returns timestamps as datetime objects. The read-back may be
        # naive (UTC implied) — normalize before comparing.
        read_ts = rows[0][0]
        if read_ts.tzinfo is None:
            read_ts = read_ts.replace(tzinfo=timezone.utc)
        assert read_ts == ts


# ---------------------------------------------------------------------------
# Filtering: fill values and DQF
# ---------------------------------------------------------------------------


class TestFillValueFilter:

    def test_mag_row_with_fill_value_in_any_column_dropped(self, tmp_path):
        ts = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        good = _make_mag_packet(ts)
        bad = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 1, tzinfo=timezone.utc),
            bz=HAPI_FILL_SENTINEL,
        )
        reports = write_partitioned_parquet([good, bad], tmp_path)
        report = reports[INSTRUMENT_MAG]
        assert report.rows_seen == 2
        assert report.rows_written == 1
        assert report.rows_dropped_fill == 1

    def test_plasma_row_with_fill_value_dropped(self, tmp_path):
        good = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        bad = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 1, 0, tzinfo=timezone.utc),
            density=HAPI_FILL_SENTINEL,
        )
        reports = write_partitioned_parquet([good, bad], tmp_path)
        report = reports[INSTRUMENT_PLASMA]
        assert report.rows_written == 1
        assert report.rows_dropped_fill == 1

    def test_fill_filter_can_be_disabled(self, tmp_path):
        """For data-exploration callers who want to see raw fill rates.

        Note: the fill sentinel (-1e31) is ALSO outside the physical bounds,
        so to get true raw passthrough we must disable the plausibility
        filter too (DEC-007). This test isolates the fill-filter toggle.
        """
        good = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        bad = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 1, tzinfo=timezone.utc),
            bz=HAPI_FILL_SENTINEL,
        )
        reports = write_partitioned_parquet(
            [good, bad],
            tmp_path,
            apply_fill_filter=False,
            apply_plausibility_filter=False,
        )
        report = reports[INSTRUMENT_MAG]
        # Both written when both filters disabled
        assert report.rows_written == 2
        assert report.rows_dropped_fill == 0
        assert report.rows_dropped_implausible == 0

    def test_nan_treated_as_fill(self, tmp_path):
        """NaN shouldn't appear in clean data, but if it does, drop it."""
        bad = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            bz=float("nan"),
        )
        reports = write_partitioned_parquet([bad], tmp_path)
        report = reports[INSTRUMENT_MAG]
        assert report.rows_written == 0
        assert report.rows_dropped_fill == 1


class TestDqfFilter:

    def test_plasma_row_with_nonzero_dqf_dropped(self, tmp_path):
        good = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc), dqf=0.0,
        )
        bad = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 1, 0, tzinfo=timezone.utc), dqf=1.0,
        )
        reports = write_partitioned_parquet([good, bad], tmp_path)
        report = reports[INSTRUMENT_PLASMA]
        assert report.rows_written == 1
        assert report.rows_dropped_dqf == 1

    def test_dqf_filter_does_not_affect_mag(self, tmp_path):
        """MAG has no DQF; filter must not accidentally drop mag rows."""
        pkt = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        reports = write_partitioned_parquet([pkt], tmp_path)
        assert reports[INSTRUMENT_MAG].rows_written == 1
        assert reports[INSTRUMENT_MAG].rows_dropped_dqf == 0

    def test_dqf_filter_can_be_disabled(self, tmp_path):
        bad = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc), dqf=2.0,
        )
        reports = write_partitioned_parquet(
            [bad], tmp_path, apply_dqf_filter=False,
        )
        assert reports[INSTRUMENT_PLASMA].rows_written == 1


# ---------------------------------------------------------------------------
# Physical-plausibility filter (DEC-007)
# ---------------------------------------------------------------------------


class TestPlausibilityFilter:

    def test_negative_density_dropped(self, tmp_path):
        """Negative proton density is physically impossible -> dropped."""
        good = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc), density=5.2
        )
        bad = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 1, 0, tzinfo=timezone.utc), density=-82.3
        )
        reports = write_partitioned_parquet([good, bad], tmp_path)
        p = reports[INSTRUMENT_PLASMA]
        assert p.rows_seen == 2
        assert p.rows_written == 1
        assert p.rows_dropped_implausible == 1

    def test_absurd_density_spike_dropped(self, tmp_path):
        """A 3.5e10 cm^-3 spike (real corpus garbage) -> dropped."""
        good = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc), density=5.2
        )
        bad = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 1, 0, tzinfo=timezone.utc),
            density=3.5727e10,
        )
        reports = write_partitioned_parquet([good, bad], tmp_path)
        p = reports[INSTRUMENT_PLASMA]
        assert p.rows_written == 1
        assert p.rows_dropped_implausible == 1

    def test_high_but_valid_density_kept(self, tmp_path):
        """150 cm^-3 is high but within the generous 200 bound -> kept."""
        pkt = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc), density=150.0
        )
        reports = write_partitioned_parquet([pkt], tmp_path)
        p = reports[INSTRUMENT_PLASMA]
        assert p.rows_written == 1
        assert p.rows_dropped_implausible == 0

    def test_density_just_over_bound_dropped(self, tmp_path):
        """250 cm^-3 exceeds the 200 ceiling -> dropped."""
        pkt = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc), density=250.0
        )
        reports = write_partitioned_parquet([pkt], tmp_path)
        p = reports[INSTRUMENT_PLASMA]
        assert p.rows_written == 0
        assert p.rows_dropped_implausible == 1

    def test_implausible_speed_dropped(self, tmp_path):
        """Bulk speed outside [150,1500] km/s -> dropped."""
        # vx chosen so the magnitude is ~2000 km/s, well past the ceiling.
        pkt = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc),
            vx=-2000.0, vy=0.0, vz=0.0,
        )
        reports = write_partitioned_parquet([pkt], tmp_path)
        p = reports[INSTRUMENT_PLASMA]
        assert p.rows_written == 0
        assert p.rows_dropped_implausible == 1

    def test_plausibility_filter_can_be_disabled(self, tmp_path):
        """Exploration callers can opt out and see raw values."""
        bad = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc),
            density=3.5727e10,
        )
        reports = write_partitioned_parquet(
            [bad], tmp_path, apply_plausibility_filter=False
        )
        p = reports[INSTRUMENT_PLASMA]
        assert p.rows_written == 1
        assert p.rows_dropped_implausible == 0

    def test_mag_implausible_component_dropped(self, tmp_path):
        """A MAG component outside +/-500 nT -> dropped."""
        pkt = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc), bz=9999.0
        )
        reports = write_partitioned_parquet([pkt], tmp_path)
        m = reports[INSTRUMENT_MAG]
        assert m.rows_written == 0
        assert m.rows_dropped_implausible == 1


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


class TestPartitioning:

    def test_rows_land_in_correct_year_month_directory(self, tmp_path):
        pkt = _make_mag_packet(
            datetime(2018, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        write_partitioned_parquet([pkt], tmp_path)
        expected_dir = (
            tmp_path / "dscovr" / "mag" / "year=2018" / "month=07"
        )
        assert expected_dir.exists()
        files = list(expected_dir.glob("*.parquet"))
        assert len(files) == 1

    def test_rows_in_different_months_go_to_different_files(self, tmp_path):
        jan = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        feb = _make_mag_packet(
            datetime(2024, 2, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        reports = write_partitioned_parquet([jan, feb], tmp_path)
        report = reports[INSTRUMENT_MAG]
        assert report.rows_written == 2
        assert len(report.files_written) == 2
        # Different partition directories
        parents = {f.parent for f in report.files_written}
        assert len(parents) == 2

    def test_mag_and_plasma_isolated_in_separate_trees(self, tmp_path):
        mag = _make_mag_packet(
            datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        plasma = _make_plasma_packet(
            datetime(2018, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        )
        write_partitioned_parquet([mag, plasma], tmp_path)
        assert (tmp_path / "dscovr" / "mag").exists()
        assert (tmp_path / "dscovr" / "plasma").exists()
        # Plasma tree must not contain mag data and vice versa
        with CorpusReader(tmp_path) as reader:
            assert reader.count("mag") == 1
            assert reader.count("plasma") == 1

    def test_iter_partition_files_returns_sorted_files(self, tmp_path):
        # Write packets across 3 months
        packets = [
            _make_mag_packet(
                datetime(2024, m, 15, 12, 0, 0, tzinfo=timezone.utc)
            )
            for m in (1, 2, 3)
        ]
        write_partitioned_parquet(packets, tmp_path)
        files = list(iter_partition_files(tmp_path, "mag"))
        assert len(files) == 3
        assert files == sorted(files)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:

    def test_rewriting_same_packets_does_not_duplicate(self, tmp_path):
        """
        Critical for resumable bulk backfill: writing the same time window
        twice must not produce two copies of every row.
        """
        ts_list = [
            datetime(2024, 1, 15, 12, m, 0, tzinfo=timezone.utc)
            for m in range(5)
        ]
        packets = [_make_mag_packet(ts) for ts in ts_list]

        write_partitioned_parquet(packets, tmp_path)
        write_partitioned_parquet(packets, tmp_path)

        with CorpusReader(tmp_path) as reader:
            assert reader.count("mag") == 5


# ---------------------------------------------------------------------------
# Unknown / unclassified packets
# ---------------------------------------------------------------------------


class TestUnclassifiedPackets:

    def test_packet_with_no_data_type_is_skipped_not_misfiled(self, tmp_path):
        """
        A packet without `data_type` shouldn't end up in either tree. Better
        to lose it than to misfile it.
        """
        weird = TelemetryPacket.create(
            spacecraft_id=SPACECRAFT_DSCOVR,
            packet_type=PacketType.TELEMETRY,
            apid=0x99,
            sequence_count=0,
            raw_bytes=b"\x00" * 4,
            parameters={"some_other_field": 1.0},  # no data_type
            source="test",
            timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        reports = write_partitioned_parquet([weird], tmp_path)
        # Neither instrument should have written this packet
        assert reports[INSTRUMENT_MAG].rows_written == 0
        assert reports[INSTRUMENT_PLASMA].rows_written == 0
        # And no files anywhere
        assert not (tmp_path / "dscovr").exists()


# ---------------------------------------------------------------------------
# CorpusReader
# ---------------------------------------------------------------------------


class TestCorpusReader:

    def test_reader_on_empty_corpus_returns_zero_counts(self, tmp_path):
        """Querying a fresh, empty corpus must not crash."""
        with CorpusReader(tmp_path) as reader:
            assert reader.count("mag") == 0
            assert reader.count("plasma") == 0
            assert reader.time_range("mag") is None

    def test_time_range_returns_min_max(self, tmp_path):
        first = _make_mag_packet(
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        )
        last = _make_mag_packet(
            datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
        )
        write_partitioned_parquet([first, last], tmp_path)
        with CorpusReader(tmp_path) as reader:
            time_range = reader.time_range("mag")
        assert time_range is not None
        min_ts, max_ts = time_range
        # Normalize tz for comparison
        if min_ts.tzinfo is None:
            min_ts = min_ts.replace(tzinfo=timezone.utc)
        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=timezone.utc)
        assert min_ts == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert max_ts == datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)

    def test_reader_supports_arbitrary_sql(self, tmp_path):
        # Three mag packets with known Bz values: -1.0, 0.0, 1.0
        packets = [
            _make_mag_packet(
                datetime(2024, 1, 15, 12, 0, i, tzinfo=timezone.utc),
                bz=bz,
            )
            for i, bz in enumerate([-1.0, 0.0, 1.0])
        ]
        write_partitioned_parquet(packets, tmp_path)
        with CorpusReader(tmp_path) as reader:
            negative_count = reader.query(
                "SELECT COUNT(*) FROM mag WHERE bz_gse_nt < 0"
            )
        assert negative_count[0][0] == 1


# ---------------------------------------------------------------------------
# WriteReport
# ---------------------------------------------------------------------------


class TestWriteReport:

    def test_drop_rate_zero_when_no_rows_seen(self):
        report = WriteReport(instrument="mag")
        assert report.drop_rate == 0.0

    def test_drop_rate_computed_from_seen_minus_written(self):
        report = WriteReport(
            instrument="plasma",
            rows_seen=10,
            rows_written=7,
            rows_dropped_fill=2,
            rows_dropped_dqf=1,
        )
        assert report.drop_rate == pytest.approx(0.3)
