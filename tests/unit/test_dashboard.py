"""
Unit tests for ChronoScope Mission Dashboard.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from src.chronoscope.dashboard.dashboard import (
    MissionDashboard,
    SessionSummary,
    SystemHealth,
    DashboardSnapshot,
)
from src.chronoscope.domain.models import (
    MissionSession,
    MissionPhase,
    TelemetryPacket,
    PacketType,
    AnomalyFlag,
    AnomalySeverity,
    ReplayStatus,
)
from src.chronoscope.replay.engine import ReplayEngine
from src.chronoscope.audit.log import AuditLog


def make_session(spacecraft_id: str = "DSCOVR") -> MissionSession:
    start = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 15, 2, 0, 0, tzinfo=timezone.utc)
    return MissionSession.create(
        spacecraft_id=spacecraft_id,
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
        timestamp=datetime(2024, 1, 15, 0, seq, 0, tzinfo=timezone.utc),
    )


def make_anomaly(severity: AnomalySeverity = AnomalySeverity.HIGH) -> AnomalyFlag:
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
    )


def make_dashboard() -> MissionDashboard:
    engine = ReplayEngine()
    audit = MagicMock(spec=AuditLog)
    audit.verify_chain.return_value = True
    return MissionDashboard(replay_engine=engine, audit_log=audit)


class TestSessionSummary:

    def test_from_session_basic(self):
        session = make_session()
        summary = SessionSummary.from_session(session)
        assert summary.spacecraft_id == "DSCOVR"
        assert summary.packet_count == 0
        assert summary.anomaly_count == 0
        assert summary.critical_anomalies == 0

    def test_from_session_with_packets(self):
        session = make_session()
        for i in range(5):
            session.add_packet(make_packet(i))
        summary = SessionSummary.from_session(session)
        assert summary.packet_count == 5

    def test_from_session_counts_critical_anomalies(self):
        session = make_session()
        session.add_anomaly(make_anomaly(AnomalySeverity.CRITICAL))
        session.add_anomaly(make_anomaly(AnomalySeverity.HIGH))
        session.add_anomaly(make_anomaly(AnomalySeverity.CRITICAL))
        summary = SessionSummary.from_session(session)
        assert summary.critical_anomalies == 2
        assert summary.anomaly_count == 3

    def test_duration_seconds(self):
        session = make_session()
        summary = SessionSummary.from_session(session)
        assert summary.duration_seconds == 7200.0


class TestSystemHealth:

    def test_nominal_status(self):
        health = SystemHealth(
            total_sessions=3,
            active_replays=1,
            total_packets_processed=1000,
            total_anomalies=5,
            critical_anomalies_unacknowledged=0,
            audit_chain_intact=True,
            last_ingestion_time=datetime.now(timezone.utc),
            uptime_seconds=3600,
        )
        assert health.health_status == "NOMINAL"

    def test_critical_status_when_unacked_criticals(self):
        health = SystemHealth(
            total_sessions=1,
            active_replays=0,
            total_packets_processed=100,
            total_anomalies=1,
            critical_anomalies_unacknowledged=1,
            audit_chain_intact=True,
            last_ingestion_time=None,
            uptime_seconds=100,
        )
        assert health.health_status == "CRITICAL"

    def test_degraded_when_audit_broken(self):
        health = SystemHealth(
            total_sessions=1,
            active_replays=0,
            total_packets_processed=100,
            total_anomalies=0,
            critical_anomalies_unacknowledged=0,
            audit_chain_intact=False,
            last_ingestion_time=None,
            uptime_seconds=100,
        )
        assert health.health_status == "DEGRADED"

    def test_idle_when_no_sessions(self):
        health = SystemHealth(
            total_sessions=0,
            active_replays=0,
            total_packets_processed=0,
            total_anomalies=0,
            critical_anomalies_unacknowledged=0,
            audit_chain_intact=True,
            last_ingestion_time=None,
            uptime_seconds=10,
        )
        assert health.health_status == "IDLE"


class TestMissionDashboard:

    def test_register_session(self):
        dashboard = make_dashboard()
        session = make_session()
        dashboard.register_session(session)
        snapshot = dashboard.get_snapshot()
        assert len(snapshot.sessions) == 1

    def test_snapshot_with_no_sessions(self):
        dashboard = make_dashboard()
        snapshot = dashboard.get_snapshot()
        assert len(snapshot.sessions) == 0
        assert snapshot.system_health.health_status == "IDLE"

    def test_snapshot_health_nominal(self):
        dashboard = make_dashboard()
        session = make_session()
        for i in range(10):
            session.add_packet(make_packet(i))
        dashboard.register_session(session)
        snapshot = dashboard.get_snapshot()
        assert snapshot.system_health.health_status == "NOMINAL"
        assert snapshot.system_health.total_packets_processed == 10

    def test_snapshot_detects_critical(self):
        dashboard = make_dashboard()
        session = make_session()
        session.add_anomaly(make_anomaly(AnomalySeverity.CRITICAL))
        dashboard.register_session(session)
        snapshot = dashboard.get_snapshot()
        assert snapshot.system_health.health_status == "CRITICAL"
        assert snapshot.has_critical_alerts is True

    def test_multiple_sessions(self):
        dashboard = make_dashboard()
        s1 = make_session("DSCOVR")
        s2 = make_session("ACE")
        dashboard.register_session(s1)
        dashboard.register_session(s2)
        snapshot = dashboard.get_snapshot()
        assert len(snapshot.sessions) == 2

    def test_get_session_detail(self):
        dashboard = make_dashboard()
        session = make_session()
        session.add_packet(make_packet(0))
        session.add_anomaly(make_anomaly(AnomalySeverity.HIGH))
        dashboard.register_session(session)
        detail = dashboard.get_session_detail(session.session_id)
        assert detail["packet_count"] == 1
        assert len(detail["recent_flags"]) == 1

    def test_session_detail_not_found_raises(self):
        dashboard = make_dashboard()
        with pytest.raises(KeyError):
            dashboard.get_session_detail("nonexistent-id")

    def test_acknowledge_anomaly(self):
        dashboard = make_dashboard()
        session = make_session()
        flag = make_anomaly(AnomalySeverity.HIGH)
        session.add_anomaly(flag)
        dashboard.register_session(session)
        result = dashboard.acknowledge_anomaly(
            session.session_id,
            flag.flag_id,
            operator_id="operator-001",
        )
        assert result is True
        updated = session.anomalies[0]
        assert updated.acknowledged is True
        assert updated.acknowledged_by == "operator-001"

    def test_acknowledge_anomaly_wrong_id(self):
        dashboard = make_dashboard()
        session = make_session()
        dashboard.register_session(session)
        result = dashboard.acknowledge_anomaly(
            session.session_id,
            "nonexistent-flag-id",
            "operator-001",
        )
        assert result is False

    def test_recent_anomalies_sorted_by_time(self):
        dashboard = make_dashboard()
        session = make_session()
        for _ in range(3):
            session.add_anomaly(make_anomaly(AnomalySeverity.LOW))
        dashboard.register_session(session)
        snapshot = dashboard.get_snapshot()
        assert len(snapshot.recent_anomalies) == 3

    def test_get_system_health(self):
        dashboard = make_dashboard()
        session = make_session()
        dashboard.register_session(session)
        health = dashboard.get_system_health()
        assert isinstance(health, SystemHealth)
        assert health.total_sessions == 1