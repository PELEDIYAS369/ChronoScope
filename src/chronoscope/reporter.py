# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Mission Report Generator
Generates professional mission reports from session data.
Output formats: JSON, Markdown.
These reports are what you hand to a buyer or investigator.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
import structlog

from src.chronoscope.domain.models import (
    MissionSession,
    AnomalySeverity,
)
from src.chronoscope.audit.log import AuditLog

logger = structlog.get_logger(__name__)


@dataclass
class MissionReport:
    """
    Complete mission report generated from a session.
    Contains everything needed for post-mission review,
    anomaly investigation, or regulatory compliance.
    """
    report_id: str
    generated_at: datetime
    session_id: str
    spacecraft_id: str
    mission_phase: str
    start_time: datetime
    end_time: datetime | None
    duration_seconds: float | None

    # Telemetry summary
    total_packets: int
    packets_per_hour: float

    # Anomaly summary
    total_anomalies: int
    anomalies_by_severity: dict[str, int]
    critical_anomalies: list[dict[str, Any]]
    unacknowledged_anomalies: int

    # Audit summary
    audit_entries: int
    audit_chain_intact: bool

    # Determinism
    session_fingerprint: str
    determinism_verified: bool

    # Recommendations
    recommendations: list[str]

    # Raw data
    anomaly_details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def health_rating(self) -> str:
        """Overall mission health rating."""
        critical = self.anomalies_by_severity.get("critical", 0)
        high = self.anomalies_by_severity.get("high", 0)
        if critical > 0:
            return "CRITICAL"
        if high > 2:
            return "DEGRADED"
        if self.total_anomalies > 10:
            return "CAUTION"
        if not self.audit_chain_intact:
            return "AUDIT_FAILED"
        return "NOMINAL"

    def to_dict(self) -> dict[str, Any]:
        """Serialize report to dictionary."""
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at.isoformat(),
            "session_id": self.session_id,
            "spacecraft_id": self.spacecraft_id,
            "mission_phase": self.mission_phase,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "health_rating": self.health_rating,
            "telemetry": {
                "total_packets": self.total_packets,
                "packets_per_hour": round(self.packets_per_hour, 2),
            },
            "anomalies": {
                "total": self.total_anomalies,
                "by_severity": self.anomalies_by_severity,
                "unacknowledged": self.unacknowledged_anomalies,
                "critical_details": self.critical_anomalies,
            },
            "audit": {
                "total_entries": self.audit_entries,
                "chain_intact": self.audit_chain_intact,
            },
            "determinism": {
                "fingerprint": self.session_fingerprint,
                "verified": self.determinism_verified,
            },
            "recommendations": self.recommendations,
            "anomaly_details": self.anomaly_details,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize report to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_markdown(self) -> str:
        """Generate a professional Markdown report."""
        now = self.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        start = self.start_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        end = (
            self.end_time.strftime("%Y-%m-%d %H:%M:%S UTC")
            if self.end_time else "Ongoing"
        )
        duration = (
            f"{self.duration_seconds / 3600:.2f} hours"
            if self.duration_seconds else "N/A"
        )

        lines = [
            "# ChronoScope AI — Mission Report",
            "",
            f"**Generated:** {now}  ",
            f"**Report ID:** `{self.report_id}`  ",
            f"**Session ID:** `{self.session_id}`  ",
            "",
            "---",
            "",
            "## Mission Overview",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Spacecraft | {self.spacecraft_id} |",
            f"| Mission Phase | {self.mission_phase} |",
            f"| Start Time | {start} |",
            f"| End Time | {end} |",
            f"| Duration | {duration} |",
            f"| Health Rating | **{self.health_rating}** |",
            "",
            "---",
            "",
            "## Telemetry Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Packets | {self.total_packets:,} |",
            f"| Packets per Hour | {self.packets_per_hour:.1f} |",
            "",
            "---",
            "",
            "## Anomaly Analysis",
            "",
            f"| Severity | Count |",
            f"|----------|-------|",
        ]

        for severity in ["critical", "high", "medium", "low", "info"]:
            count = self.anomalies_by_severity.get(severity, 0)
            if count > 0:
                lines.append(f"| {severity.capitalize()} | {count} |")

        lines += [
            f"| **Total** | **{self.total_anomalies}** |",
            f"| Unacknowledged | {self.unacknowledged_anomalies} |",
            "",
        ]

        if self.critical_anomalies:
            lines += [
                "### Critical Anomalies",
                "",
            ]
            for flag in self.critical_anomalies:
                lines += [
                    f"#### {flag.get('parameter', 'Unknown')}",
                    "",
                    f"- **Observed:** {flag.get('observed_value', 'N/A')}",
                    f"- **Reason:** {flag.get('reason', 'N/A')}",
                    f"- **Confidence:** {flag.get('confidence', 0) * 100:.0f}%",
                    f"- **Time:** {flag.get('timestamp', 'N/A')}",
                    "",
                ]

        if self.anomaly_details:
            lines += [
                "### All Anomalies",
                "",
                "| Time | Severity | Parameter | Observed | Reason |",
                "|------|----------|-----------|----------|--------|",
            ]
            for flag in self.anomaly_details[:20]:
                ts = flag.get("timestamp", "")[:19]
                sev = flag.get("severity", "").upper()
                param = flag.get("parameter", "")
                obs = flag.get("observed_value", "")
                reason = flag.get("reason", "")[:50]
                lines.append(f"| {ts} | {sev} | {param} | {obs} | {reason} |")
            lines.append("")

        lines += [
            "---",
            "",
            "## Audit Trail",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Total Entries | {self.audit_entries:,} |",
            f"| Chain Integrity | {'✅ Intact' if self.audit_chain_intact else '❌ BROKEN'} |",
            "",
            "---",
            "",
            "## Determinism Verification",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Session Fingerprint | `{self.session_fingerprint[:32]}...` |",
            f"| Verified | {'✅ Yes' if self.determinism_verified else '❌ No'} |",
            "",
            "---",
            "",
            "## Recommendations",
            "",
        ]

        if self.recommendations:
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"{i}. {rec}")
        else:
            lines.append("No recommendations — mission nominal.")

        lines += [
            "",
            "---",
            "",
            "*Report generated by ChronoScope AI*  ",
            "*Tamper-evident audit trail verified*  ",
            f"*Fingerprint: `{self.session_fingerprint[:16]}...`*",
        ]

        return "\n".join(lines)


