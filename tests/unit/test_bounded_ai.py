"""
Unit tests for bounded AI interface.
Verifies AI cannot be authoritative and always includes uncertainty.
"""

import pytest
from unittest.mock import MagicMock
from src.chronoscope.ai.bounded_interface import BoundedAIInterface, BoundedAIOutput


def make_controller():
    ctrl = MagicMock()
    ctrl.list_sessions.return_value = [
        {
            "session_id": "session-abc",
            "spacecraft_id": "DSCOVR",
            "replay_status": "ready",
            "packet_count": 223,
            "anomaly_count": 3,
        }
    ]
    ctrl.get_anomalies.return_value = [
        {
            "flag_id": "flag-001",
            "severity": "medium",
            "parameter": "ion_temperature_k",
            "observed_value": 562201.0,
            "reason": "Ion temperature 12.4% above threshold",
            "confidence": 86,
            "timestamp": "2026-05-09T06:00:00+00:00",
            "suggested_actions": [],
        }
    ]
    ctrl.status.return_value = {
        "sessions": 2,
        "ingester": "noaa_dscovr",
        "audit_entries": 42,
        "audit_chain_intact": True,
        "detector_rules": 7,
        "ai_critical": 0,
    }
    ctrl.get_health.return_value = {
        "status": "NOMINAL",
        "sessions_loaded": 2,
        "total_packets": 500,
        "audit_intact": True,
    }
    ctrl._detector._rules = []
    return ctrl


class TestBoundedAIOutput:

    def test_create_valid_output(self):
        output = BoundedAIOutput(
            explanation="Ion temperature elevated",
            operational_context="HSS arrival pattern",
            uncertainty="Root cause unknown",
            confidence=0.86,
            supporting_data=["ion_temperature_k: 562201"],
            limitations=["Cannot determine hardware cause"],
        )
        assert output.explanation == "Ion temperature elevated"
        assert output.confidence == 0.86

    def test_empty_explanation_rejected(self):
        with pytest.raises(ValueError, match="explanation"):
            BoundedAIOutput(
                explanation="",
                operational_context="context",
                uncertainty="unknown",
                confidence=0.8,
                supporting_data=[],
                limitations=[],
            )

    def test_empty_uncertainty_rejected(self):
        with pytest.raises(ValueError, match="uncertainty"):
            BoundedAIOutput(
                explanation="something",
                operational_context="context",
                uncertainty="",
                confidence=0.8,
                supporting_data=[],
                limitations=[],
            )

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValueError, match="confidence"):
            BoundedAIOutput(
                explanation="something",
                operational_context="context",
                uncertainty="unknown",
                confidence=1.5,
                supporting_data=[],
                limitations=[],
            )

    def test_to_dict_has_required_keys(self):
        output = BoundedAIOutput(
            explanation="Ion temperature elevated",
            operational_context="HSS arrival",
            uncertainty="Root cause unknown",
            confidence=0.86,
            supporting_data=["param: value"],
            limitations=["Cannot infer hardware state"],
        )
        d = output.to_dict()
        assert "explanation" in d
        assert "operational_context" in d
        assert "uncertainty" in d
        assert "confidence" in d
        assert "limitations" in d
        assert "ai_is_authoritative" in d
        assert d["ai_is_authoritative"] is False

    def test_ai_is_never_authoritative(self):
        output = BoundedAIOutput(
            explanation="test",
            operational_context="context",
            uncertainty="unknown",
            confidence=0.9,
            supporting_data=[],
            limitations=[],
        )
        assert output.to_dict()["ai_is_authoritative"] is False


class TestBoundedAIInterface:

    def test_get_object_state_known(self):
        ai = BoundedAIInterface(make_controller())
        state = ai.get_object_state("DSCOVR")
        assert state["data_available"] is True
        assert state["spacecraft_id"] == "DSCOVR"
        assert state["packet_count"] == 223

    def test_get_object_state_unknown(self):
        ai = BoundedAIInterface(make_controller())
        state = ai.get_object_state("UNKNOWN_SC")
        assert state["data_available"] is False
        assert state["state"] == "unknown"

    def test_get_object_alerts(self):
        ai = BoundedAIInterface(make_controller())
        result = ai.get_object_alerts("DSCOVR")
        assert result["data_available"] is True
        assert result["alert_count"] == 1

    def test_get_object_alerts_unknown(self):
        ai = BoundedAIInterface(make_controller())
        result = ai.get_object_alerts("UNKNOWN_SC")
        assert result["data_available"] is False
        assert result["alert_count"] == 0

    def test_get_source_snapshot(self):
        ai = BoundedAIInterface(make_controller())
        snap = ai.get_source_snapshot("noaa_dscovr")
        assert snap["data_available"] is True
        assert "sessions" in snap

    def test_get_system_status(self):
        ai = BoundedAIInterface(make_controller())
        status = ai.get_system_status()
        assert status["data_available"] is True
        assert "operational_state" in status
        assert "audit_intact" in status

    def test_get_rule_definition_not_found(self):
        ai = BoundedAIInterface(make_controller())
        result = ai.get_rule_definition("nonexistent-rule")
        assert result["data_available"] is False

    def test_explain_alert_returns_bounded_output(self):
        ai = BoundedAIInterface(make_controller())
        alert = {
            "spacecraft_id": "DSCOVR",
            "parameter": "ion_temperature_k",
            "observed_value": 562201.0,
            "reason": "Temperature 12.4% above threshold",
            "severity": "medium",
            "confidence": 86,
        }
        state = {"packet_count": 223}
        output = ai.explain_alert(alert, state)
        assert isinstance(output, BoundedAIOutput)
        assert output.explanation != ""
        assert output.uncertainty != ""
        assert output.confidence <= 0.95
        assert len(output.limitations) > 0
        assert output.to_dict()["ai_is_authoritative"] is False

    def test_explain_alert_always_has_limitations(self):
        ai = BoundedAIInterface(make_controller())
        alert = {
            "spacecraft_id": "DSCOVR",
            "parameter": "bulk_speed_km_s",
            "observed_value": 650.0,
            "reason": "Speed above threshold",
            "severity": "high",
            "confidence": 92,
        }
        output = ai.explain_alert(alert, {"packet_count": 100})
        assert len(output.limitations) >= 3

    def test_low_packet_count_noted_in_uncertainty(self):
        ai = BoundedAIInterface(make_controller())
        alert = {
            "spacecraft_id": "DSCOVR",
            "parameter": "ion_temperature_k",
            "observed_value": 510000.0,
            "reason": "Above threshold",
            "severity": "medium",
            "confidence": 80,
        }
        output = ai.explain_alert(alert, {"packet_count": 3})
        assert "3" in output.operational_context or "3" in output.uncertainty


class TestBoundedAIReadOnly:
    """Verify AI interface only reads — never writes."""

    def test_get_object_state_does_not_modify(self):
        ctrl = make_controller()
        ai = BoundedAIInterface(ctrl)
        ai.get_object_state("DSCOVR")
        ctrl.create_session.assert_not_called()
        ctrl.ingest.assert_not_called()
        ctrl.analyze.assert_not_called()

    def test_get_system_status_does_not_modify(self):
        ctrl = make_controller()
        ai = BoundedAIInterface(ctrl)
        ai.get_system_status()
        ctrl.create_session.assert_not_called()
        ctrl.ingest.assert_not_called()