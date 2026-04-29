"""
ChronoScope AI — Mission Dashboard
Unified view of all active sessions, anomaly flags,
audit status, and system health.
This is the single screen that replaces ten fragmented tools.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import structlog

from src.chronoscope.domain.models import (
    MissionSession,
    AnomalyFlag,
    AnomalySeverity,
    ReplayStatus,
)
from src.chronoscope.replay.engine import ReplayEngine
from src.chronoscope.audit.log import AuditLog

logger = structlog.get_logger(__name__)


@dataclass
class SessionSummary:
    """Lightweight summary of a session for dashboard display."""
    session_id: str
    spacecraft_id: str
    mission_phase: str
    replay_status: str
    packet_count: int
    anomaly_count: int
    critical_anomalies: int
    start_time: datetime
    end_time: datetime | None
    duration_seconds: float | None
    last_updated: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @classmethod
    def from_session(cls, session: MissionSession) -> SessionSummary:
        critical = sum(
            1 for a in session.anomalies
            if a.severity == AnomalySeverity.CRITICAL
        )
        return cls(
            session_id=session.session_id,
            spacecraft_id=session.spacecraft_id,
            mission_phase=session.mission_phase.value,
            replay_status=session.replay_status.value,
            packet_count=session.packet_count,
            anomaly_count=session.anomaly_count,
            critical_anomalies=critical,
            start_time=session.start_time,
            end_time=session.end_time,
            duration_seconds=session.duration_seconds,
        )


@dataclass
class SystemHealth:
    """Overall system health snapshot."""
    total_sessions: int
    active_replays: int
    total_packets_processed: int
    total_anomalies: int
    critical_anomalies_unacknowledged: int
    audit_chain_intact: bool
    last_ingestion_time: datetime | None
    uptime_seconds: float
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def health_status(self) -> str:
        if self.critical_anomalies_unacknowledged > 0:
            return "CRITICAL"
        if not self.audit_chain_intact:
            return "DEGRADED"
        if self.total_sessions == 0:
            return "IDLE"
        return "NOMINAL"


@dataclass
class DashboardSnapshot:
    """
    Complete dashboard state at a single point in time.
    Everything needed to render the full mission dashboard.
    """
    generated_at: datetime
    system_health: SystemHealth
    sessions: list[SessionSummary]
    recent_anomalies: list[dict[str, Any]]
    active_session_id: str | None = None

    @property
    def has_critical_alerts(self) -> bool:
        return self.system_health.critical_anomalies_unacknowledged > 0


class MissionDashboard:
    """
    Unified mission dashboard.
    Aggregates data from replay engine, audit log,
    and all active sessions into a single coherent view.
    """

    def __init__(
        self,
        replay_engine: ReplayEngine,
        audit_log: AuditLog,
    ):
        self._engine = replay_engine
        self._audit = audit_log
        self._sessions: dict[str, MissionSession] = {}
        self._start_time = datetime.now(timezone.utc)
        self._last_ingestion: datetime | None = None
        self.logger = structlog.get_logger(__name__)

    def register_session(self, session: MissionSession) -> None:
        """Register a session with the dashboard for tracking."""
        self._sessions[session.session_id] = session
        self._last_ingestion = datetime.now(timezone.utc)
        self.logger.info(
            "session_registered",
            session_id=session.session_id,
            spacecraft_id=session.spacecraft_id,
        )

    def get_snapshot(self) -> DashboardSnapshot:
        """
        Generate a complete dashboard snapshot right now.
        Call this to refresh the dashboard view.
        """
        now = datetime.now(timezone.utc)
        summaries = [
            SessionSummary.from_session(s)
            for s in self._sessions.values()
        ]

        total_packets = sum(s.packet_count for s in summaries)
        total_anomalies = sum(s.anomaly_count for s in summaries)
        critical_unacked = sum(
            s.critical_anomalies for s in summaries
        )
        active_replays = sum(
            1 for s in summaries
            if s.replay_status == ReplayStatus.PLAYING.value
        )

        audit_intact = self._check_audit_integrity()

        health = SystemHealth(
            total_sessions=len(self._sessions),
            active_replays=active_replays,
            total_packets_processed=total_packets,
            total_anomalies=total_anomalies,
            critical_anomalies_unacknowledged=critical_unacked,
            audit_chain_intact=audit_intact,
            last_ingestion_time=self._last_ingestion,
            uptime_seconds=(now - self._start_time).total_seconds(),
        )

        recent_anomalies = self._get_recent_anomalies(limit=10)

        snapshot = DashboardSnapshot(
            generated_at=now,
            system_health=health,
            sessions=summaries,
            recent_anomalies=recent_anomalies,
        )

        self.logger.info(
            "dashboard_snapshot_generated",
            sessions=len(summaries),
            health_status=health.health_status,
            total_packets=total_packets,
            critical_alerts=critical_unacked,
        )

        return snapshot

    def get_session_detail(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        """Get detailed view of a single session."""
        if session_id not in self._sessions:
            raise KeyError(f"Session not found: {session_id}")

        session = self._sessions[session_id]
        summary = SessionSummary.from_session(session)

        anomalies_by_severity: dict[str, int] = {}
        for severity in AnomalySeverity:
            count = sum(
                1 for a in session.anomalies
                if a.severity == severity
            )
            if count > 0:
                anomalies_by_severity[severity.value] = count

        recent_flags = []
        for flag in session.anomalies[-5:]:
            recent_flags.append({
                "flag_id": flag.flag_id,
                "severity": flag.severity.value,
                "parameter": flag.parameter_name,
                "observed": flag.observed_value,
                "reason": flag.reason,
                "timestamp": flag.timestamp.isoformat(),
                "acknowledged": flag.acknowledged,
                "suggested_actions": getattr(flag, "suggested_actions", []),
            })

        return {
            "summary": summary,
            "anomalies_by_severity": anomalies_by_severity,
            "recent_flags": recent_flags,
            "packet_count": session.packet_count,
            "event_count": len(session.events),
        }

    def get_system_health(self) -> SystemHealth:
        """Get current system health without full snapshot."""
        snapshot = self.get_snapshot()
        return snapshot.system_health

    def acknowledge_anomaly(
        self,
        session_id: str,
        flag_id: str,
        operator_id: str,
    ) -> bool:
        """Mark an anomaly flag as acknowledged by an operator."""
        if session_id not in self._sessions:
            return False

        session = self._sessions[session_id]
        for i, flag in enumerate(session.anomalies):
            if flag.flag_id == flag_id:
                from dataclasses import replace
                updated = replace(
                    flag,
                    acknowledged=True,
                    acknowledged_by=operator_id,
                )
                session.anomalies[i] = updated
                self.logger.info(
                    "anomaly_acknowledged",
                    flag_id=flag_id,
                    operator_id=operator_id,
                    session_id=session_id,
                )
                return True
        return False

    def _check_audit_integrity(self) -> bool:
        """Verify audit log chain is intact."""
        try:
            return self._audit.verify_chain()
        except Exception:
            return False

    def _get_recent_anomalies(
        self,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get most recent anomalies across all sessions."""
        all_flags: list[tuple[datetime, AnomalyFlag, str]] = []
        for session_id, session in self._sessions.items():
            for flag in session.anomalies:
                all_flags.append((flag.timestamp, flag, session_id))

        all_flags.sort(key=lambda x: x[0], reverse=True)

        result = []
        for ts, flag, session_id in all_flags[:limit]:
            result.append({
                "session_id": session_id,
                "flag_id": flag.flag_id,
                "severity": flag.severity.value,
                "parameter": flag.parameter_name,
                "reason": flag.reason,
                "timestamp": ts.isoformat(),
                "acknowledged": flag.acknowledged,
            })
        return result