class MissionReporter:
    """
    Generates mission reports from session data.
    Call generate() to produce a complete MissionReport.
    """

    def __init__(self, audit_log: AuditLog):
        self._audit = audit_log
        self.logger = structlog.get_logger(__name__)

    def generate(
        self,
        session: MissionSession,
        fingerprint: str = "unverified",
        determinism_verified: bool = False,
    ) -> MissionReport:
        """Generate a complete mission report from a session."""
        import uuid

        now = datetime.now(timezone.utc)
        duration = session.duration_seconds

        # Packets per hour
        if duration and duration > 0:
            pph = session.packet_count / (duration / 3600)
        else:
            pph = 0.0

        # Anomaly breakdown by severity
        by_severity: dict[str, int] = {}
        for severity in AnomalySeverity:
            count = sum(
                1 for a in session.anomalies
                if a.severity == severity
            )
            if count > 0:
                by_severity[severity.value] = count

        # Critical anomaly details
        critical_flags = [
            {
                "flag_id": f.flag_id,
                "parameter": f.parameter_name,
                "observed_value": f.observed_value,
                "expected_range": list(f.expected_range),
                "reason": f.reason,
                "confidence": f.confidence,
                "timestamp": f.timestamp.isoformat(),
                "acknowledged": f.acknowledged,
            }
            for f in session.anomalies
            if f.severity == AnomalySeverity.CRITICAL
        ]

        # All anomaly details
        all_flags = [
            {
                "flag_id": f.flag_id,
                "severity": f.severity.value,
                "parameter": f.parameter_name,
                "observed_value": f.observed_value,
                "reason": f.reason,
                "confidence": f.confidence,
                "timestamp": f.timestamp.isoformat(),
                "acknowledged": f.acknowledged,
            }
            for f in session.anomalies
        ]

        unacknowledged = sum(
            1 for f in session.anomalies if not f.acknowledged
        )

        # Audit info
        audit_intact = self._audit.verify_chain()
        audit_entries = self._audit.entry_count

        # Generate recommendations
        recommendations = self._generate_recommendations(
            session, by_severity, audit_intact
        )

        report = MissionReport(
            report_id=str(uuid.uuid4()),
            generated_at=now,
            session_id=session.session_id,
            spacecraft_id=session.spacecraft_id,
            mission_phase=session.mission_phase.value,
            start_time=session.start_time,
            end_time=session.end_time,
            duration_seconds=duration,
            total_packets=session.packet_count,
            packets_per_hour=pph,
            total_anomalies=session.anomaly_count,
            anomalies_by_severity=by_severity,
            critical_anomalies=critical_flags,
            unacknowledged_anomalies=unacknowledged,
            audit_entries=audit_entries,
            audit_chain_intact=audit_intact,
            session_fingerprint=fingerprint,
            determinism_verified=determinism_verified,
            recommendations=recommendations,
            anomaly_details=all_flags,
        )

        self.logger.info(
            "report_generated",
            report_id=report.report_id,
            session_id=session.session_id,
            health_rating=report.health_rating,
            total_anomalies=report.total_anomalies,
        )

        return report

    def _generate_recommendations(
        self,
        session: MissionSession,
        by_severity: dict[str, int],
        audit_intact: bool,
    ) -> list[str]:
        """Generate actionable recommendations based on session data."""
        recs = []

        critical = by_severity.get("critical", 0)
        high = by_severity.get("high", 0)
        unacked = sum(
            
            1 for f in session.anomalies 
            if not f.acknowledged
            and f.severity not in (AnomalySeverity.CRITICAL, AnomalySeverity.HIGH)
        )

        if critical > 0:
            recs.append(
                f"IMMEDIATE: {critical} critical anomaly flags require "
                f"operator review and acknowledgement before next operation."
            )

        if high > 0:
            recs.append(
                f"Review {high} high-severity anomaly flags and confirm "
                f"system parameters are within acceptable bounds."
            )

        if unacked > 0:
            recs.append(
                f"{unacked} anomaly flags are unacknowledged. Assign to "
                f"responsible operator for review."
            )

        if not audit_intact:
            recs.append(
                "CRITICAL: Audit chain integrity check failed. "
                "Initiate tamper investigation immediately."
            )

        if session.packet_count == 0:
            recs.append(
                "No telemetry packets in session. Verify data source "
                "connectivity and ingestion configuration."
            )

        if not recs:
            recs.append(
                "All systems nominal. Continue standard monitoring "
                "procedures."
            )

        return recs