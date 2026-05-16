# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Bounded AI Interface
AI is never authoritative. Rule engine detects. AI explains.

Architectural constraint:
- Rule engine owns anomaly detection
- AI only provides operational context and explanation
- AI cannot invent state or make unsupported inferences
- Every AI output includes uncertainty and confidence

Permitted AI tool interfaces:
- get_object_state
- get_object_alerts
- get_source_snapshot
- get_rule_definition
- get_system_status
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BoundedAIOutput:
    """
    Output from the bounded AI explainer.
    Always includes explanation, context, uncertainty, and confidence.
    Never includes invented state or unsupported inference.
    """
    explanation:         str      # What the AI observed in plain language
    operational_context: str      # Why this matters operationally
    uncertainty:         str      # What the AI does not know
    confidence:          float    # 0.0 to 1.0 — how confident the AI is
    supporting_data:     list[str] # What data points support this explanation
    limitations:         list[str] # Explicit list of what AI cannot determine

    def __post_init__(self) -> None:
        if not self.explanation:
            raise ValueError("AI explanation cannot be empty")
        if not self.uncertainty:
            raise ValueError("AI uncertainty statement cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} must be 0.0-1.0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "explanation": self.explanation,
            "operational_context": self.operational_context,
            "uncertainty": self.uncertainty,
            "confidence": self.confidence,
            "supporting_data": self.supporting_data,
            "limitations": self.limitations,
            "ai_is_authoritative": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


class BoundedAIInterface:
    """
    Strictly bounded AI interface.
    AI can only read from these five permitted sources.
    AI cannot write, cannot infer beyond available data,
    and cannot be used as a decision authority.
    """

    def __init__(self, controller: Any):
        self._controller = controller
        self.logger = structlog.get_logger(__name__)

    def get_object_state(self, spacecraft_id: str) -> dict[str, Any]:
        """
        Return current known state of a spacecraft.
        Only returns what is actually known — no extrapolation.
        """
        try:
            sessions = self._controller.list_sessions()
            matching = [
                s for s in sessions
                if s.get("spacecraft_id") == spacecraft_id
            ]
            if not matching:
                return {
                    "spacecraft_id": spacecraft_id,
                    "state": "unknown",
                    "reason": "No session found for this spacecraft",
                    "data_available": False,
                }
            latest = matching[-1]
            return {
                "spacecraft_id": spacecraft_id,
                "state": latest.get("replay_status", "unknown"),
                "packet_count": latest.get("packet_count", 0),
                "anomaly_count": latest.get("anomaly_count", 0),
                "data_available": True,
            }
        except Exception as e:
            self.logger.warning("get_object_state_failed", error=str(e))
            return {
                "spacecraft_id": spacecraft_id,
                "state": "unknown",
                "error": str(e),
                "data_available": False,
            }

    def get_object_alerts(self, spacecraft_id: str) -> dict[str, Any]:
        """
        Return active alerts for a spacecraft.
        Only returns confirmed rule-engine detections.
        """
        try:
            sessions = self._controller.list_sessions()
            matching = [
                s for s in sessions
                if s.get("spacecraft_id") == spacecraft_id
            ]
            if not matching:
                return {
                    "spacecraft_id": spacecraft_id,
                    "alerts": [],
                    "alert_count": 0,
                    "data_available": False,
                }
            session_id = matching[-1]["session_id"]
            anomalies = self._controller.get_anomalies(session_id)
            return {
                "spacecraft_id": spacecraft_id,
                "alerts": anomalies,
                "alert_count": len(anomalies),
                "data_available": True,
            }
        except Exception as e:
            self.logger.warning("get_object_alerts_failed", error=str(e))
            return {
                "spacecraft_id": spacecraft_id,
                "alerts": [],
                "alert_count": 0,
                "error": str(e),
                "data_available": False,
            }

    def get_source_snapshot(self, source_name: str) -> dict[str, Any]:
        """
        Return current ingestion status for a source.
        """
        try:
            status = self._controller.status()
            return {
                "source_name": source_name,
                "ingester": status.get("ingester", "unknown"),
                "sessions": status.get("sessions", 0),
                "audit_entries": status.get("audit_entries", 0),
                "data_available": True,
            }
        except Exception as e:
            return {
                "source_name": source_name,
                "data_available": False,
                "error": str(e),
            }

    def get_rule_definition(self, rule_id: str) -> dict[str, Any]:
        """
        Return definition of a detection rule.
        AI can read rules but cannot modify them.
        """
        try:
            rules = self._controller._detector._rules
            for rule in rules:
                if rule.rule_id == rule_id:
                    return {
                        "rule_id": rule.rule_id,
                        "name": rule.name,
                        "parameter": rule.parameter_name,
                        "min_value": rule.min_value,
                        "max_value": rule.max_value,
                        "severity": rule.severity.value,
                        "urgency_hours": rule.urgency_hours,
                        "confidence_base": rule.confidence_base,
                        "data_available": True,
                    }
            return {
                "rule_id": rule_id,
                "data_available": False,
                "reason": "Rule not found",
            }
        except Exception as e:
            return {
                "rule_id": rule_id,
                "data_available": False,
                "error": str(e),
            }

    def get_system_status(self) -> dict[str, Any]:
        """
        Return overall system status.
        AI reads this — never writes it.
        """
        try:
            status = self._controller.status()
            health = self._controller.get_health()
            return {
                "operational_state": health.get("status", "UNKNOWN"),
                "sessions": status.get("sessions", 0),
                "audit_intact": status.get("audit_chain_intact", False),
                "ai_rules_active": status.get("detector_rules", 0),
                "critical_alerts": status.get("ai_critical", 0),
                "data_available": True,
            }
        except Exception as e:
            return {
                "operational_state": "UNKNOWN",
                "data_available": False,
                "error": str(e),
            }

    def explain_alert(
        self,
        alert: dict[str, Any],
        object_state: dict[str, Any],
    ) -> BoundedAIOutput:
        """
        Generate bounded explanation for a rule-engine alert.
        AI explains — rule engine detected.
        AI never overrides, never invents, never infers beyond data.
        """
        parameter = alert.get("parameter", "unknown")
        observed = alert.get("observed_value", 0)
        reason = alert.get("reason", "")
        severity = alert.get("severity", "unknown")
        confidence = float(alert.get("confidence", 0)) / 100

        packet_count = object_state.get("packet_count", 0)
        spacecraft_id = alert.get("spacecraft_id", "unknown")

        # Build explanation from available data only
        explanation = (
            f"The rule engine detected that {parameter} reached {observed} "
            f"on spacecraft {spacecraft_id}. {reason}"
        )

        operational_context = self._derive_operational_context(
            parameter, severity, packet_count
        )

        uncertainty = (
            f"This explanation is based on {packet_count} available packets. "
            f"AI cannot determine root cause, only pattern match. "
            f"Operator verification required before any action."
        )

        supporting_data = [
            f"Parameter: {parameter}",
            f"Observed value: {observed}",
            f"Severity: {severity}",
            f"Based on {packet_count} packets",
        ]

        limitations = [
            "AI cannot determine hardware root cause",
            "AI cannot predict future state",
            "AI cannot override operator judgment",
            "AI explanation is pattern-based, not physics-based",
            "Confidence reflects pattern match, not ground truth",
        ]

        self.logger.info(
            "ai_explanation_generated",
            spacecraft_id=spacecraft_id,
            parameter=parameter,
            confidence=confidence,
            ai_is_authoritative=False,
        )

        return BoundedAIOutput(
            explanation=explanation,
            operational_context=operational_context,
            uncertainty=uncertainty,
            confidence=min(confidence, 0.95),
            supporting_data=supporting_data,
            limitations=limitations,
        )

    def _derive_operational_context(
        self,
        parameter: str,
        severity: str,
        packet_count: int,
    ) -> str:
        contexts = {
            "ion_temperature_k": (
                "Elevated ion temperature indicates a coronal hole high-speed "
                "stream or CME arrival. This affects spacecraft radiation exposure "
                "and can cause geomagnetic activity at Earth."
            ),
            "bulk_speed_km_s": (
                "Elevated solar wind speed indicates fast stream arrival. "
                "Geomagnetic storm risk increases. Spacecraft drag in LEO increases."
            ),
            "bz_gsm_nt": (
                "Strongly negative Bz drives geomagnetic reconnection. "
                "This is the primary driver of severe geomagnetic storms. "
                "Power grids and satellite operators should be notified."
            ),
            "bt_nt": (
                "High total magnetic field indicates compressed solar wind. "
                "Major CME sheath passage likely. Severe storm conditions possible."
            ),
            "proton_density_n_cc": (
                "Elevated proton density increases solar wind dynamic pressure. "
                "Magnetosphere compression increases radiation in polar orbits."
            ),
        }
        context = contexts.get(
            parameter,
            f"Parameter {parameter} exceeded operational threshold. "
            f"Review mission rules for specific operational impact."
        )
        if packet_count < 10:
            context += (
                f" Note: Only {packet_count} packets available. "
                f"Low data volume reduces context reliability."
            )
        return context