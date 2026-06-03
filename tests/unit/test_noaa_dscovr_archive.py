"""
Unit tests for the NOAA DSCOVR historical archive ingester.

Uses mocked HAPI responses so the tests run without network access — the
production sandbox typically can't reach NASA/NOAA domains, and CI must be
hermetic. Mock CSV bodies follow the documented column order for
DSCOVR_H0_MAG (1-sec MAG) and DSCOVR_H1_FC (1-min Faraday Cup plasma).
"""

import math
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR
from src.chronoscope.domain.models import MissionSession, MissionPhase, PacketType
from src.chronoscope.ingestion.noaa_dscovr_archive import (
    APID_MAGNETIC,
    APID_PLASMA,
    DATASET_FC,
    DATASET_MAG,
    DSCOVR_H1_FC_END_DATE,
    DSCOVR_OPERATIONAL_DATE,
    NOAADscovrArchiveIngester,
    _parse_hapi_time,
    _to_hapi_time,
)


# ---------------------------------------------------------------------------
# Fixture CSV bodies — HAPI default (no header), comma-separated.
# Column orders match the VERIFIED CDAWeb /info responses from 2026-05-26.
#
# DSCOVR_H1_FC columns (14 total): Time, DQF, V_GSE[0..2], V_GSE_ErrorBars[0..2],
#   THERMAL_SPD, THERMAL_SPD_ErrorBars, Np, Np_ErrorBars, THERMAL_TEMP,
#   THERMAL_TEMP_ErrorBars.
# DSCOVR_H0_MAG columns (15 total): Time, B1F1, B1SDF1, B1GSE[0..2],
#   B1SDGSE[0..2], B1RTN[0..2], B1SDRTN[0..2].
# ---------------------------------------------------------------------------

# Three plasma rows. Values chosen to produce easily-asserted derived quantities:
#  V_GSE = (-420.0, 12.5, -3.2)  -> bulk_speed = sqrt(...)
#  DQF = 0 (good)
#  Np = 5.2 cm^-3
#  THERMAL_TEMP = 85000 K  (published in K, used directly)
#  THERMAL_SPD = 37.5 km/s (kept as a separate parameter)
# Error-bar columns are filled with 0.5 (arbitrary, parser ignores them).
MOCK_FC_CSV = (
    "2018-03-15T12:00:00.000Z,0,-420.0,12.5,-3.2,0.5,0.5,0.5,37.5,0.5,5.2,0.5,85000.0,0.5\n"
    "2018-03-15T12:01:00.000Z,0,-425.0,11.8,-2.9,0.5,0.5,0.5,38.1,0.5,5.4,0.5,86000.0,0.5\n"
    "2018-03-15T12:02:00.000Z,0,-418.0,13.1,-3.4,0.5,0.5,0.5,37.0,0.5,5.1,0.5,84000.0,0.5\n"
)

# Three MAG rows. Values:
#  B1F1 = 2.6 nT magnitude
#  B1GSE = (-2.1, 1.3, -0.8) nT
# All stddev / RTN columns set to 0.1 (parser ignores them; tests assert
# they don't leak into the parameters dict).
MOCK_MAG_CSV = (
    "2024-01-15T12:00:00.000Z,2.6,0.1,-2.1,1.3,-0.8,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1\n"
    "2024-01-15T12:00:01.000Z,2.7,0.1,-2.3,1.1,-0.9,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1\n"
    "2024-01-15T12:00:02.000Z,2.5,0.1,-2.0,1.4,-0.7,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1\n"
)

# Time windows.
# Plasma window: inside the H1_FC coverage period (2016-06-03 → 2019-06-27).
PLASMA_START = datetime(2018, 3, 15, 11, 0, 0, tzinfo=timezone.utc)
PLASMA_END = datetime(2018, 3, 15, 13, 0, 0, tzinfo=timezone.utc)
# MAG window: H0_MAG covers 2015 → present, so 2024 is fine.
MAG_START = datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)
MAG_END = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
# Backwards-compatible aliases used by tests that don't care which dataset.
# These point at the plasma window so combined fetches still work.
START = PLASMA_START
END = PLASMA_END


def _make_csv_response(body: str) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.text = body
    mock.raise_for_status.return_value = None
    return mock


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


