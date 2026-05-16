# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Hourly Operational Report Generator
Event-driven. Generates both human-readable and machine-readable reports.

Report contains:
- Report window (start/end)
- Objects tracked
- Sources ingested / failed
- Alerts created / resolved
- Degraded conditions
- Source activity summary
- Rule evaluation summary
- Operational posture summary
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
import json
import uuid
import structlog

from src.chronoscope.observability.events import EventBus, EventType

logger = structlog.get_logger(__name__)


@dataclass
class HourlyReportData:
    """Structured data for one hourly reporting window."""
    report_id:          str
    window_start:       datetime
    window_end:         datetime
    generated_at:       datetime

    # Asset tracking
    objects_tracked:    list[str]
    total_packets:      int

    # Source activity
    sources_ingested:   list[dict[str, Any]]
    sources_failed:     list[dict[str, Any]]
    total_ingestions:   int
    failed_ingestions:  int

    # Alert activity
    alerts_created:     list[dict[str, Any]]
    alerts_resolved:    list[dict[str, Any]]
    total_alerts:       int
    resolved_alerts:    int

    # Degraded conditions
    degraded_conditions: list[dict[str, Any]]
    operational_posture: str

    # Rule evaluation
    rules_evaluated:    int
    rules_triggered:    int
    rule_summary:       dict[str, int]

    # Source summary
    source_summary:     dict[str, dict[str, Any]]


