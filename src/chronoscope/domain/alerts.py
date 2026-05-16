# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Auditable Alert Model
Every alert preserves full context for replayability.
Source snapshot, rule version, state version, timestamps, reason, confidence.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from src.chronoscope.domain.models import AnomalySeverity, SourceProvenance


@dataclass(frozen=True)
class AuditableAlert:
    """
    Production-grade alert with full audit context.
    Every field needed to replay the exact conditions that triggered this alert.

    Invariants:
    - source_snapshot_id links to the exact ingestion batch
    - rule_version ensures rule changes are traceable
    - state_version links to the exact object state at alert time
    - reason is always human-readable — never empty
    - confidence is always present
    """
    alert_id:           str
    created_at:         datetime
    resolved_at:        datetime | None

    # What triggered it
    rule_id:            str
    rule_version:       str
    rule_name:          str

    # What state triggered it
    source_snapshot_id: str
    state_version:      str
    spacecraft_id:      str
    parameter_name:     str
    observed_value:     float
    expected_range:     tuple[float, float]

    # Why it matters
    severity:           AnomalySeverity
    reason:             str           # Always human-readable
    confidence:         float         # 0.0 to 1.0
    urgency_hours:      float

    # Provenance
    provenance:         SourceProvenance

    # Resolution
    resolved:           bool = False
    resolved_by:        str | None = None
    resolution_note:    str | None = None

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("Alert reason cannot be empty — explainability required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} must be 0.0-1.0")

    @classmethod
    def create(
        cls,
        rule_id: str,
        rule_version: str,
        rule_name: str,
        source_snapshot_id: str,
        state_version: str,
        spacecraft_id: str,
        parameter_name: str,
        observed_value: float,
        expected_range: tuple[float, float],
        severity: AnomalySeverity,
        reason: str,
        confidence: float,
        urgency_hours: float,
        provenance: SourceProvenance,
    ) -> AuditableAlert:
        return cls(
            alert_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
            resolved_at=None,
            rule_id=rule_id,
            rule_version=rule_version,
            rule_name=rule_name,
            source_snapshot_id=source_snapshot_id,
            state_version=state_version,
            spacecraft_id=spacecraft_id,
            parameter_name=parameter_name,
            observed_value=observed_value,
            expected_range=expected_range,
            severity=severity,
            reason=reason,
            confidence=confidence,
            urgency_hours=urgency_hours,
            provenance=provenance,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "rule_name": self.rule_name,
            "source_snapshot_id": self.source_snapshot_id,
            "state_version": self.state_version,
            "spacecraft_id": self.spacecraft_id,
            "parameter_name": self.parameter_name,
            "observed_value": self.observed_value,
            "expected_range": list(self.expected_range),
            "severity": self.severity.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "urgency_hours": self.urgency_hours,
            "provenance": self.provenance.to_dict(),
            "resolved": self.resolved,
            "resolved_by": self.resolved_by,
            "resolution_note": self.resolution_note,
        }


@dataclass
class AlertRegistry:
    """
    In-memory registry of all auditable alerts.
    Supports replayability — every alert is immutable once created.
    """
    _alerts: dict[str, AuditableAlert] = field(default_factory=dict)
    _resolved: dict[str, AuditableAlert] = field(default_factory=dict)

    def register(self, alert: AuditableAlert) -> None:
        self._alerts[alert.alert_id] = alert

    def resolve(
        self,
        alert_id: str,
        resolved_by: str,
        resolution_note: str = "",
    ) -> AuditableAlert | None:
        alert = self._alerts.get(alert_id)
        if not alert or alert.resolved:
            return None
        from dataclasses import replace
        resolved = replace(
            alert,
            resolved=True,
            resolved_at=datetime.now(timezone.utc),
            resolved_by=resolved_by,
            resolution_note=resolution_note,
        )
        self._resolved[alert_id] = resolved
        del self._alerts[alert_id]
        return resolved

    def get_active(self) -> list[AuditableAlert]:
        return list(self._alerts.values())

    def get_resolved(self) -> list[AuditableAlert]:
        return list(self._resolved.values())

    def get_by_id(self, alert_id: str) -> AuditableAlert | None:
        return self._alerts.get(alert_id) or self._resolved.get(alert_id)

    def get_by_spacecraft(self, spacecraft_id: str) -> list[AuditableAlert]:
        return [
            a for a in self._alerts.values()
            if a.spacecraft_id == spacecraft_id
        ]

    @property
    def active_count(self) -> int:
        return len(self._alerts)

    @property
    def resolved_count(self) -> int:
        return len(self._resolved)