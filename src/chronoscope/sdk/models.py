"""
ChronoScope AI SDK — Data Models
Clean data structures for SDK consumers.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass
class SDKConfig:
    """Configuration for the ChronoScope SDK."""
    base_url: str = "http://localhost:8000"
    api_key: str = ""
    timeout_seconds: int = 30
    auto_reconnect: bool = True
    webhook_port: int = 8001


@dataclass
class SDKAlert:
    """
    Anomaly alert delivered to SDK consumers.
    Every alert is explainable — no black box outputs.
    """
    alert_id: str
    timestamp: datetime
    severity: str          # critical / high / medium / low / info
    spacecraft_id: str
    parameter: str
    observed_value: float
    expected_range: tuple[float, float]
    reason: str            # Human readable — always present
    confidence: float      # 0.0 to 1.0
    urgency_hours: float
    suggested_actions: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""

    @property
    def is_critical(self) -> bool:
        return self.severity == "critical"

    @property
    def top_action(self) -> dict[str, Any] | None:
        if self.suggested_actions:
            return self.suggested_actions[0]
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity,
            "spacecraft_id": self.spacecraft_id,
            "parameter": self.parameter,
            "observed_value": self.observed_value,
            "expected_range": list(self.expected_range),
            "reason": self.reason,
            "confidence": self.confidence,
            "urgency_hours": self.urgency_hours,
            "suggested_actions": self.suggested_actions,
            "session_id": self.session_id,
        }


@dataclass
class SDKSession:
    """Session summary for SDK consumers."""
    session_id: str
    spacecraft_id: str
    mission_phase: str
    packet_count: int
    anomaly_count: int
    replay_status: str
    start_time: datetime
    end_time: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "spacecraft_id": self.spacecraft_id,
            "mission_phase": self.mission_phase,
            "packet_count": self.packet_count,
            "anomaly_count": self.anomaly_count,
            "replay_status": self.replay_status,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }


@dataclass
class SDKHealth:
    """System health for SDK consumers."""
    status: str
    sessions: int
    total_packets: int
    total_anomalies: int
    audit_intact: bool
    timestamp: datetime

    @property
    def is_healthy(self) -> bool:
        return self.status == "NOMINAL" and self.audit_intact

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sessions": self.sessions,
            "total_packets": self.total_packets,
            "total_anomalies": self.total_anomalies,
            "audit_intact": self.audit_intact,
            "timestamp": self.timestamp.isoformat(),
            "is_healthy": self.is_healthy,
        }