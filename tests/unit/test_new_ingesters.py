"""
Unit tests for ACE, OpenSky, and CelesTrak ingesters.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from src.chronoscope.ingestion.ace import ACEIngester, SPACECRAFT_ACE
from src.chronoscope.ingestion.opensky import OpenSkyIngester
from src.chronoscope.ingestion.celestrak import CelesTrakIngester
from src.chronoscope.domain.models import PacketType

START = datetime(2026, 4, 25, 0, 0, 0, tzinfo=timezone.utc)
END   = datetime(2026, 5,  2, 0, 0, 0, tzinfo=timezone.utc)

MOCK_PLASMA = [
    ["time_tag", "density", "speed", "temperature"],
    ["2026-04-28 12:00:00.000", "5.2", "420.0", "85000.0"],
    ["2026-04-28 12:01:00.000", "5.4", "425.0", "510000.0"],
    ["2026-04-28 12:02:00.000", "5.1", "418.0", "84000.0"],
]

MOCK_MAG = [
    ["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "bt"],
    ["2026-04-28 12:00:00.000", "-2.1", "1.3", "-0.8", "2.6"],
    ["2026-04-28 12:01:00.000", "-2.3", "1.1", "-0.9", "2.7"],
]

MOCK_OPENSKY = {
    "time": 1777749134,
    "states": [
        ["a5f852", "UAL528  ", "United States", 1777749133, 1777749133,
         -95.97, 30.65, 3482.0, False, 182.0, 45.0, 0.5, None, 3500.0, "1234", False, 0],
        ["abc123", "AAL100  ", "United States", 1777749133, 1777749133,
         -80.87, 34.20, 11278.0, False, 264.0, 90.0, 0.0, None, 11300.0, "5678", False, 0],
        ["xyz999", "ONGROUND", "United States", 1777749133, 1777749133,
         -100.0, 40.0, 0.0, True, 0.0, 0.0, 0.0, None, 0.0, "0000", False, 0],
    ]
}

MOCK_CELESTRAK = [
    {
        "OBJECT_NAME": "ISS (ZARYA)",
        "OBJECT_ID": "1998-067A",
        "NORAD_CAT_ID": 25544,
        "PERIOD": "92.96",
        "INCLINATION": "51.63",
        "APOGEE": "424",
        "PERIGEE": "415",
        "ECCENTRICITY": "0.0006",
        "RA_OF_ASC_NODE": "123.45",
    }
]


# ── ACE Ingester ──────────────────────────────────────────────────

class TestACEIngester:

    def _mock_response(self, data):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = data
        mock.raise_for_status.return_value = None
        return mock

    def test_source_name(self):
        i = ACEIngester()
        assert i.source_name == "ace_spacecraft"

    def test_available_spacecraft(self):
        i = ACEIngester()
        assert SPACECRAFT_ACE in i.get_available_spacecraft()

    @patch("src.chronoscope.ingestion.ace.requests.get")
    def test_fetch_plasma_packets(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_PLASMA)
        i = ACEIngester()
        pkts = list(i._fetch_plasma(START, END))
        assert len(pkts) == 3
        assert all(p.spacecraft_id == SPACECRAFT_ACE for p in pkts)
        assert all(p.packet_type == PacketType.TELEMETRY for p in pkts)

    @patch("src.chronoscope.ingestion.ace.requests.get")
    def test_fetch_mag_packets(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_MAG)
        i = ACEIngester()
        pkts = list(i._fetch_mag(START, END))
        assert len(pkts) == 2

    @patch("src.chronoscope.ingestion.ace.requests.get")
    def test_plasma_parameters(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_PLASMA)
        i = ACEIngester()
        pkts = list(i._fetch_plasma(START, END))
        assert pkts[0].parameters["proton_density_n_cc"] == 5.2
        assert pkts[0].parameters["bulk_speed_km_s"] == 420.0

    @patch("src.chronoscope.ingestion.ace.requests.get")
    def test_wrong_spacecraft_yields_nothing(self, mock_get):
        i = ACEIngester()
        pkts = list(i.fetch_packets("WRONG_SC", START, END))
        assert len(pkts) == 0
        mock_get.assert_not_called()

    @patch("src.chronoscope.ingestion.ace.requests.get")
    def test_is_available_true(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_PLASMA)
        i = ACEIngester()
        assert i.is_available() is True

    @patch("src.chronoscope.ingestion.ace.requests.get")
    def test_is_available_false(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        i = ACEIngester()
        assert i.is_available() is False

    @patch("src.chronoscope.ingestion.ace.requests.get")
    def test_packets_have_unique_ids(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_PLASMA)
        i = ACEIngester()
        pkts = list(i._fetch_plasma(START, END))
        ids = [p.packet_id for p in pkts]
        assert len(ids) == len(set(ids))

    @patch("src.chronoscope.ingestion.ace.requests.get")
    def test_raw_bytes_encoded(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_PLASMA)
        i = ACEIngester()
        pkts = list(i._fetch_plasma(START, END))
        assert len(pkts[0].raw_bytes) == 12  # 3 floats x 4 bytes

    def test_safe_float_none(self):
        i = ACEIngester()
        assert i._safe_float(None) == 0.0

    def test_safe_float_empty(self):
        i = ACEIngester()
        assert i._safe_float("") == 0.0

    def test_safe_float_valid(self):
        i = ACEIngester()
        assert i._safe_float("3.14") == 3.14


# ── OpenSky Ingester ──────────────────────────────────────────────

class TestOpenSkyIngester:

    def _mock_response(self, data):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = data
        mock.raise_for_status.return_value = None
        return mock

    def test_source_name(self):
        i = OpenSkyIngester()
        assert i.source_name == "opensky_network"

    def test_available_spacecraft(self):
        i = OpenSkyIngester()
        assert "OPENSKY_LIVE" in i.get_available_spacecraft()

    @patch("src.chronoscope.ingestion.opensky.requests.get")
    def test_fetch_airborne_only(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_OPENSKY)
        i = OpenSkyIngester()
        pkts = list(i.fetch_packets("OPENSKY_LIVE", START, END))
        # Should exclude the on-ground aircraft
        assert all(
            "AIRCRAFT_" in p.spacecraft_id for p in pkts
        )
        callsigns = [p.parameters["callsign"].strip() for p in pkts]
        assert "ONGROUND" not in callsigns

    @patch("src.chronoscope.ingestion.opensky.requests.get")
    def test_aircraft_parameters(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_OPENSKY)
        i = OpenSkyIngester()
        pkts = list(i.fetch_packets("OPENSKY_LIVE", START, END))
        assert len(pkts) >= 1
        p = pkts[0]
        assert "latitude_deg" in p.parameters
        assert "longitude_deg" in p.parameters
        assert "baro_altitude_m" in p.parameters
        assert "velocity_ms" in p.parameters

    @patch("src.chronoscope.ingestion.opensky.requests.get")
    def test_packets_are_telemetry_type(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_OPENSKY)
        i = OpenSkyIngester()
        pkts = list(i.fetch_packets("OPENSKY_LIVE", START, END))
        assert all(p.packet_type == PacketType.TELEMETRY for p in pkts)

    @patch("src.chronoscope.ingestion.opensky.requests.get")
    def test_is_available_true(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_OPENSKY)
        i = OpenSkyIngester()
        assert i.is_available() is True

    @patch("src.chronoscope.ingestion.opensky.requests.get")
    def test_is_available_false(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        i = OpenSkyIngester()
        assert i.is_available() is False

    @patch("src.chronoscope.ingestion.opensky.requests.get")
    def test_max_aircraft_limit(self, mock_get):
        many_states = {
            "time": 123,
            "states": MOCK_OPENSKY["states"] * 10
        }
        mock_get.return_value = self._mock_response(many_states)
        i = OpenSkyIngester(max_aircraft=3)
        pkts = list(i.fetch_packets("OPENSKY_LIVE", START, END))
        assert len(pkts) <= 3

    @patch("src.chronoscope.ingestion.opensky.requests.get")
    def test_skips_null_positions(self, mock_get):
        data = {
            "time": 123,
            "states": [
                ["abc", "FLT001  ", "US", 123, 123,
                 None, None, 5000.0, False, 200.0,
                 90.0, 0.0, None, 5100.0, "1234", False, 0],
            ]
        }
        mock_get.return_value = self._mock_response(data)
        i = OpenSkyIngester()
        pkts = list(i.fetch_packets("OPENSKY_LIVE", START, END))
        assert len(pkts) == 0


# ── CelesTrak Ingester ────────────────────────────────────────────

class TestCelesTrakIngester:

    def _mock_response(self, data):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = data
        mock.raise_for_status.return_value = None
        return mock

    def test_source_name(self):
        i = CelesTrakIngester(group="ISS")
        assert "celestrak" in i.source_name

    def test_available_spacecraft(self):
        i = CelesTrakIngester()
        sc = i.get_available_spacecraft()
        assert len(sc) > 0

    @patch("src.chronoscope.ingestion.celestrak.requests.get")
    def test_fetch_iss_record(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_CELESTRAK)
        i = CelesTrakIngester(group="ISS")
        pkts = list(i.fetch_packets("SAT_ISS", START, END))
        assert len(pkts) == 1
        p = pkts[0]
        assert "ISS" in p.spacecraft_id
        assert p.parameters["period_min"] == 92.96
        assert p.parameters["inclination_deg"] == 51.63
        assert p.parameters["apogee_km"] == 424.0
        assert p.parameters["perigee_km"] == 415.0

    @patch("src.chronoscope.ingestion.celestrak.requests.get")
    def test_orbital_parameters_present(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_CELESTRAK)
        i = CelesTrakIngester()
        pkts = list(i.fetch_packets("SAT_ISS", START, END))
        assert len(pkts) >= 1
        params = pkts[0].parameters
        assert "period_min" in params
        assert "inclination_deg" in params
        assert "apogee_km" in params
        assert "perigee_km" in params
        assert "eccentricity" in params

    @patch("src.chronoscope.ingestion.celestrak.requests.get")
    def test_packet_type_is_telemetry(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_CELESTRAK)
        i = CelesTrakIngester()
        pkts = list(i.fetch_packets("SAT_ISS", START, END))
        assert all(p.packet_type == PacketType.TELEMETRY for p in pkts)

    @patch("src.chronoscope.ingestion.celestrak.requests.get")
    def test_is_available_true(self, mock_get):
        mock_get.return_value = self._mock_response(MOCK_CELESTRAK)
        i = CelesTrakIngester()
        assert i.is_available() is True

    @patch("src.chronoscope.ingestion.celestrak.requests.get")
    def test_is_available_false(self, mock_get):
        mock_get.side_effect = Exception("timeout")
        i = CelesTrakIngester()
        assert i.is_available() is False

    @patch("src.chronoscope.ingestion.celestrak.requests.get")
    def test_empty_response_yields_nothing(self, mock_get):
        mock_get.return_value = self._mock_response([])
        i = CelesTrakIngester()
        pkts = list(i.fetch_packets("SAT_ISS", START, END))
        assert len(pkts) == 0