class TestHapiTimeHelpers:

    def test_to_hapi_time_naive_assumed_utc(self):
        t = datetime(2024, 1, 15, 12, 0, 0)
        s = _to_hapi_time(t)
        assert s.startswith("2024-01-15T12:00:00")
        assert s.endswith("Z")

    def test_to_hapi_time_round_trip(self):
        t = datetime(2024, 1, 15, 12, 30, 45, 123000, tzinfo=timezone.utc)
        round_tripped = _parse_hapi_time(_to_hapi_time(t))
        assert round_tripped == t

    def test_parse_hapi_time_with_milliseconds(self):
        t = _parse_hapi_time("2024-01-15T12:00:00.000Z")
        assert t == datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_parse_hapi_time_without_milliseconds(self):
        t = _parse_hapi_time("2024-01-15T12:00:00Z")
        assert t == datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_parse_hapi_time_rejects_garbage(self):
        from src.chronoscope.domain.exceptions import PacketParseError
        with pytest.raises(PacketParseError):
            _parse_hapi_time("not a timestamp")

    def test_parse_hapi_time_with_nanoseconds(self):
        # Regression: CDAWeb emits DSCOVR_H1_FC timestamps with 9 fractional
        # digits (nanoseconds). strptime %f only accepts 6, so the original
        # parser raised on every plasma row and silently zeroed plasma ingest.
        t = _parse_hapi_time('2018-03-15T00:00:00.000000000Z')
        assert t == datetime(2018, 3, 15, 0, 0, 0, tzinfo=timezone.utc)

    def test_parse_hapi_time_nanoseconds_with_nonzero_fraction(self):
        # 123456789 ns -> 123456 us (truncated, not rounded).
        t = _parse_hapi_time('2018-03-15T12:34:56.123456789Z')
        assert t == datetime(2018, 3, 15, 12, 34, 56, 123456, tzinfo=timezone.utc)

    def test_parse_hapi_time_microseconds(self):
        t = _parse_hapi_time('2018-03-15T12:34:56.123456Z')
        assert t == datetime(2018, 3, 15, 12, 34, 56, 123456, tzinfo=timezone.utc)

    def test_parse_hapi_time_returns_utc_aware(self):
        t = _parse_hapi_time('2018-03-15T00:00:00.000000000Z')
        assert t.tzinfo is not None
        assert t.utcoffset().total_seconds() == 0


    def test_parse_hapi_time_nanoseconds_with_nonzero_fraction(self):
        """
        Nanosecond precision with a nonzero fractional part: the first 6
        digits (microseconds) are kept, digits beyond are truncated (not
        rounded). 123456789 ns -> 123456 us.
        """
        t = _parse_hapi_time("2018-03-15T12:34:56.123456789Z")
        assert t == datetime(
            2018, 3, 15, 12, 34, 56, 123456, tzinfo=timezone.utc
        )

    def test_parse_hapi_time_microseconds(self):
        """Six fractional digits (microseconds) must parse exactly."""
        t = _parse_hapi_time("2018-03-15T12:34:56.123456Z")
        assert t == datetime(
            2018, 3, 15, 12, 34, 56, 123456, tzinfo=timezone.utc
        )

    def test_parse_hapi_time_returns_utc_aware(self):
        """Parsed timestamps must always be UTC-aware, never naive."""
        t = _parse_hapi_time("2018-03-15T00:00:00.000000000Z")
        assert t.tzinfo is not None
        assert t.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# Plasma (DSCOVR_H1_FC) ingestion
# ---------------------------------------------------------------------------


