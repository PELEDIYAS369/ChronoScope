"""
Unit tests for ChronoScope ingestion layer.
Uses mocked HTTP responses so tests run without network access.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from src.chronoscope.ingestion.base import BaseIngester, IngestionResult
from src.chronoscope.ingestion.noaa_dscovr import NOAADscovrIngester
from src.chronoscope.domain.models import (
    MissionSession,
    MissionPhase,
    PacketType,
)
from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR


# Sample NOAA API response format — matches real API structure exactly
MOCK_PLASMA_RESPONSE = [
    ["time_tag", "density", "speed", "temperature"],
    ["2024-01-15 12:00:00.000", "5.2", "420.0", "85000.0"],
    ["2024-01-15 12:01:00.000", "5.4", "425.0", "86000.0"],
    ["2024-01-15 12:02:00.000", "5.1", "418.0", "84000.0"],
]

MOCK_MAG_RESPONSE = [
    ["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "bt"],
    ["2024-01-15 12:00:00.000", "-2.1", "1.3", "-0.8", "2.6"],
    ["2024-01-15 12:01:00.000", "-2.3", "1.1", "-0.9", "2.7"],
    ["2024-01-15 12:02:00.000", "-2.0", "1.4", "-0.7", "2.5"],
]

START = datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)
END = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)


class TestIngestionResult:

    def test_success_rate_all_success(self):
        result = IngestionResult(
            success=True,
            source="test",
            packets_ingested=10,
            packets_failed=0,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            errors=[],
        )
        assert result.success_rate == 1.0

    def test_success_rate_partial(self):
        result = IngestionResult(
            success=True,
            source="test",
            packets_ingested=8,
            packets_failed=2,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            errors=[],
        )
        assert result.success_rate == 0.8

    def test_success_rate_zero_packets(self):
        result = IngestionResult(
            success=False,
            source="test",
            packets_ingested=0,
            packets_failed=0,
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            errors=[],
        )
        assert result.success_rate == 0.0


class TestNOAADscovrIngester:

    def _make_mock_response(self, data):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = data
        mock.raise_for_status.return_value = None
        return mock

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_fetch_plasma_packets(self, mock_get):
        mock_get.return_value = self._make_mock_response(MOCK_PLASMA_RESPONSE)
        ingester = NOAADscovrIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        assert len(packets) == 3
        assert all(p.spacecraft_id == SPACECRAFT_DSCOVR for p in packets)
        assert all(p.packet_type == PacketType.TELEMETRY for p in packets)
        assert all(p.apid == 0x64 for p in packets)

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_plasma_parameters_parsed_correctly(self, mock_get):
        mock_get.return_value = self._make_mock_response(MOCK_PLASMA_RESPONSE)
        ingester = NOAADscovrIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        first = packets[0]
        assert first.parameters["proton_density_n_cc"] == 5.2
        assert first.parameters["bulk_speed_km_s"] == 420.0
        assert first.parameters["ion_temperature_k"] == 85000.0

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_fetch_magnetic_packets(self, mock_get):
        mock_get.return_value = self._make_mock_response(MOCK_MAG_RESPONSE)
        ingester = NOAADscovrIngester()
        packets = list(ingester._fetch_magnetic_packets(START, END))
        assert len(packets) == 3
        assert all(p.apid == 0x65 for p in packets)

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_magnetic_parameters_parsed_correctly(self, mock_get):
        mock_get.return_value = self._make_mock_response(MOCK_MAG_RESPONSE)
        ingester = NOAADscovrIngester()
        packets = list(ingester._fetch_magnetic_packets(START, END))
        first = packets[0]
        assert first.parameters["bx_gsm_nt"] == -2.1
        assert first.parameters["bz_gsm_nt"] == -0.8

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_time_range_filter(self, mock_get):
        mock_get.return_value = self._make_mock_response(MOCK_PLASMA_RESPONSE)
        ingester = NOAADscovrIngester()
        # Very narrow window — should get zero packets
        narrow_start = datetime(2024, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
        narrow_end = datetime(2024, 1, 15, 7, 0, 0, tzinfo=timezone.utc)
        packets = list(ingester._fetch_plasma_packets(narrow_start, narrow_end))
        assert len(packets) == 0

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_ingest_into_session(self, mock_get):
        mock_get.side_effect = [
            self._make_mock_response(MOCK_PLASMA_RESPONSE),
            self._make_mock_response(MOCK_MAG_RESPONSE),
        ]
        ingester = NOAADscovrIngester()
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

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_unsupported_spacecraft_yields_nothing(self, mock_get):
        ingester = NOAADscovrIngester()
        packets = list(ingester.fetch_packets("UNKNOWN_SC", START, END))
        assert len(packets) == 0
        mock_get.assert_not_called()

    def test_get_available_spacecraft(self):
        ingester = NOAADscovrIngester()
        spacecraft = ingester.get_available_spacecraft()
        assert SPACECRAFT_DSCOVR in spacecraft

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_is_available_true(self, mock_get):
        mock_get.return_value = self._make_mock_response(MOCK_PLASMA_RESPONSE)
        ingester = NOAADscovrIngester()
        assert ingester.is_available() is True

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_is_available_false_on_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        ingester = NOAADscovrIngester()
        assert ingester.is_available() is False

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_raw_bytes_encoded(self, mock_get):
        mock_get.return_value = self._make_mock_response(MOCK_PLASMA_RESPONSE)
        ingester = NOAADscovrIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        assert len(packets[0].raw_bytes) == 12  # 3 floats × 4 bytes

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_packets_have_unique_ids(self, mock_get):
        mock_get.return_value = self._make_mock_response(MOCK_PLASMA_RESPONSE)
        ingester = NOAADscovrIngester()
        packets = list(ingester._fetch_plasma_packets(START, END))
        ids = [p.packet_id for p in packets]
        assert len(ids) == len(set(ids))