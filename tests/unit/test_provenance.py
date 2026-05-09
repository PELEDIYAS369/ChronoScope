"""
Unit tests for source trust policy and provenance.
"""

import pytest
from datetime import datetime, timezone, timedelta
from src.chronoscope.domain.provenance import SourceTrustPolicy
from src.chronoscope.domain.models import SourceTrustLevel, DegradedCondition


def make_fresh_timestamp() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def make_stale_timestamp() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=6)


class TestSourceTrustPolicy:

    def test_fresh_source_is_verified_public(self):
        policy = SourceTrustPolicy()
        prov = policy.evaluate("noaa_dscovr", make_fresh_timestamp())
        assert prov.source_trust_level == SourceTrustLevel.VERIFIED_PUBLIC
        assert prov.stale is False

    def test_stale_source_demoted(self):
        policy = SourceTrustPolicy()
        prov = policy.evaluate("noaa_dscovr", make_stale_timestamp())
        assert prov.source_trust_level == SourceTrustLevel.STALE_PUBLIC
        assert prov.stale is True

    def test_stale_source_reduces_confidence(self):
        policy = SourceTrustPolicy()
        fresh = policy.evaluate("noaa_dscovr", make_fresh_timestamp(), 1.0)
        stale = policy.evaluate("noaa_dscovr", make_stale_timestamp(), 1.0)
        assert stale.confidence_score < fresh.confidence_score

    def test_confidence_capped_at_1(self):
        policy = SourceTrustPolicy()
        prov = policy.evaluate("noaa_dscovr", make_fresh_timestamp(), 1.5)
        assert prov.confidence_score <= 1.0

    def test_record_source_unavailable(self):
        policy = SourceTrustPolicy()
        condition = policy.record_source_unavailable(
            "noaa_dscovr", "Connection timeout"
        )
        assert condition.condition_type == "source_unavailable"
        assert condition.source_name == "noaa_dscovr"
        assert condition.severity == "error"

    def test_record_stale_creates_degraded_condition(self):
        policy = SourceTrustPolicy()
        policy.evaluate("noaa_dscovr", make_stale_timestamp())
        conditions = policy.get_active_conditions()
        assert len(conditions) == 1
        assert conditions[0].condition_type == "stale_source"

    def test_no_conditions_when_fresh(self):
        policy = SourceTrustPolicy()
        policy.evaluate("noaa_dscovr", make_fresh_timestamp())
        assert len(policy.get_active_conditions()) == 0

    def test_operational_state_nominal_when_no_conditions(self):
        policy = SourceTrustPolicy()
        assert policy.get_operational_state() == "nominal"

    def test_operational_state_degraded_when_stale(self):
        policy = SourceTrustPolicy()
        policy.evaluate("noaa_dscovr", make_stale_timestamp())
        assert policy.get_operational_state() == "degraded"

    def test_operational_state_critical_when_critical_condition(self):
        policy = SourceTrustPolicy()
        policy._record_degraded(
            "source_unavailable", "noaa_dscovr", "Down", "critical"
        )
        assert policy.get_operational_state() == "critical"

    def test_resolve_condition(self):
        policy = SourceTrustPolicy()
        cond = policy.record_source_unavailable("noaa_dscovr", "Timeout")
        result = policy.resolve_condition(cond.condition_id)
        assert result is True
        assert len(policy.get_active_conditions()) == 0

    def test_resolve_nonexistent_condition(self):
        policy = SourceTrustPolicy()
        result = policy.resolve_condition("nonexistent-id")
        assert result is False

    def test_record_invalid_propagation(self):
        policy = SourceTrustPolicy()
        cond = policy.record_invalid_propagation("noaa_dscovr", "NaN in output")
        assert cond.condition_type == "invalid_propagation"

    def test_record_missing_inputs(self):
        policy = SourceTrustPolicy()
        cond = policy.record_missing_inputs(
            "noaa_dscovr", ["density", "speed"]
        )
        assert cond.condition_type == "missing_inputs"
        assert "density" in cond.description

    def test_provenance_to_dict(self):
        policy = SourceTrustPolicy()
        prov = policy.evaluate("noaa_dscovr", make_fresh_timestamp())
        d = prov.to_dict()
        assert "source_name" in d
        assert "source_trust_level" in d
        assert "confidence_score" in d
        assert "stale" in d
        assert "ingestion_timestamp" in d
        assert "propagation_timestamp" in d

    def test_unknown_source_gets_default_trust(self):
        policy = SourceTrustPolicy()
        prov = policy.evaluate("unknown_source_xyz", make_fresh_timestamp())
        assert prov.source_trust_level == SourceTrustLevel.VERIFIED_PUBLIC

    def test_multiple_stale_sources_tracked(self):
        policy = SourceTrustPolicy()
        policy.evaluate("noaa_dscovr", make_stale_timestamp())
        policy.evaluate("ace_spacecraft", make_stale_timestamp())
        conditions = policy.get_active_conditions()
        assert len(conditions) == 2