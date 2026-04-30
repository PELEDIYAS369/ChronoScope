"""
Unit tests for ChronoScope Mission Reporter.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from src.chronoscope.reporter import MissionReporter, MissionReport
from src.chronoscope.domain.models import (
    MissionSession,
    MissionPhase,
    TelemetryPacket,
    PacketType,
    AnomalyFlag,
    AnomalySeverity,
)
from src.chronoscope.audit.log import AuditLog


def make_session() -> MissionSession:
    start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 15, 2, 0, 0, tzinfo=timezone.utc)
    return MissionSession.create(
        spacecraft_id="DSCOVR",
        mission_phase=MissionPhase.NOMINAL,
        start_time=start,
        end_time=end,
    )


def make_packet(seq: int) -> TelemetryPacket:
    return TelemetryPacket.create(
        spacecraft_id="DSCOVR",
        packet_type=PacketType.TELEMETRY,
        apid=100,
        sequence_count=seq % 16384,
        raw_bytes=b"\x00",
        parameters={"value": float(seq)},
        source="test",
        timestamp=datetime(2024, 1, 15, 0, seq % 60, 0, tzinfo=timezone.utc),
    )


def make_anomaly(severity: AnomalySeverity, acked: bool = False) -> AnomalyFlag:
    import uuid
    return AnomalyFlag(
        flag_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        spacecraft_id="DSCOVR",
        severity=severity,
        parameter_name="voltage",
        observed_value=15.9,
        expected_range=(10.0, 15.0),
        reason="Voltage exceeded upper limit",
        confidence=0.92,
        source_packet_id="pkt-001",
        acknowledged=acked,
    )


def make_reporter() -> MissionReporter:
    audit = MagicMock(spec=AuditLog)
    audit.verify_chain.return_value = True
    audit.entry_count = 42
    return MissionReporter(audit_log=audit)


class TestMissionReport:

    def test_health_rating_nominal(self):
        report = MissionReport(
            report_id="r1",
            generated_at=datetime.now(timezone.utc),
            session_id="s1",
            spacecraft_id="DSCOVR",
            mission_phase="nominal",
            start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 15, 2, tzinfo=timezone.utc),
            duration_seconds=7200,
            total_packets=100,
            packets_per_hour=50.0,
            total_anomalies=0,
            anomalies_by_severity={},
            critical_anomalies=[],
            unacknowledged_anomalies=0,
            audit_entries=10,
            audit_chain_intact=True,
            session_fingerprint="abc123",
            determinism_verified=True,
            recommendations=[],
        )
        assert report.health_rating == "NOMINAL"

    def test_health_rating_critical(self):
        report = MissionReport(
            report_id="r1",
            generated_at=datetime.now(timezone.utc),
            session_id="s1",
            spacecraft_id="DSCOVR",
            mission_phase="nominal",
            start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_time=None,
            duration_seconds=None,
            total_packets=100,
            packets_per_hour=50.0,
            total_anomalies=1,
            anomalies_by_severity={"critical": 1},
            critical_anomalies=[],
            unacknowledged_anomalies=1,
            audit_entries=10,
            audit_chain_intact=True,
            session_fingerprint="abc123",
            determinism_verified=True,
            recommendations=[],
        )
        assert report.health_rating == "CRITICAL"

    def test_health_rating_audit_failed(self):
        report = MissionReport(
            report_id="r1",
            generated_at=datetime.now(timezone.utc),
            session_id="s1",
            spacecraft_id="DSCOVR",
            mission_phase="nominal",
            start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_time=None,
            duration_seconds=None,
            total_packets=0,
            packets_per_hour=0,
            total_anomalies=0,
            anomalies_by_severity={},
            critical_anomalies=[],
            unacknowledged_anomalies=0,
            audit_entries=0,
            audit_chain_intact=False,
            session_fingerprint="abc123",
            determinism_verified=False,
            recommendations=[],
        )
        assert report.health_rating == "AUDIT_FAILED"

    def test_to_dict_has_required_keys(self):
        reporter = make_reporter()
        session = make_session()
        report = reporter.generate(session)
        d = report.to_dict()
        assert "report_id" in d
        assert "health_rating" in d
        assert "telemetry" in d
        assert "anomalies" in d
        assert "audit" in d
        assert "determinism" in d
        assert "recommendations" in d

    def test_to_json_is_valid(self):
        import json
        reporter = make_reporter()
        session = make_session()
        report = reporter.generate(session)
        raw = report.to_json()
        parsed = json.loads(raw)
        assert parsed["spacecraft_id"] == "DSCOVR"

    def test_to_markdown_contains_sections(self):
        reporter = make_reporter()
        session = make_session()
        report = reporter.generate(session)
        md = report.to_markdown()
        assert "# ChronoScope AI" in md
        assert "## Mission Overview" in md
        assert "## Anomaly Analysis" in md
        assert "## Audit Trail" in md
        assert "## Recommendations" in md


class TestMissionReporter:

    def test_generate_basic_report(self):
        reporter = make_reporter()
        session = make_session()
        report = reporter.generate(session)
        assert report.spacecraft_id == "DSCOVR"
        assert report.session_id == session.session_id
        assert report.total_packets == 0
        assert report.total_anomalies == 0
        assert report.audit_chain_intact is True

    def test_generate_with_packets(self):
        reporter = make_reporter()
        session = make_session()
        for i in range(10):
            session.add_packet(make_packet(i))
        report = reporter.generate(session)
        assert report.total_packets == 10
        assert report.packets_per_hour > 0

    def test_generate_with_anomalies(self):
        reporter = make_reporter()
        session = make_session()
        session.add_anomaly(make_anomaly(AnomalySeverity.CRITICAL))
        session.add_anomaly(make_anomaly(AnomalySeverity.HIGH))
        session.add_anomaly(make_anomaly(AnomalySeverity.LOW))
        report = reporter.generate(session)
        assert report.total_anomalies == 3
        assert report.anomalies_by_severity.get("critical") == 1
        assert report.anomalies_by_severity.get("high") == 1
        assert len(report.critical_anomalies) == 1
        assert report.health_rating == "CRITICAL"

    def test_unacknowledged_count(self):
        reporter = make_reporter()
        session = make_session()
        session.add_anomaly(make_anomaly(AnomalySeverity.HIGH, acked=False))
        session.add_anomaly(make_anomaly(AnomalySeverity.HIGH, acked=True))
        report = reporter.generate(session)
        assert report.unacknowledged_anomalies == 1

    def test_recommendations_nominal(self):
        reporter = make_reporter()
        session = make_session()
        session.add_packet(make_packet(0))
        report = reporter.generate(session)
        assert len(report.recommendations) == 1
        assert "nominal" in report.recommendations[0].lower()

    def test_recommendations_critical_anomaly(self):
        reporter = make_reporter()
        session = make_session()
        session.add_anomaly(make_anomaly(AnomalySeverity.CRITICAL))
        report = reporter.generate(session)
        assert any("IMMEDIATE" in r for r in report.recommendations)

    def test_recommendations_broken_audit(self):
        audit = MagicMock(spec=AuditLog)
        audit.verify_chain.return_value = False
        audit.entry_count = 0
        reporter = MissionReporter(audit_log=audit)
        session = make_session()
        report = reporter.generate(session)
        assert any("tamper" in r.lower() for r in report.recommendations)

    def test_report_id_is_unique(self):
        reporter = make_reporter()
        session = make_session()
        r1 = reporter.generate(session)
        r2 = reporter.generate(session)
        assert r1.report_id != r2.report_id

    def test_duration_in_report(self):
        reporter = make_reporter()
        session = make_session()
        report = reporter.generate(session)
        assert report.duration_seconds == 7200.0

    def test_fingerprint_stored(self):
        reporter = make_reporter()
        session = make_session()
        report = reporter.generate(
            session,
            fingerprint="abc123def456",
            determinism_verified=True,
        )
        assert report.session_fingerprint == "abc123def456"
        assert report.determinism_verified is True
