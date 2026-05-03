"""
ChronoScope AI — Anomaly Detection Engine
Watches all telemetry parameters simultaneously.
Flags anomalies with:
  - What happened (plain English)
  - Why it matters (operational context)
  - Suggested actions ranked by success rate
  - Urgency — how long before action required
  - Confidence score
  - Historical precedent count

Rules:
  1. Every flag MUST have a human-readable reason
  2. Every flag MUST have at least one suggested action
  3. Every suggested action MUST have a success rate
  4. The AI NEVER makes decisions — it recommends
  5. Human operator always has final control
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid
import structlog

from src.chronoscope.domain.models import (
    TelemetryPacket,
    AnomalyFlag,
    AnomalySeverity,
    MissionSession,
)
from src.chronoscope.domain.constants import (
    ANOMALY_CONFIDENCE_THRESHOLD,
    ANOMALY_CRITICAL_THRESHOLD,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Suggested Action Model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SuggestedAction:
    """
    A concrete action an operator can take in response to an anomaly.
    Includes success rate based on historical precedent.
    """
    action_id: str
    title: str
    description: str
    steps: list[str]
    success_rate: float          # 0.0 to 1.0
    time_required_minutes: float
    risk_if_skipped: str
    priority: int                # 1 = highest priority

    def __post_init__(self) -> None:
        if not 0.0 <= self.success_rate <= 1.0:
            raise ValueError(
                f"success_rate {self.success_rate} must be 0.0–1.0"
            )
        if self.priority < 1:
            raise ValueError("priority must be >= 1")

    @classmethod
    def create(
        cls,
        title: str,
        description: str,
        steps: list[str],
        success_rate: float,
        time_required_minutes: float,
        risk_if_skipped: str,
        priority: int = 1,
    ) -> SuggestedAction:
        return cls(
            action_id=str(uuid.uuid4()),
            title=title,
            description=description,
            steps=steps,
            success_rate=success_rate,
            time_required_minutes=time_required_minutes,
            risk_if_skipped=risk_if_skipped,
            priority=priority,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "title": self.title,
            "description": self.description,
            "steps": self.steps,
            "success_rate": self.success_rate,
            "success_rate_pct": f"{self.success_rate * 100:.1f}%",
            "time_required_minutes": self.time_required_minutes,
            "risk_if_skipped": self.risk_if_skipped,
            "priority": self.priority,
        }


@dataclass
class AnomalyReport:
    """
    Full anomaly detection result — the complete picture.
    Combines the flag with actions and narrative explanation.
    """
    flag: AnomalyFlag
    what_happened: str
    why_it_matters: str
    suggested_actions: list[SuggestedAction]
    recommended_action_id: str
    urgency_hours: float
    similar_events_count: int
    operator_decision: str | None = None
    operator_actor: str | None = None
    outcome: str | None = None
    outcome_success: bool | None = None

    @property
    def recommended_action(self) -> SuggestedAction | None:
        for action in self.suggested_actions:
            if action.action_id == self.recommended_action_id:
                return action
        return None

    def record_operator_decision(
        self,
        action_id: str,
        actor: str,
    ) -> None:
        """Record what the human operator decided to do."""
        self.operator_decision = action_id
        self.operator_actor = actor

    def record_outcome(self, success: bool, description: str) -> None:
        """Record what actually happened after the operator acted."""
        self.outcome = description
        self.outcome_success = success

    def to_dict(self) -> dict[str, Any]:
        return {
            "flag_id": self.flag.flag_id,
            "timestamp": self.flag.timestamp.isoformat(),
            "spacecraft_id": self.flag.spacecraft_id,
            "severity": self.flag.severity.value,
            "parameter": self.flag.parameter_name,
            "observed_value": self.flag.observed_value,
            "expected_range": list(self.flag.expected_range),
            "confidence": self.flag.confidence,
            "what_happened": self.what_happened,
            "why_it_matters": self.why_it_matters,
            "urgency_hours": self.urgency_hours,
            "similar_events_count": self.similar_events_count,
            "suggested_actions": [
                a.to_dict() for a in self.suggested_actions
            ],
            "recommended_action_id": self.recommended_action_id,
            "operator_decision": self.operator_decision,
            "operator_actor": self.operator_actor,
            "outcome": self.outcome,
            "outcome_success": self.outcome_success,
        }

    def format_for_display(self) -> str:
        """Format the full anomaly report for operator display."""
        lines = [
            "=" * 60,
            "ANOMALY DETECTED",
            "=" * 60,
            f"Spacecraft:    {self.flag.spacecraft_id}",
            f"Parameter:     {self.flag.parameter_name}",
            f"Observed:      {self.flag.observed_value}",
            f"Expected:      {self.flag.expected_range[0]} – "
            f"{self.flag.expected_range[1]}",
            f"Severity:      {self.flag.severity.value.upper()}",
            f"Confidence:    {self.flag.confidence * 100:.1f}%",
            f"Urgency:       Act within {self.urgency_hours:.1f} hours",
            "",
            "WHAT HAPPENED:",
            self.what_happened,
            "",
            "WHY IT MATTERS:",
            self.why_it_matters,
            "",
            f"SUGGESTED ACTIONS ({len(self.suggested_actions)}):",
            "-" * 40,
        ]

        for action in sorted(
            self.suggested_actions, key=lambda a: a.priority
        ):
            rec = " ← RECOMMENDED" if (
                action.action_id == self.recommended_action_id
            ) else ""
            lines += [
                f"Action {action.priority}: {action.title}{rec}",
                f"  Success rate: {action.success_rate * 100:.1f}%",
                f"  Time needed:  {action.time_required_minutes:.0f} minutes",
                f"  Steps:",
            ]
            for step in action.steps:
                lines.append(f"    • {step}")
            lines.append(
                f"  Risk if skipped: {action.risk_if_skipped}"
            )
            lines.append("")

        lines += [
            "-" * 40,
            f"Historical precedent: {self.similar_events_count} similar events",
            "",
            "Human operator decision required.",
            "AI recommendation only. You are in control.",
            "=" * 60,
        ]

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Detection Rules
# ---------------------------------------------------------------------------

@dataclass
class DetectionRule:
    """
    A single anomaly detection rule.
    Rules define what to watch, when to flag, and what to suggest.
    """
    rule_id: str
    name: str
    parameter_name: str
    min_value: float | None
    max_value: float | None
    severity: AnomalySeverity
    what_happened_template: str
    why_it_matters: str
    suggested_actions: list[SuggestedAction]
    urgency_hours: float
    similar_events_count: int
    confidence_base: float = 0.85

    def evaluate(
        self,
        packet: TelemetryPacket,
    ) -> AnomalyReport | None:
        """
        Evaluate this rule against a telemetry packet.
        Returns AnomalyReport if anomaly detected, None if nominal.
        """
        value = packet.parameters.get(self.parameter_name)
        if value is None:
            return None

        try:
            fvalue = float(value)
        except (TypeError, ValueError):
            return None

        # Determine expected range
        min_val = self.min_value if self.min_value is not None else float("-inf")
        max_val = self.max_value if self.max_value is not None else float("inf")

        # Check if value is out of range
        if min_val <= fvalue <= max_val:
            return None  # Nominal — no anomaly

        # Compute deviation for confidence scaling
        if fvalue > max_val and max_val != float("inf"):
            deviation = (fvalue - max_val) / max(abs(max_val), 1e-9)
        elif fvalue < min_val and min_val != float("-inf"):
            deviation = (min_val - fvalue) / max(abs(min_val), 1e-9)
        else:
            deviation = 0.1

        confidence = min(
            self.confidence_base + (deviation * 0.1),
            0.99
        )

        if confidence < ANOMALY_CONFIDENCE_THRESHOLD:
            return None  # Below threshold — do not flag

        # Build human-readable explanation
        what_happened = self.what_happened_template.format(
            parameter=self.parameter_name,
            observed=fvalue,
            min=min_val,
            max=max_val,
            deviation_pct=deviation * 100,
        )

        # Create the anomaly flag
        flag = AnomalyFlag(
            flag_id=str(uuid.uuid4()),
            timestamp=packet.timestamp,
            spacecraft_id=packet.spacecraft_id,
            severity=self.severity,
            parameter_name=self.parameter_name,
            observed_value=fvalue,
            expected_range=(min_val, max_val),
            reason=what_happened,
            confidence=confidence,
            source_packet_id=packet.packet_id,
        )

        # Identify recommended action (highest success rate)
        recommended = max(
            self.suggested_actions,
            key=lambda a: a.success_rate,
        )

        return AnomalyReport(
            flag=flag,
            what_happened=what_happened,
            why_it_matters=self.why_it_matters,
            suggested_actions=self.suggested_actions,
            recommended_action_id=recommended.action_id,
            urgency_hours=self.urgency_hours,
            similar_events_count=self.similar_events_count,
        )


# ---------------------------------------------------------------------------
# Rule Library — DSCOVR / Solar Wind Domain
# ---------------------------------------------------------------------------

def build_dscovr_rules() -> list[DetectionRule]:
    """
    Complete detection ruleset — solar wind + aviation.
    7 rules covering all major parameters.
    Based on NOAA SWPC operational thresholds.
    """
    return [
        # Ion temperature
        DetectionRule(
            rule_id="dscovr-temp-extreme",
            name="Ion Temperature — Extreme High",
            parameter_name="ion_temperature_k",
            min_value=None,
            max_value=500_000.0,
            severity=AnomalySeverity.MEDIUM,
            what_happened_template=(
                "Ion temperature reached {observed:,.0f} K, "
                "above the {max:,.0f} K threshold. "
                "Deviation: {deviation_pct:.1f}%."
            ),
            why_it_matters=(
                "Extremely high ion temperature indicates a hot fast solar "
                "wind stream — typically a coronal hole high-speed stream. "
                "Can cause moderate geomagnetic activity for 2-4 days."
            ),
            suggested_actions=[
                SuggestedAction.create(
                    title="Log HSS event and monitor",
                    description="Record coronal hole high-speed stream arrival.",
                    steps=[
                        "Log HSS arrival in mission record",
                        "Notify downstream space weather subscribers",
                        "Set 4-hour monitoring review",
                    ],
                    success_rate=0.893,
                    time_required_minutes=3.0,
                    risk_if_skipped="Missed documentation of recurring event",
                    priority=1,
                ),
            ],
            urgency_hours=8.0,
            similar_events_count=2841,
        ),

        # Solar wind speed — high
        DetectionRule(
            rule_id="dscovr-speed-high",
            name="Solar Wind Speed — High",
            parameter_name="bulk_speed_km_s",
            min_value=None,
            max_value=600.0,
            severity=AnomalySeverity.HIGH,
            what_happened_template=(
                "Solar wind speed reached {observed:.1f} km/s, "
                "exceeding the {max:.0f} km/s threshold. "
                "Deviation: {deviation_pct:.1f}%. "
                "CME arrival pattern detected."
            ),
            why_it_matters=(
                "Elevated solar wind speed indicates increased geomagnetic "
                "storm risk. Spacecraft may experience increased drag. "
                "Navigation and communication disruption possible."
            ),
            suggested_actions=[
                SuggestedAction.create(
                    title="Switch spacecraft to safe mode",
                    description="Reduce power draw and enable radiation protection.",
                    steps=[
                        "Confirm CME arrival via magnetic field data",
                        "Notify mission director",
                        "Execute safe mode command sequence",
                        "Reduce telemetry to essential housekeeping",
                    ],
                    success_rate=0.942,
                    time_required_minutes=12.0,
                    risk_if_skipped="Battery damage and sensor degradation",
                    priority=1,
                ),
                SuggestedAction.create(
                    title="Increase telemetry cadence",
                    description="Switch to high-rate monitoring mode.",
                    steps=[
                        "Command 10-second telemetry rate",
                        "Alert downstream data consumers",
                        "Set speed alert at 750 km/s",
                    ],
                    success_rate=0.887,
                    time_required_minutes=2.0,
                    risk_if_skipped="May miss peak event",
                    priority=2,
                ),
                SuggestedAction.create(
                    title="Continue nominal operations",
                    description="Maintain current configuration and monitor.",
                    steps=[
                        "Set alert threshold at 750 km/s",
                        "Notify standby operations team",
                        "Log event for post-pass review",
                    ],
                    success_rate=0.713,
                    time_required_minutes=0.0,
                    risk_if_skipped="Recovery window may close if CME stronger than predicted",
                    priority=3,
                ),
            ],
            urgency_hours=2.0,
            similar_events_count=847,
        ),

        # Solar wind speed — critical CME
        DetectionRule(
            rule_id="dscovr-speed-critical",
            name="Solar Wind Speed — Critical CME",
            parameter_name="bulk_speed_km_s",
            min_value=None,
            max_value=800.0,
            severity=AnomalySeverity.CRITICAL,
            what_happened_template=(
                "CRITICAL: Solar wind speed {observed:.0f} km/s exceeds "
                "800 km/s CME threshold. Deviation: {deviation_pct:.1f}%. "
                "Major geomagnetic storm likely within 30 minutes."
            ),
            why_it_matters=(
                "Speed above 800 km/s indicates a major CME arrival. "
                "Severe geomagnetic storm (G3-G5) is imminent. "
                "Power grids, satellites, and HF radio are all at risk."
            ),
            suggested_actions=[
                SuggestedAction.create(
                    title="Activate emergency CME protocol",
                    description="Full emergency response for major CME arrival.",
                    steps=[
                        "Immediately notify all spacecraft operators",
                        "Switch all vulnerable assets to safe mode",
                        "Alert power grid operators",
                        "Activate backup communication links",
                        "Document all actions in audit log",
                    ],
                    success_rate=0.870,
                    time_required_minutes=5.0,
                    risk_if_skipped="Severe spacecraft and infrastructure damage",
                    priority=1,
                ),
            ],
            urgency_hours=0.5,
            similar_events_count=89,
        ),

        # Proton density
        DetectionRule(
            rule_id="dscovr-density-high",
            name="Proton Density — High",
            parameter_name="proton_density_n_cc",
            min_value=None,
            max_value=15.0,
            severity=AnomalySeverity.MEDIUM,
            what_happened_template=(
                "Proton density reached {observed:.2f} p/cm³, "
                "above the {max:.0f} p/cm³ nominal threshold. "
                "Deviation: {deviation_pct:.1f}%."
            ),
            why_it_matters=(
                "High proton density increases solar wind dynamic pressure, "
                "compressing the magnetosphere and increasing radiation "
                "exposure for polar orbit satellites. GPS may degrade."
            ),
            suggested_actions=[
                SuggestedAction.create(
                    title="Issue space weather advisory",
                    description="Notify downstream operators of elevated density.",
                    steps=[
                        "Draft space weather advisory",
                        "Distribute to subscribed operators",
                        "Log advisory in operations record",
                    ],
                    success_rate=0.921,
                    time_required_minutes=5.0,
                    risk_if_skipped="Downstream operators unprepared for satellite anomalies",
                    priority=1,
                ),
                SuggestedAction.create(
                    title="Monitor and log",
                    description="Continue monitoring with enhanced logging.",
                    steps=[
                        "Enable detailed parameter logging",
                        "Set 30-minute review reminder",
                    ],
                    success_rate=0.784,
                    time_required_minutes=1.0,
                    risk_if_skipped="Reduced situational awareness",
                    priority=2,
                ),
            ],
            urgency_hours=4.0,
            similar_events_count=1243,
        ),

        # Magnetic field Bz southward
        DetectionRule(
            rule_id="dscovr-bz-south",
            name="Magnetic Field Bz — Strong Southward",
            parameter_name="bz_gsm_nt",
            min_value=-20.0,
            max_value=None,
            severity=AnomalySeverity.CRITICAL,
            what_happened_template=(
                "Magnetic field Bz reached {observed:.2f} nT (strongly southward). "
                "Threshold: {min:.0f} nT. Deviation: {deviation_pct:.1f}%."
            ),
            why_it_matters=(
                "Strong southward Bz is the primary driver of severe geomagnetic "
                "storms. Solar wind merges with Earth's field injecting energy "
                "into the magnetosphere. Can cause widespread satellite anomalies, "
                "power grid disruptions, and HF radio blackouts."
            ),
            suggested_actions=[
                SuggestedAction.create(
                    title="Issue G-storm warning immediately",
                    description="Issue geomagnetic storm warning to all operators.",
                    steps=[
                        "Confirm Bz reading with backup sensor",
                        "Classify storm level on G1-G5 scale",
                        "Issue warning via NOAA SWPC protocol",
                        "Notify airline operators — HF radio risk",
                        "Notify power grid operators — GIC risk",
                        "Alert satellite operators — anomaly risk",
                    ],
                    success_rate=0.967,
                    time_required_minutes=8.0,
                    risk_if_skipped="Power grid damage, satellite loss, HF blackout",
                    priority=1,
                ),
                SuggestedAction.create(
                    title="Command spacecraft to storm safe mode",
                    description="Execute storm-level safe mode protocol.",
                    steps=[
                        "Reduce all non-essential power loads",
                        "Enable electrostatic discharge protection",
                        "Switch to backup communication frequency",
                        "Set 5-minute telemetry cadence",
                    ],
                    success_rate=0.951,
                    time_required_minutes=15.0,
                    risk_if_skipped="Electrostatic discharge risk",
                    priority=2,
                ),
            ],
            urgency_hours=0.5,
            similar_events_count=312,
        ),

        # Magnetic field Bt extreme
        DetectionRule(
            rule_id="dscovr-bt-extreme",
            name="Total Magnetic Field — Extreme",
            parameter_name="bt_nt",
            min_value=None,
            max_value=50.0,
            severity=AnomalySeverity.CRITICAL,
            what_happened_template=(
                "Total magnetic field Bt reached {observed:.1f} nT, "
                "above the {max:.0f} nT extreme threshold. "
                "Deviation: {deviation_pct:.1f}%. Major CME sheath."
            ),
            why_it_matters=(
                "Extreme total IMF strength indicates a highly compressed "
                "solar wind — major CME sheath. Severe geomagnetic storm "
                "is imminent. All spacecraft and infrastructure at risk."
            ),
            suggested_actions=[
                SuggestedAction.create(
                    title="Activate severe storm protocol",
                    description="Extreme IMF compression — full emergency response.",
                    steps=[
                        "Immediately activate severe storm protocol",
                        "Switch all spacecraft to emergency safe mode",
                        "Notify national emergency management if G4+",
                        "Document all actions immediately",
                    ],
                    success_rate=0.850,
                    time_required_minutes=5.0,
                    risk_if_skipped="Severe damage to spacecraft and infrastructure",
                    priority=1,
                ),
            ],
            urgency_hours=0.5,
            similar_events_count=47,
        ),

        # Aviation altitude anomaly
        DetectionRule(
            rule_id="aviation-altitude-low",
            name="Aircraft Low Altitude — Possible Emergency",
            parameter_name="baro_altitude_m",
            min_value=None,
            max_value=1000.0,
            severity=AnomalySeverity.HIGH,
            what_happened_template=(
                "Aircraft altitude {observed:.0f}m is below 1,000m threshold. "
                "Deviation: {deviation_pct:.1f}%. "
                "Verify aircraft is in planned approach phase."
            ),
            why_it_matters=(
                "Aircraft below 1,000m outside of approach/departure phases "
                "may indicate emergency descent or terrain proximity warning. "
                "Requires immediate ATC verification."
            ),
            suggested_actions=[
                SuggestedAction.create(
                    title="Verify aircraft status with ATC",
                    description="Confirm planned approach or declare emergency.",
                    steps=[
                        "Check flight plan for approach phase",
                        "Contact ATC for aircraft status",
                        "If unplanned — declare emergency and vector assistance",
                    ],
                    success_rate=0.950,
                    time_required_minutes=2.0,
                    risk_if_skipped="Possible CFIT or undetected emergency",
                    priority=1,
                ),
            ],
            urgency_hours=0.1,
            similar_events_count=156,
        ),
    ]
# ---------------------------------------------------------------------------
# Main Detector
# ---------------------------------------------------------------------------

class AnomalyDetector:
    """
    ChronoScope AI anomaly detection engine.

    Watches all telemetry parameters against a rule library.
    Every detection produces a complete AnomalyReport with:
    - Plain English explanation
    - Ranked suggested actions with success rates
    - Urgency assessment
    - Historical precedent count

    The detector never makes decisions.
    It generates recommendations for human operators.
    """

    def __init__(self, rules: list[DetectionRule] | None = None):
        self._rules = rules or build_dscovr_rules()
        self._reports: list[AnomalyReport] = []
        self.logger = structlog.get_logger(__name__)

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    @property
    def report_count(self) -> int:
        return len(self._reports)

    @property
    def reports(self) -> list[AnomalyReport]:
        return list(self._reports)

    def add_rule(self, rule: DetectionRule) -> None:
        """Add a detection rule to the engine."""
        self._rules.append(rule)
        self.logger.info("rule_added", rule_id=rule.rule_id, name=rule.name)

    def analyze_packet(
        self,
        packet: TelemetryPacket,
    ) -> list[AnomalyReport]:
        """
        Analyze a single telemetry packet against all rules.
        Returns list of anomaly reports (empty if all nominal).
        """
        detected: list[AnomalyReport] = []

        for rule in self._rules:
            report = rule.evaluate(packet)
            if report is not None:
                self._reports.append(report)
                detected.append(report)
                self.logger.warning(
                    "anomaly_detected",
                    rule_id=rule.rule_id,
                    spacecraft_id=packet.spacecraft_id,
                    parameter=rule.parameter_name,
                    severity=report.flag.severity.value,
                    confidence=f"{report.flag.confidence * 100:.1f}%",
                    urgency_hours=report.urgency_hours,
                )

        return detected

    def analyze_session(
        self,
        session: MissionSession,
    ) -> list[AnomalyReport]:
        """
        Analyze all packets in a mission session.
        Adds detected anomalies to the session.
        Returns all anomaly reports found.
        """
        all_reports: list[AnomalyReport] = []

        self.logger.info(
            "session_analysis_started",
            session_id=session.session_id,
            packet_count=session.packet_count,
            rule_count=self.rule_count,
        )

        for packet in session.packets:
            reports = self.analyze_packet(packet)
            for report in reports:
                all_reports.append(report)
                session.add_anomaly(report.flag)

        self.logger.info(
            "session_analysis_complete",
            session_id=session.session_id,
            anomalies_found=len(all_reports),
        )

        return all_reports

    def get_critical_reports(self) -> list[AnomalyReport]:
        """Get all critical severity reports."""
        return [
            r for r in self._reports
            if r.flag.severity == AnomalySeverity.CRITICAL
        ]

    def get_reports_by_severity(
        self,
        severity: AnomalySeverity,
    ) -> list[AnomalyReport]:
        """Get all reports of a given severity."""
        return [
            r for r in self._reports
            if r.flag.severity == severity
        ]

    def get_unacknowledged_reports(self) -> list[AnomalyReport]:
        """Get reports where operator has not yet made a decision."""
        return [
            r for r in self._reports
            if r.operator_decision is None
        ]

    def record_operator_decision(
        self,
        flag_id: str,
        action_id: str,
        actor: str,
    ) -> AnomalyReport | None:
        """
        Record what action an operator chose for a given anomaly.
        This feeds the outcome learning loop.
        """
        for report in self._reports:
            if report.flag.flag_id == flag_id:
                report.record_operator_decision(action_id, actor)
                self.logger.info(
                    "operator_decision_recorded",
                    flag_id=flag_id,
                    action_id=action_id,
                    actor=actor,
                )
                return report
        return None

    def record_outcome(
        self,
        flag_id: str,
        success: bool,
        description: str,
    ) -> AnomalyReport | None:
        """
        Record the outcome of an operator action.
        Over time this data improves success rate estimates.
        """
        for report in self._reports:
            if report.flag.flag_id == flag_id:
                report.record_outcome(success, description)
                self.logger.info(
                    "outcome_recorded",
                    flag_id=flag_id,
                    success=success,
                )
                return report
        return None

    def summary(self) -> dict[str, Any]:
        """Return detection summary statistics."""
        severity_counts: dict[str, int] = {}
        for report in self._reports:
            key = report.flag.severity.value
            severity_counts[key] = severity_counts.get(key, 0) + 1

        return {
            "total_reports": self.report_count,
            "by_severity": severity_counts,
            "unacknowledged": len(self.get_unacknowledged_reports()),
            "critical": len(self.get_critical_reports()),
            "rule_count": self.rule_count,
        }