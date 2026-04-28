"""
Unit tests for ChronoScope AI anomaly detection engine.
Tests detection, suggestions, success rates, and operator workflow.
"""

import pytest
from datetime import datetime, timezone
from src.chronoscope.ai.detector import (
    AnomalyDetector,
    AnomalyReport,
    SuggestedAction,
    DetectionRule,
    build_dscovr_rules,
)
from src.chronoscope.domain.models import (
    TelemetryPacket,
    MissionSession,
    MissionPhase,
    PacketType,
    AnomalySeverity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_packet(parameters: dict, apid: int = 0x64) -> TelemetryPacket:
    return TelemetryPacket.create(
        spacecraft_id="DSCOVR",
        packet_type=PacketType.TELEMETRY,
        apid=apid,
        sequence_count=1,
        raw_bytes=b"\x00",
        parameters=parameters,
        source="test",
        timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# SuggestedAction Tests
# ---------------------------------------------------------------------------

class TestSuggestedAction:

    def test_create_action(self):
        action = SuggestedAction.create(
            title="Safe mode",
            description="Switch to safe mode",
            steps=["Step 1", "Step 2"],
            success_rate=0.94,
            time_required_minutes=12.0,
            risk_if_skipped="Battery damage",
        )
        assert action.title == "Safe mode"
        assert action.success_rate == 0.94
        assert len(action.steps) == 2
        assert action.action_id is not None

    def test_invalid_success_rate_rejected(self):
        with pytest.raises(ValueError):
            SuggestedAction.create(
                title="Test",
                description="Test",
                steps=[],
                success_rate=1.5,
                time_required_minutes=1.0,
                risk_if_skipped="none",
            )

    def test_to_dict_contains_pct(self):
        action = SuggestedAction.create(
            title="Test",
            description="Test",
            steps=["do it"],
            success_rate=0.942,
            time_required_minutes=5.0,
            risk_if_skipped="risk",
        )
        d = action.to_dict()
        assert d["success_rate_pct"] == "94.2%"

    def test_action_is_immutable(self):
        action = SuggestedAction.create(
            title="Test", description="d",
            steps=[], success_rate=0.9,
            time_required_minutes=1.0,
            risk_if_skipped="r",
        )
        with pytest.raises(Exception):
            action.success_rate = 0.5


# ---------------------------------------------------------------------------
# AnomalyDetector — Solar Wind Speed Tests
# ---------------------------------------------------------------------------

class TestSolarWindSpeedDetection:

    def test_nominal_speed_no_flag(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 450.0})
        reports = detector.analyze_packet(packet)
        assert len(reports) == 0

    def test_high_speed_flagged(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 687.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        assert len(speed_reports) >= 1

    def test_high_speed_has_severity_high(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        assert speed_reports[0].flag.severity == AnomalySeverity.HIGH

    def test_high_speed_has_reason(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        assert len(speed_reports[0].flag.reason) > 10

    def test_high_speed_has_suggested_actions(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        assert len(speed_reports[0].suggested_actions) >= 2

    def test_actions_have_success_rates(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        for action in speed_reports[0].suggested_actions:
            assert 0.0 <= action.success_rate <= 1.0

    def test_recommended_action_highest_success_rate(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        report = speed_reports[0]
        recommended = report.recommended_action
        assert recommended is not None
        # Recommended should have highest success rate
        max_rate = max(a.success_rate for a in report.suggested_actions)
        assert recommended.success_rate == max_rate

    def test_urgency_is_set(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        assert speed_reports[0].urgency_hours > 0

    def test_similar_events_count_present(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        assert speed_reports[0].similar_events_count > 0


# ---------------------------------------------------------------------------
# Magnetic Field Bz — Critical Detection
# ---------------------------------------------------------------------------

class TestMagneticFieldDetection:

    def test_nominal_bz_no_flag(self):
        detector = AnomalyDetector()
        packet = make_packet({"bz_gsm_nt": -5.0}, apid=0x65)
        reports = detector.analyze_packet(packet)
        bz_reports = [
            r for r in reports
            if r.flag.parameter_name == "bz_gsm_nt"
        ]
        assert len(bz_reports) == 0

    def test_strong_southward_bz_critical(self):
        detector = AnomalyDetector()
        packet = make_packet({"bz_gsm_nt": -25.0}, apid=0x65)
        reports = detector.analyze_packet(packet)
        bz_reports = [
            r for r in reports
            if r.flag.parameter_name == "bz_gsm_nt"
        ]
        assert len(bz_reports) >= 1
        assert bz_reports[0].flag.severity == AnomalySeverity.CRITICAL

    def test_critical_bz_high_urgency(self):
        detector = AnomalyDetector()
        packet = make_packet({"bz_gsm_nt": -30.0}, apid=0x65)
        reports = detector.analyze_packet(packet)
        bz_reports = [
            r for r in reports
            if r.flag.parameter_name == "bz_gsm_nt"
        ]
        assert bz_reports[0].urgency_hours <= 1.0


# ---------------------------------------------------------------------------
# Operator Workflow Tests
# ---------------------------------------------------------------------------

class TestOperatorWorkflow:

    def test_record_operator_decision(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        report = speed_reports[0]
        action_id = report.suggested_actions[0].action_id
        flag_id = report.flag.flag_id

        result = detector.record_operator_decision(
            flag_id=flag_id,
            action_id=action_id,
            actor="flight_controller_chen",
        )

        assert result is not None
        assert result.operator_decision == action_id
        assert result.operator_actor == "flight_controller_chen"

    def test_record_outcome(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        report = speed_reports[0]
        flag_id = report.flag.flag_id

        result = detector.record_outcome(
            flag_id=flag_id,
            success=True,
            description="Safe mode activated. Spacecraft nominal.",
        )

        assert result is not None
        assert result.outcome_success is True
        assert "nominal" in result.outcome

    def test_unacknowledged_reports(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        detector.analyze_packet(packet)
        unacked = detector.get_unacknowledged_reports()
        assert len(unacked) > 0

    def test_after_decision_not_unacknowledged(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        report = speed_reports[0]
        detector.record_operator_decision(
            flag_id=report.flag.flag_id,
            action_id=report.suggested_actions[0].action_id,
            actor="controller",
        )
        unacked = detector.get_unacknowledged_reports()
        ids = [r.flag.flag_id for r in unacked]
        assert report.flag.flag_id not in ids


# ---------------------------------------------------------------------------
# Session Analysis Tests
# ---------------------------------------------------------------------------

class TestSessionAnalysis:

    def test_analyze_clean_session(self):
        detector = AnomalyDetector()
        session = MissionSession.create(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime(2024, 1, 15, 12, 0, 0,
                                tzinfo=timezone.utc),
        )
        for i in range(5):
            session.add_packet(make_packet(
                {"bulk_speed_km_s": 450.0,
                 "proton_density_n_cc": 5.0}
            ))
        reports = detector.analyze_session(session)
        assert len(reports) == 0
        assert session.anomaly_count == 0

    def test_analyze_session_with_anomaly(self):
        detector = AnomalyDetector()
        session = MissionSession.create(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime(2024, 1, 15, 12, 0, 0,
                                tzinfo=timezone.utc),
        )
        session.add_packet(make_packet({"bulk_speed_km_s": 450.0}))
        session.add_packet(make_packet({"bulk_speed_km_s": 700.0}))
        session.add_packet(make_packet({"bulk_speed_km_s": 450.0}))
        reports = detector.analyze_session(session)
        assert len(reports) >= 1
        assert session.anomaly_count >= 1

    def test_summary_statistics(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        detector.analyze_packet(packet)
        summary = detector.summary()
        assert summary["total_reports"] >= 1
        assert "by_severity" in summary
        assert summary["rule_count"] > 0


# ---------------------------------------------------------------------------
# Display Formatting Test
# ---------------------------------------------------------------------------

class TestDisplayFormat:

    def test_format_for_display(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        formatted = speed_reports[0].format_for_display()
        assert "ANOMALY DETECTED" in formatted
        assert "WHAT HAPPENED" in formatted
        assert "WHY IT MATTERS" in formatted
        assert "SUGGESTED ACTIONS" in formatted
        assert "Success rate:" in formatted
        assert "RECOMMENDED" in formatted
        assert "Human operator decision required" in formatted

    def test_to_dict_complete(self):
        detector = AnomalyDetector()
        packet = make_packet({"bulk_speed_km_s": 700.0})
        reports = detector.analyze_packet(packet)
        speed_reports = [
            r for r in reports
            if r.flag.parameter_name == "bulk_speed_km_s"
        ]
        d = speed_reports[0].to_dict()
        assert "what_happened" in d
        assert "why_it_matters" in d
        assert "suggested_actions" in d
        assert "recommended_action_id" in d
        assert "urgency_hours" in d
        assert "similar_events_count" in d