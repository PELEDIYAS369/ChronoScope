# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Operational Event System
Event-driven observability for hourly reporting.

Events emitted during runtime:
- source_ingested
- source_failed
- propagation_completed
- rule_evaluated
- alert_created
- alert_resolved
- system_degraded
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid
import structlog

logger = structlog.get_logger(__name__)


class EventType(Enum):
    SOURCE_INGESTED        = "source_ingested"
    SOURCE_FAILED          = "source_failed"
    PROPAGATION_COMPLETED  = "propagation_completed"
    RULE_EVALUATED         = "rule_evaluated"
    ALERT_CREATED          = "alert_created"
    ALERT_RESOLVED         = "alert_resolved"
    SYSTEM_DEGRADED        = "system_degraded"


@dataclass(frozen=True)
class OperationalEvent:
    """
    Immutable operational event.
    Every significant system action emits one of these.
    Used for hourly report generation and audit.
    """
    event_id:   str
    event_type: EventType
    timestamp:  datetime
    source:     str
    details:    dict[str, Any]

    @classmethod
    def create(
        cls,
        event_type: EventType,
        source: str,
        details: dict[str, Any],
    ) -> OperationalEvent:
        return cls(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            source=source,
            details=details,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "details": self.details,
        }


class EventBus:
    """
    In-process event bus for operational events.
    Collects events for hourly report generation.
    Thread-safe for single-process use.
    """

    def __init__(self):
        self._events: list[OperationalEvent] = []
        self.logger = structlog.get_logger(__name__)

    def emit(
        self,
        event_type: EventType,
        source: str,
        details: dict[str, Any],
    ) -> OperationalEvent:
        event = OperationalEvent.create(
            event_type=event_type,
            source=source,
            details=details,
        )
        self._events.append(event)
        self.logger.info(
            "event_emitted",
            event_type=event_type.value,
            source=source,
        )
        return event

    def emit_source_ingested(
        self,
        source_name: str,
        packets_ingested: int,
        duration_seconds: float,
        session_id: str,
    ) -> OperationalEvent:
        return self.emit(
            EventType.SOURCE_INGESTED,
            source=source_name,
            details={
                "packets_ingested": packets_ingested,
                "duration_seconds": duration_seconds,
                "session_id": session_id,
            },
        )

    def emit_source_failed(
        self,
        source_name: str,
        reason: str,
        error_type: str = "unknown",
    ) -> OperationalEvent:
        return self.emit(
            EventType.SOURCE_FAILED,
            source=source_name,
            details={
                "reason": reason,
                "error_type": error_type,
            },
        )

    def emit_rule_evaluated(
        self,
        rule_id: str,
        spacecraft_id: str,
        triggered: bool,
        parameter: str,
    ) -> OperationalEvent:
        return self.emit(
            EventType.RULE_EVALUATED,
            source=spacecraft_id,
            details={
                "rule_id": rule_id,
                "triggered": triggered,
                "parameter": parameter,
            },
        )

    def emit_alert_created(
        self,
        alert_id: str,
        spacecraft_id: str,
        severity: str,
        parameter: str,
        rule_id: str,
    ) -> OperationalEvent:
        return self.emit(
            EventType.ALERT_CREATED,
            source=spacecraft_id,
            details={
                "alert_id": alert_id,
                "severity": severity,
                "parameter": parameter,
                "rule_id": rule_id,
            },
        )

    def emit_alert_resolved(
        self,
        alert_id: str,
        spacecraft_id: str,
        resolved_by: str,
    ) -> OperationalEvent:
        return self.emit(
            EventType.ALERT_RESOLVED,
            source=spacecraft_id,
            details={
                "alert_id": alert_id,
                "resolved_by": resolved_by,
            },
        )

    def emit_system_degraded(
        self,
        condition_type: str,
        source_name: str,
        description: str,
        severity: str,
    ) -> OperationalEvent:
        return self.emit(
            EventType.SYSTEM_DEGRADED,
            source=source_name,
            details={
                "condition_type": condition_type,
                "description": description,
                "severity": severity,
            },
        )

    def get_events_in_window(
        self,
        start: datetime,
        end: datetime,
    ) -> list[OperationalEvent]:
        return [
            e for e in self._events
            if start <= e.timestamp <= end
        ]

    def get_events_by_type(
        self,
        event_type: EventType,
    ) -> list[OperationalEvent]:
        return [e for e in self._events if e.event_type == event_type]

    def clear_before(self, cutoff: datetime) -> int:
        before = len(self._events)
        self._events = [e for e in self._events if e.timestamp >= cutoff]
        return before - len(self._events)

    @property
    def total_events(self) -> int:
        return len(self._events)