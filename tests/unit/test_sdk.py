"""
Unit tests for ChronoScope Integration SDK.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from src.chronoscope.sdk.models import SDKAlert, SDKSession, SDKHealth, SDKConfig
from src.chronoscope.sdk.client import ChronoScopeSDK
from src.chronoscope.sdk.webhooks import WebhookManager


def make_alert(severity: str = "high") -> SDKAlert:
    return SDKAlert(
        alert_id="test-alert-001",
        timestamp=datetime.now(timezone.utc),
        severity=severity,
        spacecraft_id="DSCOVR",
        parameter="ion_temperature_k",
        observed_value=562201.0,
        expected_range=(0.0, 500000.0),
        reason="Ion temperature 12.4% above threshold",
        confidence=0.86,
        urgency_hours=8.0,
        suggested_actions=[
            {"title": "Log HSS event", "success_rate": 0.89}
        ],
        session_id="session-abc",
    )


# ── SDK Models ────────────────────────────────────────────────────

class TestSDKAlert:

    def test_is_critical_false(self):
        alert = make_alert("high")
        assert alert.is_critical is False

    def test_is_critical_true(self):
        alert = make_alert("critical")
        assert alert.is_critical is True

    def test_top_action(self):
        alert = make_alert()
        assert alert.top_action is not None
        assert alert.top_action["title"] == "Log HSS event"

    def test_top_action_none_when_empty(self):
        alert = make_alert()
        alert.suggested_actions.clear()
        assert alert.top_action is None

    def test_to_dict_has_required_keys(self):
        alert = make_alert()
        d = alert.to_dict()
        assert "alert_id" in d
        assert "severity" in d
        assert "reason" in d
        assert "confidence" in d
        assert "suggested_actions" in d

    def test_to_dict_serializable(self):
        import json
        alert = make_alert()
        raw = json.dumps(alert.to_dict(), default=str)
        parsed = json.loads(raw)
        assert parsed["severity"] == "high"


class TestSDKHealth:

    def test_is_healthy_nominal(self):
        health = SDKHealth(
            status="NOMINAL",
            sessions=3,
            total_packets=1000,
            total_anomalies=5,
            audit_intact=True,
            timestamp=datetime.now(timezone.utc),
        )
        assert health.is_healthy is True

    def test_is_healthy_false_when_critical(self):
        health = SDKHealth(
            status="CRITICAL",
            sessions=1,
            total_packets=100,
            total_anomalies=1,
            audit_intact=True,
            timestamp=datetime.now(timezone.utc),
        )
        assert health.is_healthy is False

    def test_is_healthy_false_when_audit_broken(self):
        health = SDKHealth(
            status="NOMINAL",
            sessions=1,
            total_packets=100,
            total_anomalies=0,
            audit_intact=False,
            timestamp=datetime.now(timezone.utc),
        )
        assert health.is_healthy is False

    def test_to_dict(self):
        health = SDKHealth(
            status="NOMINAL",
            sessions=2,
            total_packets=500,
            total_anomalies=3,
            audit_intact=True,
            timestamp=datetime.now(timezone.utc),
        )
        d = health.to_dict()
        assert d["status"] == "NOMINAL"
        assert d["is_healthy"] is True


class TestSDKSession:

    def test_to_dict(self):
        session = SDKSession(
            session_id="abc-123",
            spacecraft_id="DSCOVR",
            mission_phase="nominal",
            packet_count=100,
            anomaly_count=3,
            replay_status="ready",
            start_time=datetime.now(timezone.utc),
        )
        d = session.to_dict()
        assert d["session_id"] == "abc-123"
        assert d["spacecraft_id"] == "DSCOVR"


# ── Webhook Manager ───────────────────────────────────────────────

class TestWebhookManager:

    def test_register_webhook(self):
        wm = WebhookManager()
        wid = wm.register("https://example.com/hook", ["anomaly.any"])
        assert wid is not None
        assert len(wm.list_webhooks()) == 1

    def test_unregister_webhook(self):
        wm = WebhookManager()
        wid = wm.register("https://example.com/hook", ["anomaly.any"])
        result = wm.unregister(wid)
        assert result is True
        assert len(wm.list_webhooks()) == 0

    def test_unregister_nonexistent(self):
        wm = WebhookManager()
        result = wm.unregister("nonexistent")
        assert result is False

    def test_list_webhooks(self):
        wm = WebhookManager()
        wm.register("https://a.com/hook", ["anomaly.critical"])
        wm.register("https://b.com/hook", ["anomaly.any"])
        hooks = wm.list_webhooks()
        assert len(hooks) == 2
        urls = [h["url"] for h in hooks]
        assert "https://a.com/hook" in urls

    def test_should_fire_any(self):
        wm = WebhookManager()
        wm.register("https://example.com", ["anomaly.any"])
        alert = make_alert("medium")
        wh = list(wm._webhooks.values())[0]
        assert wm._should_fire(wh, alert) is True

    def test_should_fire_specific_severity(self):
        wm = WebhookManager()
        wm.register("https://example.com", ["anomaly.critical"])
        alert_high = make_alert("high")
        alert_crit = make_alert("critical")
        wh = list(wm._webhooks.values())[0]
        assert wm._should_fire(wh, alert_high) is False
        assert wm._should_fire(wh, alert_crit) is True

    def test_delivery_count_increments(self):
        wm = WebhookManager()
        wid = wm.register("https://example.com", ["anomaly.any"], secret="")
        wh = wm._webhooks[wid]
        assert wh.delivery_count == 0


# ── SDK Client ────────────────────────────────────────────────────

class TestChronoScopeSDK:

    def test_init_defaults(self):
        sdk = ChronoScopeSDK()
        assert sdk.base_url == "http://localhost:8000"
        assert sdk.api_key == ""

    def test_init_custom(self):
        sdk = ChronoScopeSDK(
            base_url="http://myserver:9000",
            api_key="secret-key",
        )
        assert sdk.base_url == "http://myserver:9000"
        assert sdk.api_key == "secret-key"

    def test_register_anomaly_callback(self):
        sdk = ChronoScopeSDK()
        results = []
        sdk.on_anomaly(lambda a: results.append(a))
        assert len(sdk._anomaly_callbacks) == 1

    def test_register_critical_callback(self):
        sdk = ChronoScopeSDK()
        sdk.on_critical(lambda a: None)
        assert len(sdk._critical_callbacks) == 1

    def test_fire_callbacks_on_anomaly(self):
        sdk = ChronoScopeSDK()
        fired = []
        sdk.on_anomaly(lambda a: fired.append(a.severity))
        alert = make_alert("high")
        sdk._fire_callbacks(alert)
        assert "high" in fired

    def test_fire_critical_callback_only_for_critical(self):
        sdk = ChronoScopeSDK()
        critical_fired = []
        sdk.on_critical(lambda a: critical_fired.append(a))
        alert_high = make_alert("high")
        alert_crit = make_alert("critical")
        sdk._fire_callbacks(alert_high)
        assert len(critical_fired) == 0
        sdk._fire_callbacks(alert_crit)
        assert len(critical_fired) == 1

    def test_register_webhook(self):
        sdk = ChronoScopeSDK()
        wid = sdk.register_webhook("https://example.com/hook")
        assert wid is not None
        assert len(sdk.list_webhooks()) == 1

    def test_unregister_webhook(self):
        sdk = ChronoScopeSDK()
        wid = sdk.register_webhook("https://example.com/hook")
        result = sdk.unregister_webhook(wid)
        assert result is True

    @patch("src.chronoscope.sdk.client.requests.get")
    def test_ping_true(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        sdk = ChronoScopeSDK()
        assert sdk.ping() is True

    @patch("src.chronoscope.sdk.client.requests.get")
    def test_ping_false_on_error(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        sdk = ChronoScopeSDK()
        assert sdk.ping() is False

    @patch("src.chronoscope.sdk.client.requests.get")
    def test_health_unreachable(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        sdk = ChronoScopeSDK()
        health = sdk.health()
        assert health.status == "UNREACHABLE"
        assert health.is_healthy is False

    @patch("src.chronoscope.sdk.client.requests.get")
    def test_health_nominal(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "status": "NOMINAL",
                "sessions": 2,
                "total_packets": 500,
                "total_anomalies": 3,
                "audit_intact": True,
            }
        )
        sdk = ChronoScopeSDK()
        health = sdk.health()
        assert health.status == "NOMINAL"
        assert health.is_healthy is True

    @patch("src.chronoscope.sdk.client.requests.get")
    def test_list_sessions_empty(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: []
        )
        sdk = ChronoScopeSDK()
        sessions = sdk.list_sessions()
        assert sessions == []

    @patch("src.chronoscope.sdk.client.requests.get")
    def test_verify_audit_true(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"chain_intact": True}
        )
        sdk = ChronoScopeSDK()
        assert sdk.verify_audit() is True

    @patch("src.chronoscope.sdk.client.requests.get")
    def test_verify_audit_false_on_error(self, mock_get):
        mock_get.side_effect = Exception("error")
        sdk = ChronoScopeSDK()
        assert sdk.verify_audit() is False

    def test_headers_no_key(self):
        sdk = ChronoScopeSDK()
        headers = sdk._headers()
        assert "Authorization" not in headers

    def test_headers_with_key(self):
        sdk = ChronoScopeSDK(api_key="my-secret")
        headers = sdk._headers()
        assert headers["Authorization"] == "Bearer my-secret"