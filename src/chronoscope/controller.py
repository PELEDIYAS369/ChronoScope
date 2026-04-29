"""
ChronoScope AI — System Controller
The single entry point that coordinates all subsystems.

Ingestion → Session → Replay → AI Detection → Audit

Every operation is logged to the audit trail.
This is what an operator or API layer talks to.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any
import structlog

from src.chronoscope.domain.models import (
    MissionSession,
    MissionPhase,
    AnomalySeverity,
)
from src.chronoscope.domain.exceptions import SessionNotFoundError
from src.chronoscope.ingestion.base import BaseIngester, IngestionResult
from src.chronoscope.ingestion.noaa_dscovr import NOAADscovrIngester
from src.chronoscope.replay.engine import ReplayEngine
from src.chronoscope.replay.cursor import ReplayCursor
from src.chronoscope.audit.log import AuditLog, AuditEventType
from src.chronoscope.ai.detector import (
    AnomalyDetector,
    AnomalyReport,
    build_dscovr_rules,
)

logger = structlog.get_logger(__name__)


class ChronoScopeController:
    """
    Unified controller for all ChronoScope operations.

    Coordinates:
    - Data ingestion from any source
    - Mission session lifecycle
    - Deterministic replay engine
    - Tamper-evident audit logging
    - AI anomaly detection with suggestions

    Every significant operation is recorded in the audit log.
    The controller is the single source of truth for system state.
    """

    def __init__(
        self,
        ingester: BaseIngester | None = None,
        detector: AnomalyDetector | None = None,
        audit_log: AuditLog | None = None,
    ):
        self._ingester = ingester or NOAADscovrIngester()
        self._detector = detector or AnomalyDetector(
            rules=build_dscovr_rules()
        )
        self._audit = audit_log or AuditLog()
        self._replay = ReplayEngine()
        self._sessions: dict[str, MissionSession] = {}
        self.logger = structlog.get_logger(__name__)

        # Record system startup
        self._audit.record(
            event_type=AuditEventType.SYSTEM_STARTUP,
            actor="system",
            details={
                "ingester": self._ingester.source_name,
                "rules": self._detector.rule_count,
            },
        )

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    def create_session(
        self,
        spacecraft_id: str,
        mission_phase: MissionPhase,
        start_time: datetime,
        end_time: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> MissionSession:
        """Create and register a new mission session."""
        session = MissionSession.create(
            spacecraft_id=spacecraft_id,
            mission_phase=mission_phase,
            start_time=start_time,
            end_time=end_time,
            metadata=metadata or {},
        )

        self._sessions[session.session_id] = session

        self._audit.record(
            event_type=AuditEventType.SESSION_CREATED,
            actor=actor,
            details={
                "spacecraft_id": spacecraft_id,
                "mission_phase": mission_phase.value,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat() if end_time else None,
            },
            session_id=session.session_id,
            spacecraft_id=spacecraft_id,
        )

        self.logger.info(
            "session_created",
            session_id=session.session_id,
            spacecraft_id=spacecraft_id,
        )

        return session

    def get_session(self, session_id: str) -> MissionSession:
        """Get a session by ID."""
        if session_id not in self._sessions:
            raise SessionNotFoundError(session_id)
        return self._sessions[session_id]

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions with summary info."""
        return [
            {
                "session_id": s.session_id,
                "spacecraft_id": s.spacecraft_id,
                "mission_phase": s.mission_phase.value,
                "packet_count": s.packet_count,
                "anomaly_count": s.anomaly_count,
                "replay_status": s.replay_status.value,
            }
            for s in self._sessions.values()
        ]

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(
        self,
        session_id: str,
        start_time: datetime,
        end_time: datetime,
        actor: str = "system",
    ) -> IngestionResult:
        """Ingest telemetry data into a session."""
        session = self.get_session(session_id)

        self._audit.record(
            event_type=AuditEventType.INGESTION_STARTED,
            actor=actor,
            details={
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "source": self._ingester.source_name,
            },
            session_id=session_id,
            spacecraft_id=session.spacecraft_id,
        )

        result = self._ingester.ingest_into_session(
            session, start_time, end_time
        )

        event_type = (
            AuditEventType.INGESTION_COMPLETED
            if result.success
            else AuditEventType.INGESTION_FAILED
        )

        self._audit.record(
            event_type=event_type,
            actor=actor,
            details={
                "packets_ingested": result.packets_ingested,
                "packets_failed": result.packets_failed,
                "success_rate": result.success_rate,
                "duration_seconds": result.duration_seconds,
                "errors": result.errors,
            },
            session_id=session_id,
            spacecraft_id=session.spacecraft_id,
        )

        return result

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def load_replay(
        self,
        session_id: str,
        actor: str = "system",
    ) -> ReplayCursor:
        """Load a session into the replay engine."""
        session = self.get_session(session_id)
        cursor = self._replay.load_session(session)

        self._audit.record(
            event_type=AuditEventType.SESSION_LOADED,
            actor=actor,
            details={
                "packet_count": session.packet_count,
                "start_time": cursor.start_time.isoformat(),
                "end_time": cursor.end_time.isoformat(),
            },
            session_id=session_id,
            spacecraft_id=session.spacecraft_id,
        )

        return cursor

    def play(
        self, session_id: str, actor: str = "operator"
    ) -> ReplayCursor:
        cursor = self._replay.play(session_id)
        self._audit.record(
            AuditEventType.REPLAY_STARTED, actor,
            {"speed": cursor.speed}, session_id=session_id,
        )
        return cursor

    def pause(
        self, session_id: str, actor: str = "operator"
    ) -> ReplayCursor:
        cursor = self._replay.pause(session_id)
        self._audit.record(
            AuditEventType.REPLAY_PAUSED, actor,
            {"index": cursor.current_index,
             "progress_pct": cursor.progress * 100},
            session_id=session_id,
        )
        return cursor

    def seek(
        self,
        session_id: str,
        target_time: datetime,
        actor: str = "operator",
    ) -> ReplayCursor:
        cursor = self._replay.seek(session_id, target_time)
        self._audit.record(
            AuditEventType.REPLAY_SEEKED, actor,
            {"target_time": target_time.isoformat(),
             "landed_index": cursor.current_index},
            session_id=session_id,
        )
        return cursor

    def step_forward(
        self, session_id: str, actor: str = "operator"
    ) -> ReplayCursor:
        return self._replay.step_forward(session_id)

    def step_backward(
        self, session_id: str, actor: str = "operator"
    ) -> ReplayCursor:
        return self._replay.step_backward(session_id)

    def set_speed(
        self,
        session_id: str,
        speed: float,
        actor: str = "operator",
    ) -> ReplayCursor:
        cursor = self._replay.set_speed(session_id, speed)
        self._audit.record(
            AuditEventType.REPLAY_SPEED_CHANGED, actor,
            {"speed": speed}, session_id=session_id,
        )
        return cursor

    def verify_determinism(self, session_id: str) -> bool:
        result = self._replay.verify_determinism(session_id)
        self._audit.record(
            AuditEventType.DETERMINISM_VERIFIED, "system",
            {"result": result}, session_id=session_id,
        )
        return result

    # ------------------------------------------------------------------
    # AI Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        session_id: str,
        actor: str = "ai_engine",
    ) -> list[AnomalyReport]:
        """Run AI anomaly detection on a session."""
        session = self.get_session(session_id)
        reports = self._detector.analyze_session(session)

        for report in reports:
            self._audit.record(
                event_type=AuditEventType.ANOMALY_DETECTED,
                actor=actor,
                details={
                    "parameter": report.flag.parameter_name,
                    "severity": report.flag.severity.value,
                    "confidence": report.flag.confidence,
                    "urgency_hours": report.urgency_hours,
                    "recommended_action": (
                        report.recommended_action.title
                        if report.recommended_action else None
                    ),
                    "top_success_rate": (
                        report.recommended_action.success_rate
                        if report.recommended_action else None
                    ),
                },
                session_id=session_id,
                spacecraft_id=session.spacecraft_id,
            )

        return reports

    def operator_decides(
        self,
        flag_id: str,
        action_id: str,
        actor: str,
        session_id: str | None = None,
    ) -> AnomalyReport | None:
        """Record an operator's decision on an anomaly flag."""
        report = self._detector.record_operator_decision(
            flag_id, action_id, actor
        )

        if report:
            self._audit.record(
                event_type=AuditEventType.OPERATOR_ACTION_TAKEN,
                actor=actor,
                details={
                    "flag_id": flag_id,
                    "action_id": action_id,
                    "action_title": (
                        report.operator_decision
                    ),
                },
                session_id=session_id,
            )

        return report

    def record_outcome(
        self,
        flag_id: str,
        success: bool,
        description: str,
        actor: str = "system",
        session_id: str | None = None,
    ) -> AnomalyReport | None:
        """Record the outcome of an operator action."""
        report = self._detector.record_outcome(
            flag_id, success, description
        )

        if report:
            self._audit.record(
                event_type=AuditEventType.OPERATOR_ACTION_OUTCOME,
                actor=actor,
                details={
                    "flag_id": flag_id,
                    "success": success,
                    "description": description,
                },
                session_id=session_id,
            )

        return report

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def verify_audit_chain(self) -> bool:
        """Verify the entire audit chain is intact."""
        return self._audit.verify_chain()

    def export_audit(self) -> str:
        """Export the full audit log as JSON."""
        exported = self._audit.export_json()
        self._audit.record(
            AuditEventType.AUDIT_EXPORTED,
            actor="system",
            details={"entry_count": self._audit.entry_count},
        )
        return exported

    def audit_summary(self) -> dict[str, Any]:
        return self._audit.get_summary()

    # ------------------------------------------------------------------
    # System Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return complete system status."""
        return {
            "sessions": len(self._sessions),
            "audit_entries": self._audit.entry_count,
            "audit_chain_intact": self._audit.verify_chain(),
            "ai_reports": self._detector.report_count,
            "ai_unacknowledged": len(
                self._detector.get_unacknowledged_reports()
            ),
            "ai_critical": len(self._detector.get_critical_reports()),
            "detector_rules": self._detector.rule_count,
            "ingester": self._ingester.source_name,
        }

    # ------------------------------------------------------------------
    # CLI / Dashboard Helpers
    # ------------------------------------------------------------------

    def get_health(self) -> dict[str, Any]:
        """Health check used by CLI and dashboard."""
        s = self.status()
        return {
            "status": "CRITICAL" if s["ai_critical"] > 0 else "NOMINAL",
            "sessions_loaded": s["sessions"],
            "total_packets": sum(
                sess.packet_count for sess in self._sessions.values()
            ),
            "total_anomalies": sum(
                sess.anomaly_count for sess in self._sessions.values()
            ),
            "audit_intact": s["audit_chain_intact"],
            "uptime_seconds": 0.0,
        }

    def replay_summary(self, session_id: str) -> dict[str, Any]:
        """Summary of a loaded replay session for CLI display."""
        session = self.get_session(session_id)
        fingerprint = self._replay._replay_hashes.get(session_id, "not-loaded")
        return {
            "packet_count": session.packet_count,
            "duration_seconds": session.duration_seconds or 0.0,
            "start_time": session.start_time.isoformat(),
            "end_time": session.end_time.isoformat() if session.end_time else None,
            "anomaly_count": session.anomaly_count,
            "fingerprint": fingerprint,
        }

    def get_anomalies(self, session_id: str) -> list[dict[str, Any]]:
        """Get all anomaly flags for a session, formatted for CLI display."""
        session = self.get_session(session_id)
        result = []
        for flag in session.anomalies:
            actions = []
            for report in self._detector._reports.values():
                if report.flag.flag_id == flag.flag_id:
                    actions = [
                        {
                            "title": a.title,
                            "success_rate": a.success_rate,
                        }
                        for a in report.suggested_actions
                    ]
                    break
            result.append({
                "flag_id": flag.flag_id,
                "severity": flag.severity.value,
                "parameter": flag.parameter_name,
                "observed_value": flag.observed_value,
                "reason": flag.reason,
                "timestamp": flag.timestamp.isoformat(),
                "acknowledged": flag.acknowledged,
                "suggested_actions": actions,
            })
        return result

    def get_audit_status(self) -> dict[str, Any]:
        """Audit log status for CLI display."""
        summary = self.audit_summary()
        return {
            "chain_intact": self._audit.verify_chain(),
            "entry_count": self._audit.entry_count,
            "algorithm": "sha256",
            **summary,
        }

    def export_session(self, session_id: str) -> dict[str, Any]:
        """Export a session to a JSON-serializable dict."""
        session = self.get_session(session_id)
        return {
            "session_id": session.session_id,
            "spacecraft_id": session.spacecraft_id,
            "mission_phase": session.mission_phase.value,
            "start_time": session.start_time.isoformat(),
            "end_time": session.end_time.isoformat() if session.end_time else None,
            "packet_count": session.packet_count,
            "anomaly_count": session.anomaly_count,
            "packets": [
                {
                    "packet_id": p.packet_id,
                    "timestamp": p.timestamp.isoformat(),
                    "apid": p.apid,
                    "packet_type": p.packet_type.value,
                    "parameters": p.parameters,
                }
                for p in session.packets
            ],
            "anomalies": [
                {
                    "flag_id": f.flag_id,
                    "severity": f.severity.value,
                    "parameter": f.parameter_name,
                    "observed_value": f.observed_value,
                    "reason": f.reason,
                    "timestamp": f.timestamp.isoformat(),
                }
                for f in session.anomalies
            ],
        }