class TestPlasmaIngestion:

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_fetch_plasma_returns_expected_packet_count(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        assert len(packets) == 3

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_plasma_packets_have_correct_apid_and_type(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        for p in packets:
            assert p.apid == APID_PLASMA
            assert p.packet_type == PacketType.TELEMETRY
            assert p.spacecraft_id == SPACECRAFT_DSCOVR
            assert p.source == "noaa_dscovr_archive"

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_plasma_parameters_parsed_correctly(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        first = packets[0]
        assert first.parameters["proton_density_n_cc"] == pytest.approx(5.2)
        # bulk speed is magnitude of V_GSE = sqrt(420^2 + 12.5^2 + 3.2^2)
        expected_speed = math.sqrt(420.0**2 + 12.5**2 + 3.2**2)
        assert first.parameters["bulk_speed_km_s"] == pytest.approx(expected_speed)
        # Temperature is published directly in THERMAL_TEMP — no conversion.
        assert first.parameters["ion_temperature_k"] == pytest.approx(85000.0)

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_plasma_preserves_velocity_components(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        first = list(ingester._fetch_plasma_packets(START, END))[0]
        assert first.parameters["vx_gse_km_s"] == pytest.approx(-420.0)
        assert first.parameters["vy_gse_km_s"] == pytest.approx(12.5)
        assert first.parameters["vz_gse_km_s"] == pytest.approx(-3.2)
        assert first.parameters["thermal_speed_km_s"] == pytest.approx(37.5)

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_plasma_preserves_data_quality_flag(self, mock_get):
        """DQF must be preserved so downstream filters can drop bad rows."""
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        first = list(ingester._fetch_plasma_packets(START, END))[0]
        assert first.parameters["data_quality_flag"] == 0.0

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_plasma_marks_data_level_definitive(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        first = list(ingester._fetch_plasma_packets(START, END))[0]
        assert first.parameters["data_level"] == "definitive"
        assert first.parameters["archive_dataset"] == DATASET_FC

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_plasma_skips_malformed_rows(self, mock_get):
        # First row well-formed (14 cols), second row too few columns.
        good_row = "0,-420.0,12.5,-3.2,0.5,0.5,0.5,37.5,0.5,5.2,0.5,85000.0,0.5"
        bad_csv = (
            f"2018-03-15T12:00:00.000Z,{good_row}\n"
            "2018-03-15T12:01:00.000Z,0,5.4\n"  # truncated row
            f"2018-03-15T12:02:00.000Z,{good_row}\n"
        )
        mock_get.return_value = _make_csv_response(bad_csv)
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        # The malformed row is dropped, but the good ones survive
        assert len(packets) == 2


# ---------------------------------------------------------------------------
# Magnetometer (DSCOVR_H0_MAG) ingestion
# ---------------------------------------------------------------------------


class TestMagneticIngestion:

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_fetch_mag_returns_expected_packet_count(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_MAG_CSV)
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester._fetch_magnetic_packets(START, END))
        assert len(packets) == 3

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_mag_packets_have_correct_apid(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_MAG_CSV)
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester._fetch_magnetic_packets(START, END))
        assert all(p.apid == APID_MAGNETIC for p in packets)

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_mag_parameters_use_gse_keys(self, mock_get):
        """Archive uses GSE, not GSM. Keys must differ from live ingester."""
        mock_get.return_value = _make_csv_response(MOCK_MAG_CSV)
        ingester = NOAADscovrArchiveIngester()
        first = list(ingester._fetch_magnetic_packets(START, END))[0]
        assert "bx_gse_nt" in first.parameters
        assert "by_gse_nt" in first.parameters
        assert "bz_gse_nt" in first.parameters
        assert "bt_nt" in first.parameters
        # Live-feed key MUST NOT appear on archive packets — that would be
        # silently lying about the coordinate frame.
        assert "bx_gsm_nt" not in first.parameters

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_mag_values_parsed_correctly(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_MAG_CSV)
        ingester = NOAADscovrArchiveIngester()
        first = list(ingester._fetch_magnetic_packets(START, END))[0]
        assert first.parameters["bt_nt"] == pytest.approx(2.6)
        assert first.parameters["bx_gse_nt"] == pytest.approx(-2.1)
        assert first.parameters["by_gse_nt"] == pytest.approx(1.3)
        assert first.parameters["bz_gse_nt"] == pytest.approx(-0.8)

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_mag_does_not_leak_stddev_or_rtn(self, mock_get):
        """
        Regression test for the column-order bug found in the original parser:
        it must NOT confuse stddev columns or RTN-frame values for the GSE
        components. If the mock CSV has 0.1 in all skip columns and the GSE
        values are -2.1/1.3/-0.8, none of the GSE-named parameters should
        equal 0.1.
        """
        mock_get.return_value = _make_csv_response(MOCK_MAG_CSV)
        ingester = NOAADscovrArchiveIngester()
        first = list(ingester._fetch_magnetic_packets(START, END))[0]
        for key in ("bx_gse_nt", "by_gse_nt", "bz_gse_nt"):
            assert first.parameters[key] != pytest.approx(0.1), (
                f"{key} got the stddev/RTN value instead of the GSE value — "
                f"column-order regression."
            )

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_mag_raw_bytes_layout(self, mock_get):
        """Raw bytes are 4 floats × 4 bytes = 16 bytes."""
        mock_get.return_value = _make_csv_response(MOCK_MAG_CSV)
        ingester = NOAADscovrArchiveIngester()
        first = list(ingester._fetch_magnetic_packets(START, END))[0]
        assert len(first.raw_bytes) == 16


# ---------------------------------------------------------------------------
# BaseIngester contract & integration
# ---------------------------------------------------------------------------


class TestIngesterContract:

    def test_get_available_spacecraft(self):
        ingester = NOAADscovrArchiveIngester()
        assert SPACECRAFT_DSCOVR in ingester.get_available_spacecraft()

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_is_available_true_on_200(self, mock_get):
        mock_get.return_value = _make_csv_response("ok")
        ingester = NOAADscovrArchiveIngester()
        assert ingester.is_available() is True

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_is_available_false_on_network_error(self, mock_get):
        mock_get.side_effect = Exception("DNS failure")
        ingester = NOAADscovrArchiveIngester()
        assert ingester.is_available() is False

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_unsupported_spacecraft_yields_nothing(self, mock_get):
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester.fetch_packets("UNKNOWN_SC", START, END))
        assert packets == []
        mock_get.assert_not_called()

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_fetch_packets_calls_both_endpoints(self, mock_get):
        # First call -> FC (plasma), second -> MAG.
        mock_get.side_effect = [
            _make_csv_response(MOCK_FC_CSV),
            _make_csv_response(MOCK_MAG_CSV),
        ]
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester.fetch_packets(SPACECRAFT_DSCOVR, START, END))
        assert len(packets) == 6
        # Plasma comes first
        assert packets[0].apid == APID_PLASMA
        assert packets[-1].apid == APID_MAGNETIC

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_ingest_into_session(self, mock_get):
        mock_get.side_effect = [
            _make_csv_response(MOCK_FC_CSV),
            _make_csv_response(MOCK_MAG_CSV),
        ]
        ingester = NOAADscovrArchiveIngester()
        session = MissionSession.create(
            spacecraft_id=SPACECRAFT_DSCOVR,
            mission_phase=MissionPhase.NOMINAL,
            start_time=START,
            end_time=END,
        )
        result = ingester.ingest_into_session(session, START, END)
        assert result.success is True
        assert result.packets_ingested == 6
        assert result.packets_failed == 0
        assert session.packet_count == 6

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_packets_have_unique_ids(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        ids = [p.packet_id for p in packets]
        assert len(ids) == len(set(ids))

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_network_error_raises_data_source_unavailable(self, mock_get):
        import requests as _requests
        from src.chronoscope.domain.exceptions import DataSourceUnavailableError
        mock_get.side_effect = _requests.ConnectionError("nope")
        ingester = NOAADscovrArchiveIngester()
        # Generator must be exhausted to surface the error
        with pytest.raises(DataSourceUnavailableError):
            list(ingester._fetch_plasma_packets(START, END))


# ---------------------------------------------------------------------------
# Operational-window safety
# ---------------------------------------------------------------------------


class TestOperationalWindow:
    """
    DSCOVR launched Feb 2015 but only became operational 2016-07-27.
    Data before that date is ground-test data. The ingester must refuse to
    silently include it — that would poison any training corpus.
    """

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_request_entirely_before_operational_returns_nothing(self, mock_get):
        ingester = NOAADscovrArchiveIngester()
        # Window entirely in 2015, before operational date
        pre_op_start = datetime(2015, 5, 1, tzinfo=timezone.utc)
        pre_op_end = datetime(2015, 5, 2, tzinfo=timezone.utc)
        packets = list(ingester.fetch_packets(
            SPACECRAFT_DSCOVR, pre_op_start, pre_op_end
        ))
        assert packets == []
        # Critically: no HAPI call was made
        mock_get.assert_not_called()

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_request_spanning_operational_boundary_is_clamped(self, mock_get):
        """
        A window that straddles the operational date must be silently
        truncated to start at the operational date, and the HAPI call must
        use the clamped start time — not the user's original (invalid) start.
        """
        mock_get.side_effect = [
            _make_csv_response(MOCK_FC_CSV),
            _make_csv_response(MOCK_MAG_CSV),
        ]
        ingester = NOAADscovrArchiveIngester()
        pre_op_start = datetime(2016, 1, 1, tzinfo=timezone.utc)
        post_op_end = datetime(2016, 12, 31, tzinfo=timezone.utc)
        list(ingester.fetch_packets(SPACECRAFT_DSCOVR, pre_op_start, post_op_end))

        # Inspect the actual HAPI call(s); time.min must equal the clamp date
        called_params = mock_get.call_args_list[0].kwargs["params"]
        clamped_min = called_params["time.min"]
        assert clamped_min.startswith("2016-07-27"), (
            f"HAPI time.min should be clamped to operational date, "
            f"got {clamped_min!r}"
        )

    def test_operational_date_constant_matches_documented_value(self):
        """Smoke test: NCEI documents 2016-07-27 as the operational date."""
        assert DSCOVR_OPERATIONAL_DATE == datetime(
            2016, 7, 27, tzinfo=timezone.utc
        )


# ---------------------------------------------------------------------------
# DSCOVR_H1_FC dataset-end-date safety
# ---------------------------------------------------------------------------


class TestH1FCEndDate:
    """
    The DSCOVR_H1_FC plasma dataset stopped being updated after 2019-06-27.
    Requesting plasma data past that date must surface a clear warning rather
    than silently returning nothing and pretending all is well.
    """

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_plasma_request_after_h1_fc_end_makes_no_http_call(self, mock_get):
        """Pure plasma fetch with a start time past H1_FC end skips HTTP."""
        ingester = NOAADscovrArchiveIngester()
        # Window entirely after H1_FC end date
        post_end_start = datetime(2022, 1, 1, tzinfo=timezone.utc)
        post_end_end = datetime(2022, 1, 2, tzinfo=timezone.utc)
        packets = list(ingester._fetch_plasma_packets(
            post_end_start, post_end_end
        ))
        assert packets == []
        mock_get.assert_not_called()

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_mag_still_fetches_after_h1_fc_end(self, mock_get):
        """MAG is unaffected by the H1_FC end date — it goes to present day."""
        mock_get.return_value = _make_csv_response(MOCK_MAG_CSV)
        ingester = NOAADscovrArchiveIngester()
        post_end_start = datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)
        post_end_end = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        packets = list(ingester._fetch_magnetic_packets(
            post_end_start, post_end_end
        ))
        assert len(packets) == 3

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_full_fetch_post_h1_fc_end_returns_only_mag(self, mock_get):
        """
        A combined fetch_packets call with a start past H1_FC end must skip
        the plasma HTTP call entirely but still fetch MAG. Result: only
        magnetic packets, exactly one HTTP call.
        """
        mock_get.return_value = _make_csv_response(MOCK_MAG_CSV)
        ingester = NOAADscovrArchiveIngester()
        post_end_start = datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)
        post_end_end = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        packets = list(ingester.fetch_packets(
            SPACECRAFT_DSCOVR, post_end_start, post_end_end
        ))
        assert len(packets) == 3
        assert all(p.apid == APID_MAGNETIC for p in packets)
        assert mock_get.call_count == 1

    def test_h1_fc_end_date_matches_documented_value(self):
        """Smoke test: CDAWeb /info reports stopDate 2019-06-27 for H1_FC."""
        # We store the exclusive upper bound (2019-06-28).
        assert DSCOVR_H1_FC_END_DATE == datetime(
            2019, 6, 28, tzinfo=timezone.utc
        )


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


class TestEmptyResponses:

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_empty_csv_body_yields_no_packets(self, mock_get):
        mock_get.return_value = _make_csv_response("")
        ingester = NOAADscovrArchiveIngester()
        assert list(ingester._fetch_plasma_packets(START, END)) == []

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_comment_lines_are_skipped(self, mock_get):
        body = (
            "# this is a server comment\n"
            "\n"
            "2018-03-15T12:00:00.000Z,0,-420.0,12.5,-3.2,0.5,0.5,0.5,37.5,0.5,5.2,0.5,85000.0,0.5\n"
        )
        mock_get.return_value = _make_csv_response(body)
        ingester = NOAADscovrArchiveIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        assert len(packets) == 1

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_dataset_id_passed_correctly(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        list(ingester._fetch_plasma_packets(START, END))
        params = mock_get.call_args.kwargs["params"]
        assert params["id"] == DATASET_FC
        assert params["format"] == "csv"
