"""
Unit tests for auditable alert system.
"""

import pytest
from datetime import datetime, timezone
from src.chronoscope.domain.alerts import AuditableAlert, AlertRegistry
from src.chronoscope.domain.models import AnomalySeverity, SourceTrustLevel
from src.chronoscope.domain.provenance import SourceTrustPolicy


def make_provenance():
    policy = SourceTrustPolicy()
    return policy.evaluate(
        "noaa_dscovr",
        datetime.now(timezone.utc),
        raw_confidence=0.90,
    )


def make_alert(**kwargs) -> AuditableAlert:
    defaults = dict(
        rule_id="dscovr-temp-extreme",
        rule_version="1.0",
        rule_name="Ion Temperature Anomaly",
        source_snapshot_id="snapshot-001",
        state_version="v1",
        spacecraft_id="DSCOVR",
        parameter_name="ion_temperature_k",
        observed_value=562201.0,
        expected_range=(0.0, 500000.0),
        severity=AnomalySeverity.MEDIUM,
        reason="Ion temperature 12.4% above threshold",
        confidence=0.86,
        urgency_hours=8.0,
        provenance=make_provenance(),
    )
    defaults.update(kwargs)
    return AuditableAlert.create(**defaults)


class TestAuditableAlert:

    def test_create_alert(self):
        alert = make_alert()
        assert alert.alert_id is not None
        assert alert.spacecraft_id == "DSCOVR"
        assert alert.parameter_name == "ion_temperature_k"
        assert alert.resolved is False

    def test_empty_reason_rejected(self):
        with pytest.raises(ValueError, match="reason"):
            make_alert(reason="")

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValueError, match="confidence"):
            make_alert(confidence=1.5)

    def test_negative_confidence_rejected(self):
        with pytest.raises(ValueError, match="confidence"):
            make_alert(confidence=-0.1)

    def test_alert_is_immutable(self):
        alert = make_alert()
        with pytest.raises(Exception):
            alert.spacecraft_id = "MODIFIED"

    def test_to_dict_has_required_keys(self):
        alert = make_alert()
        d = alert.to_dict()
        assert "alert_id" in d
        assert "rule_id" in d
        assert "rule_version" in d
        assert "source_snapshot_id" in d
        assert "state_version" in d
        assert "reason" in d
        assert "confidence" in d
        assert "provenance" in d
        assert "created_at" in d

    def test_to_dict_provenance_present(self):
        alert = make_alert()
        d = alert.to_dict()
        prov = d["provenance"]
        assert "source_name" in prov
        assert "source_trust_level" in prov
        assert "confidence_score" in prov

    def test_alert_preserves_rule_version(self):
        alert = make_alert(rule_version="2.3.1")
        assert alert.rule_version == "2.3.1"
        assert alert.to_dict()["rule_version"] == "2.3.1"

    def test_alert_preserves_source_snapshot(self):
        alert = make_alert(source_snapshot_id="snap-abc-123")
        assert alert.source_snapshot_id == "snap-abc-123"

    def test_alert_preserves_state_version(self):
        alert = make_alert(state_version="v42")
        assert alert.state_version == "v42"

    def test_two_alerts_have_unique_ids(self):
        a1 = make_alert()
        a2 = make_alert()
        assert a1.alert_id != a2.alert_id


class TestAlertRegistry:

    def test_register_alert(self):
        registry = AlertRegistry()
        alert = make_alert()
        registry.register(alert)
        assert registry.active_count == 1

    def test_resolve_alert(self):
        registry = AlertRegistry()
        alert = make_alert()
        registry.register(alert)
        resolved = registry.resolve(
            alert.alert_id,
            resolved_by="operator-001",
            resolution_note="Confirmed HSS event",
        )
        assert resolved is not None
        assert resolved.resolved is True
        assert resolved.resolved_by == "operator-001"
        assert registry.active_count == 0
        assert registry.resolved_count == 1

    def test_resolve_nonexistent_returns_none(self):
        registry = AlertRegistry()
        result = registry.resolve("nonexistent-id", "operator-001")
        assert result is None

    def test_get_active(self):
        registry = AlertRegistry()
        a1 = make_alert()
        a2 = make_alert()
        registry.register(a1)
        registry.register(a2)
        active = registry.get_active()
        assert len(active) == 2

    def test_get_resolved(self):
        registry = AlertRegistry()
        alert = make_alert()
        registry.register(alert)
        registry.resolve(alert.alert_id, "operator-001")
        resolved = registry.get_resolved()
        assert len(resolved) == 1

    def test_get_by_id_active(self):
        registry = AlertRegistry()
        alert = make_alert()
        registry.register(alert)
        found = registry.get_by_id(alert.alert_id)
        assert found is not None
        assert found.alert_id == alert.alert_id

    def test_get_by_id_resolved(self):
        registry = AlertRegistry()
        alert = make_alert()
        registry.register(alert)
        registry.resolve(alert.alert_id, "operator-001")
        found = registry.get_by_id(alert.alert_id)
        assert found is not None
        assert found.resolved is True

    def test_get_by_spacecraft(self):
        registry = AlertRegistry()
        a1 = make_alert(spacecraft_id="DSCOVR")
        a2 = make_alert(spacecraft_id="ACE")
        registry.register(a1)
        registry.register(a2)
        dscovr_alerts = registry.get_by_spacecraft("DSCOVR")
        assert len(dscovr_alerts) == 1
        assert dscovr_alerts[0].spacecraft_id == "DSCOVR"

    def test_cannot_resolve_twice(self):
        registry = AlertRegistry()
        alert = make_alert()
        registry.register(alert)
        registry.resolve(alert.alert_id, "operator-001")
        result = registry.resolve(alert.alert_id, "operator-002")
        assert result is None