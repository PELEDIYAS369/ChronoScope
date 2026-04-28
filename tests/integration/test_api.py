"""
Integration tests for ChronoScope REST API.
Uses FastAPI TestClient — no real network calls.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.chronoscope.api.app import app
from src.chronoscope.api.routes import get_controller, _controller
from src.chronoscope.api import routes as routes_module

VALID_KEY = "chronoscope-demo-key-2026"
HEADERS = {"X-API-Key": VALID_KEY}

START = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
END = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_controller():
    """Reset controller between tests."""
    routes_module._controller = None
    yield
    routes_module._controller = None


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health Tests
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_no_auth_required(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"

    def test_root_endpoint(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["product"] == "ChronoScope AI"


# ---------------------------------------------------------------------------
# Auth Tests
# ---------------------------------------------------------------------------

class TestAuth:

    def test_no_key_returns_401(self, client):
        resp = client.get("/api/v1/status")
        assert resp.status_code == 401

    def test_invalid_key_returns_401(self, client):
        resp = client.get(
            "/api/v1/status",
            headers={"X-API-Key": "bad-key"}
        )
        assert resp.status_code == 401

    def test_valid_key_accepted(self, client):
        resp = client.get("/api/v1/status", headers=HEADERS)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Session Tests
# ---------------------------------------------------------------------------

class TestSessions:

    def test_create_session(self, client):
        resp = client.post(
            "/api/v1/sessions",
            headers=HEADERS,
            json={
                "spacecraft_id": "DSCOVR",
                "mission_phase": "nominal",
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["spacecraft_id"] == "DSCOVR"
        assert data["mission_phase"] == "nominal"
        assert "session_id" in data

    def test_get_session(self, client):
        create = client.post(
            "/api/v1/sessions",
            headers=HEADERS,
            json={
                "spacecraft_id": "DSCOVR",
                "mission_phase": "nominal",
                "start_time": START.isoformat(),
            },
        )
        session_id = create.json()["session_id"]
        resp = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == session_id

    def test_get_nonexistent_session_404(self, client):
        resp = client.get(
            "/api/v1/sessions/nonexistent-id",
            headers=HEADERS,
        )
        assert resp.status_code == 404

    def test_list_sessions(self, client):
        client.post(
            "/api/v1/sessions",
            headers=HEADERS,
            json={
                "spacecraft_id": "DSCOVR",
                "mission_phase": "nominal",
                "start_time": START.isoformat(),
            },
        )
        resp = client.get("/api/v1/sessions", headers=HEADERS)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_invalid_mission_phase_400(self, client):
        resp = client.post(
            "/api/v1/sessions",
            headers=HEADERS,
            json={
                "spacecraft_id": "DSCOVR",
                "mission_phase": "invalid_phase",
                "start_time": START.isoformat(),
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Ingestion Tests (mocked)
# ---------------------------------------------------------------------------

MOCK_PLASMA = [
    ["time_tag", "density", "speed", "temperature"],
    ["2024-01-15 12:00:00.000", "5.2", "420.0", "85000.0"],
    ["2024-01-15 12:01:00.000", "5.4", "425.0", "86000.0"],
]
MOCK_MAG = [
    ["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "bt"],
    ["2024-01-15 12:00:00.000", "-2.1", "1.3", "-0.8", "2.6"],
    ["2024-01-15 12:01:00.000", "-2.3", "1.1", "-0.9", "2.7"],
]


class TestIngestion:

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_ingest_success(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = [MOCK_PLASMA, MOCK_MAG]
        mock_get.return_value = mock_resp

        create = client.post(
            "/api/v1/sessions",
            headers=HEADERS,
            json={
                "spacecraft_id": "DSCOVR",
                "mission_phase": "nominal",
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
            },
        )
        session_id = create.json()["session_id"]

        resp = client.post(
            f"/api/v1/sessions/{session_id}/ingest",
            headers=HEADERS,
            json={
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["packets_ingested"] > 0

    def test_ingest_nonexistent_session(self, client):
        resp = client.post(
            "/api/v1/sessions/fake-id/ingest",
            headers=HEADERS,
            json={
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
            },
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Replay Tests (mocked)
# ---------------------------------------------------------------------------

class TestReplay:

    @patch("src.chronoscope.ingestion.noaa_dscovr.requests.get")
    def test_full_replay_flow(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = MOCK_PLASMA
        mock_get.return_value = mock_resp

        # Create session
        create = client.post(
            "/api/v1/sessions",
            headers=HEADERS,
            json={
                "spacecraft_id": "DSCOVR",
                "mission_phase": "nominal",
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
            },
        )
        sid = create.json()["session_id"]

        # Ingest
        mock_resp.json.side_effect = [MOCK_PLASMA, MOCK_MAG]
        client.post(
            f"/api/v1/sessions/{sid}/ingest",
            headers=HEADERS,
            json={
                "start_time": START.isoformat(),
                "end_time": END.isoformat(),
            },
        )

        # Load replay
        mock_resp.json.side_effect = None
        load = client.post(
            f"/api/v1/sessions/{sid}/replay/load",
            headers=HEADERS,
        )
        assert load.status_code == 200
        cursor = load.json()
        assert cursor["total_packets"] > 0
        assert cursor["current_index"] == 0

        # Play
        play = client.post(
            f"/api/v1/sessions/{sid}/replay/play",
            headers=HEADERS,
        )
        assert play.status_code == 200
        assert play.json()["is_playing"] is True

        # Pause
        pause = client.post(
            f"/api/v1/sessions/{sid}/replay/pause",
            headers=HEADERS,
        )
        assert pause.status_code == 200
        assert pause.json()["is_playing"] is False

        # Step forward
        step = client.post(
            f"/api/v1/sessions/{sid}/replay/step-forward",
            headers=HEADERS,
        )
        assert step.status_code == 200
        assert step.json()["current_index"] == 1


# ---------------------------------------------------------------------------
# Audit Tests
# ---------------------------------------------------------------------------

class TestAudit:

    def test_audit_summary(self, client):
        resp = client.get("/api/v1/audit/summary", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_entries" in data
        assert data["chain_intact"] is True

    def test_audit_verify(self, client):
        resp = client.post("/api/v1/audit/verify", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["chain_intact"] is True

    def test_audit_export(self, client):
        resp = client.get("/api/v1/audit/export", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "genesis_hash" in data