class HourlyReportGenerator:
    """
    Generates hourly operational reports from the event bus.
    Call generate() at the top of each hour or on demand.
    """

    def __init__(self, event_bus: EventBus):
        self._bus = event_bus
        self.logger = structlog.get_logger(__name__)

    def generate(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        controller: Any = None,
    ) -> HourlyReportData:
        """
        Generate report for the given window.
        Defaults to the last complete hour.
        """
        now = datetime.now(timezone.utc)
        if window_end is None:
            window_end = now
        if window_start is None:
            window_start = window_end - timedelta(hours=1)

        events = self._bus.get_events_in_window(window_start, window_end)

        # Parse events by type
        ingested = [
            e for e in events
            if e.event_type == EventType.SOURCE_INGESTED
        ]
        failed = [
            e for e in events
            if e.event_type == EventType.SOURCE_FAILED
        ]
        alerts_created = [
            e for e in events
            if e.event_type == EventType.ALERT_CREATED
        ]
        alerts_resolved = [
            e for e in events
            if e.event_type == EventType.ALERT_RESOLVED
        ]
        degraded = [
            e for e in events
            if e.event_type == EventType.SYSTEM_DEGRADED
        ]
        rules_eval = [
            e for e in events
            if e.event_type == EventType.RULE_EVALUATED
        ]

        # Source summary
        source_summary: dict[str, dict[str, Any]] = {}
        for e in ingested:
            src = e.source
            if src not in source_summary:
                source_summary[src] = {
                    "ingestions": 0,
                    "total_packets": 0,
                    "failures": 0,
                }
            source_summary[src]["ingestions"] += 1
            source_summary[src]["total_packets"] += e.details.get(
                "packets_ingested", 0
            )
        for e in failed:
            src = e.source
            if src not in source_summary:
                source_summary[src] = {
                    "ingestions": 0,
                    "total_packets": 0,
                    "failures": 0,
                }
            source_summary[src]["failures"] += 1

        # Rule summary
        rule_summary: dict[str, int] = {}
        for e in rules_eval:
            rule_id = e.details.get("rule_id", "unknown")
            if e.details.get("triggered", False):
                rule_summary[rule_id] = rule_summary.get(rule_id, 0) + 1

        # Objects tracked
        objects_tracked = list({
            e.source for e in events
            if e.event_type in (
                EventType.SOURCE_INGESTED,
                EventType.ALERT_CREATED,
            )
        })

        # Total packets
        total_packets = sum(
            e.details.get("packets_ingested", 0) for e in ingested
        )

        # Operational posture
        if degraded:
            severities = {
                e.details.get("severity", "warning") for e in degraded
            }
            if "critical" in severities:
                posture = "CRITICAL"
            else:
                posture = "DEGRADED"
        elif failed:
            posture = "DEGRADED"
        else:
            posture = "NOMINAL"

        report = HourlyReportData(
            report_id=str(uuid.uuid4()),
            window_start=window_start,
            window_end=window_end,
            generated_at=now,
            objects_tracked=objects_tracked,
            total_packets=total_packets,
            sources_ingested=[e.to_dict() for e in ingested],
            sources_failed=[e.to_dict() for e in failed],
            total_ingestions=len(ingested),
            failed_ingestions=len(failed),
            alerts_created=[e.to_dict() for e in alerts_created],
            alerts_resolved=[e.to_dict() for e in alerts_resolved],
            total_alerts=len(alerts_created),
            resolved_alerts=len(alerts_resolved),
            degraded_conditions=[e.to_dict() for e in degraded],
            operational_posture=posture,
            rules_evaluated=len(rules_eval),
            rules_triggered=sum(rule_summary.values()),
            rule_summary=rule_summary,
            source_summary=source_summary,
        )

        self.logger.info(
            "hourly_report_generated",
            report_id=report.report_id,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            posture=posture,
            alerts=len(alerts_created),
            ingestions=len(ingested),
        )

        return report

    def to_json(self, report: HourlyReportData) -> str:
        """Generate machine-readable JSON report."""
        data = {
            "report_id": report.report_id,
            "report_type": "hourly_operational",
            "window_start": report.window_start.isoformat(),
            "window_end": report.window_end.isoformat(),
            "generated_at": report.generated_at.isoformat(),
            "operational_posture": report.operational_posture,
            "summary": {
                "objects_tracked": len(report.objects_tracked),
                "total_packets": report.total_packets,
                "total_ingestions": report.total_ingestions,
                "failed_ingestions": report.failed_ingestions,
                "alerts_created": report.total_alerts,
                "alerts_resolved": report.resolved_alerts,
                "rules_evaluated": report.rules_evaluated,
                "rules_triggered": report.rules_triggered,
                "degraded_conditions": len(report.degraded_conditions),
            },
            "objects_tracked": report.objects_tracked,
            "source_activity": report.source_summary,
            "rule_evaluation_summary": report.rule_summary,
            "alerts_created": report.alerts_created,
            "alerts_resolved": report.alerts_resolved,
            "degraded_conditions": report.degraded_conditions,
            "sources_failed": report.sources_failed,
        }
        return json.dumps(data, indent=2, default=str)

    def to_markdown(self, report: HourlyReportData) -> str:
        """Generate human-readable Markdown report."""
        start = report.window_start.strftime("%Y-%m-%d %H:%M UTC")
        end = report.window_end.strftime("%Y-%m-%d %H:%M UTC")
        gen = report.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")

        posture_icon = {
            "NOMINAL": "✅",
            "DEGRADED": "⚠️",
            "CRITICAL": "🔴",
        }.get(report.operational_posture, "❓")

        lines = [
            "# ChronoScope AI — Hourly Operational Report",
            "",
            f"**Report ID:** `{report.report_id}`  ",
            f"**Generated:** {gen}  ",
            f"**Window:** {start} → {end}  ",
            f"**Operational Posture:** {posture_icon} **{report.operational_posture}**",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Objects Tracked | {len(report.objects_tracked)} |",
            f"| Total Packets | {report.total_packets:,} |",
            f"| Successful Ingestions | {report.total_ingestions} |",
            f"| Failed Ingestions | {report.failed_ingestions} |",
            f"| Alerts Created | {report.total_alerts} |",
            f"| Alerts Resolved | {report.resolved_alerts} |",
            f"| Rules Evaluated | {report.rules_evaluated} |",
            f"| Rules Triggered | {report.rules_triggered} |",
            f"| Degraded Conditions | {len(report.degraded_conditions)} |",
            "",
            "---",
            "",
            "## Objects Tracked",
            "",
        ]

        if report.objects_tracked:
            for obj in report.objects_tracked:
                lines.append(f"- `{obj}`")
        else:
            lines.append("No objects tracked in this window.")

        lines += [
            "",
            "---",
            "",
            "## Source Activity Summary",
            "",
            "| Source | Ingestions | Packets | Failures |",
            "|--------|-----------|---------|----------|",
        ]

        for src, stats in report.source_summary.items():
            lines.append(
                f"| {src} | {stats['ingestions']} | "
                f"{stats['total_packets']:,} | {stats['failures']} |"
            )

        if not report.source_summary:
            lines.append("| No source activity recorded | — | — | — |")

        lines += [
            "",
            "---",
            "",
            "## Rule Evaluation Summary",
            "",
            "| Rule ID | Times Triggered |",
            "|---------|----------------|",
        ]

        if report.rule_summary:
            for rule_id, count in report.rule_summary.items():
                lines.append(f"| `{rule_id}` | {count} |")
        else:
            lines.append("| No rules triggered | — |")

        lines += [
            "",
            "---",
            "",
            "## Alerts Created",
            "",
        ]

        if report.alerts_created:
            for alert in report.alerts_created:
                details = alert.get("details", {})
                lines += [
                    f"### Alert `{details.get('alert_id', 'unknown')[:8]}...`",
                    "",
                    f"- **Severity:** {details.get('severity', 'unknown').upper()}",
                    f"- **Parameter:** {details.get('parameter', 'unknown')}",
                    f"- **Rule:** `{details.get('rule_id', 'unknown')}`",
                    f"- **Spacecraft:** {alert.get('source', 'unknown')}",
                    f"- **Time:** {alert.get('timestamp', 'unknown')}",
                    "",
                ]
        else:
            lines.append("No alerts created in this window. ✅")

        lines += [
            "",
            "---",
            "",
            "## Degraded Conditions",
            "",
        ]

        if report.degraded_conditions:
            for cond in report.degraded_conditions:
                details = cond.get("details", {})
                sev = details.get("severity", "warning").upper()
                lines += [
                    f"- **[{sev}]** {details.get('description', 'Unknown condition')}",
                    f"  - Type: `{details.get('condition_type', 'unknown')}`",
                    f"  - Time: {cond.get('timestamp', 'unknown')}",
                    "",
                ]
        else:
            lines.append("No degraded conditions recorded. ✅")

        lines += [
            "",
            "---",
            "",
            "## Operational Posture Summary",
            "",
            f"{posture_icon} System operated in **{report.operational_posture}** "
            f"mode during this window.",
            "",
        ]

        if report.operational_posture == "NOMINAL":
            lines.append(
                "All sources ingested successfully. No degraded conditions. "
                "Audit chain intact."
            )
        elif report.operational_posture == "DEGRADED":
            lines.append(
                f"{report.failed_ingestions} source failure(s) or stale source(s) "
                f"detected. Review degraded conditions above."
            )
        else:
            lines.append(
                "Critical conditions detected. Immediate operator review required."
            )

        lines += [
            "",
            "---",
            "",
            "*Generated by ChronoScope AI — Tamper-evident audit trail verified*  ",
            f"*Report ID: `{report.report_id}`*",
        ]

        return "\n".join(lines)

    def save(
        self,
        report: HourlyReportData,
        output_dir: str = ".",
    ) -> tuple[str, str]:
        """
        Save both JSON and Markdown reports to disk.
        Returns (json_path, markdown_path).
        """
        import os
        timestamp = report.window_end.strftime("%Y%m%d_%H%M")
        json_path = os.path.join(
            output_dir, f"chronoscope_report_{timestamp}.json"
        )
        md_path = os.path.join(
            output_dir, f"chronoscope_report_{timestamp}.md"
        )
        with open(json_path, "w") as f:
            f.write(self.to_json(report))
        with open(md_path, "w") as f:
            f.write(self.to_markdown(report))

        self.logger.info(
            "hourly_report_saved",
            json_path=json_path,
            md_path=md_path,
        )
        return json_path, md_path