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
    DSCOVR_OPERATIONAL_DATE,
    NOAADscovrArchiveIngester,
    _parse_hapi_time,
    _to_hapi_time,
)


# ---------------------------------------------------------------------------
# Fixture CSV bodies — HAPI default (no header), comma-separated.
# Real HAPI responses look like this; column order matches what the CDAWeb
# HAPI /info endpoint publishes for each dataset.
# ---------------------------------------------------------------------------

# DSCOVR_H1_FC columns: Epoch, Np, V_GSE_X, V_GSE_Y, V_GSE_Z, THERMAL_SPD
MOCK_FC_CSV = (
    "2024-01-15T12:00:00.000Z,5.2,-420.0,12.5,-3.2,37.5\n"
    "2024-01-15T12:01:00.000Z,5.4,-425.0,11.8,-2.9,38.1\n"
    "2024-01-15T12:02:00.000Z,5.1,-418.0,13.1,-3.4,37.0\n"
)

# DSCOVR_H0_MAG columns: Epoch, B1F1, B1GSE_X, B1GSE_Y, B1GSE_Z
MOCK_MAG_CSV = (
    "2024-01-15T12:00:00.000Z,2.6,-2.1,1.3,-0.8\n"
    "2024-01-15T12:00:01.000Z,2.7,-2.3,1.1,-0.9\n"
    "2024-01-15T12:00:02.000Z,2.5,-2.0,1.4,-0.7\n"
)

# Time window — post-DSCOVR-operational-date so the operational-window clamp
# doesn't filter our test data.
START = datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)
END = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)


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
        # Temperature from thermal speed: T = v_th^2 * 60.5
        expected_temp = 37.5**2 * 60.5
        assert first.parameters["ion_temperature_k"] == pytest.approx(expected_temp)

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
    def test_plasma_marks_data_level_definitive(self, mock_get):
        mock_get.return_value = _make_csv_response(MOCK_FC_CSV)
        ingester = NOAADscovrArchiveIngester()
        first = list(ingester._fetch_plasma_packets(START, END))[0]
        assert first.parameters["data_level"] == "definitive"
        assert first.parameters["archive_dataset"] == DATASET_FC

    @patch("src.chronoscope.ingestion.noaa_dscovr_archive.requests.get")
    def test_plasma_skips_malformed_rows(self, mock_get):
        # First row well-formed, second row too few columns
        bad_csv = (
            "2024-01-15T12:00:00.000Z,5.2,-420.0,12.5,-3.2,37.5\n"
            "2024-01-15T12:01:00.000Z,5.4\n"  # only 2 columns
            "2024-01-15T12:02:00.000Z,5.1,-418.0,13.1,-3.4,37.0\n"
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
            "2024-01-15T12:00:00.000Z,5.2,-420.0,12.5,-3.2,37.5\n"
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
