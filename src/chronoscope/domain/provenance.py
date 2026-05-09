"""
ChronoScope AI — Source Trust Policy
Manages source precedence and trust level assignment.
Authoritative mission feed > verified public > stale public.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any
import structlog

from src.chronoscope.domain.models import (
    SourceProvenance,
    SourceTrustLevel,
    DegradedCondition,
)

logger = structlog.get_logger(__name__)

# Staleness thresholds per source type
STALENESS_THRESHOLDS: dict[str, timedelta] = {
    "noaa_dscovr":      timedelta(hours=2),
    "ace_spacecraft":   timedelta(hours=2),
    "opensky_network":  timedelta(minutes=10),
    "celestrak_iss":    timedelta(hours=24),
    "authoritative":    timedelta(minutes=5),
    "default":          timedelta(hours=1),
}

# Trust level assignment per source
SOURCE_TRUST_MAP: dict[str, SourceTrustLevel] = {
    "noaa_dscovr":      SourceTrustLevel.VERIFIED_PUBLIC,
    "ace_spacecraft":   SourceTrustLevel.VERIFIED_PUBLIC,
    "opensky_network":  SourceTrustLevel.VERIFIED_PUBLIC,
    "celestrak_iss":    SourceTrustLevel.VERIFIED_PUBLIC,
    "authoritative":    SourceTrustLevel.AUTHORITATIVE,
}


class SourceTrustPolicy:
    """
    Enforces source trust hierarchy.
    Determines trust level, staleness, and confidence for any source.
    """

    def __init__(self):
        self._degraded_conditions: list[DegradedCondition] = []
        self.logger = structlog.get_logger(__name__)

    def evaluate(
        self,
        source_name: str,
        source_timestamp: datetime,
        raw_confidence: float = 1.0,
    ) -> SourceProvenance:
        """
        Evaluate a source and return provenance metadata.
        Detects staleness and adjusts confidence accordingly.
        """
        trust_level = SOURCE_TRUST_MAP.get(
            source_name, SourceTrustLevel.VERIFIED_PUBLIC
        )
        threshold = STALENESS_THRESHOLDS.get(
            source_name,
            STALENESS_THRESHOLDS["default"],
        )

        now = datetime.now(timezone.utc)
        age = now - source_timestamp
        stale = age > threshold

        if stale:
            confidence = raw_confidence * 0.5
            self._record_degraded(
                condition_type="stale_source",
                source_name=source_name,
                description=(
                    f"Source {source_name} data is {age.total_seconds()/3600:.1f}h old. "
                    f"Threshold is {threshold.total_seconds()/3600:.1f}h."
                ),
                severity="warning",
            )
            trust_level = SourceTrustLevel.STALE_PUBLIC
        else:
            confidence = raw_confidence

        provenance = SourceProvenance.create(
            source_name=source_name,
            source_trust_level=trust_level,
            source_timestamp=source_timestamp,
            confidence_score=min(confidence, 1.0),
            stale=stale,
        )

        self.logger.debug(
            "source_evaluated",
            source=source_name,
            trust=trust_level.name,
            stale=stale,
            confidence=confidence,
        )

        return provenance

    def record_source_unavailable(self, source_name: str, reason: str) -> DegradedCondition:
        """Record that a source is completely unavailable."""
        condition = self._record_degraded(
            condition_type="source_unavailable",
            source_name=source_name,
            description=f"Source {source_name} unavailable: {reason}",
            severity="error",
        )
        self.logger.warning(
            "source_unavailable",
            source=source_name,
            reason=reason,
        )
        return condition

    def record_invalid_propagation(
        self, source_name: str, detail: str
    ) -> DegradedCondition:
        """Record that propagation produced invalid results."""
        return self._record_degraded(
            condition_type="invalid_propagation",
            source_name=source_name,
            description=f"Invalid propagation from {source_name}: {detail}",
            severity="error",
        )

    def record_missing_inputs(
        self, source_name: str, missing: list[str]
    ) -> DegradedCondition:
        """Record that required inputs are missing."""
        return self._record_degraded(
            condition_type="missing_inputs",
            source_name=source_name,
            description=f"Missing inputs from {source_name}: {', '.join(missing)}",
            severity="warning",
        )

    def resolve_condition(self, condition_id: str) -> bool:
        """Mark a degraded condition as resolved."""
        for i, c in enumerate(self._degraded_conditions):
            if c.condition_id == condition_id and not c.resolved:
                from dataclasses import replace
                self._degraded_conditions[i] = replace(
                    c,
                    resolved=True,
                    resolved_at=datetime.now(timezone.utc),
                )
                return True
        return False

    def get_active_conditions(self) -> list[DegradedCondition]:
        """Get all unresolved degraded conditions."""
        return [c for c in self._degraded_conditions if not c.resolved]

    def get_operational_state(self) -> str:
        """
        Derive system operational state from active conditions.
        Never returns NOMINAL when degraded conditions exist.
        """
        from src.chronoscope.domain.models import SystemOperationalState
        active = self.get_active_conditions()
        if not active:
            return SystemOperationalState.NOMINAL.value
        severities = {c.severity for c in active}
        if "critical" in severities:
            return SystemOperationalState.CRITICAL.value
        if "error" in severities:
            return SystemOperationalState.DEGRADED.value
        return SystemOperationalState.DEGRADED.value

    def _record_degraded(
        self,
        condition_type: str,
        source_name: str,
        description: str,
        severity: str,
    ) -> DegradedCondition:
        condition = DegradedCondition.create(
            condition_type=condition_type,
            source_name=source_name,
            description=description,
            severity=severity,
        )
        self._degraded_conditions.append(condition)
        return condition