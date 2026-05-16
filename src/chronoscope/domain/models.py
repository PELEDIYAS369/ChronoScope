# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid

"""
ChronoScope AI — Domain Models
Core data contracts for all mission data flowing through the system.
These models are the single source of truth. Nothing gets stored,
replayed, or analyzed without passing through these contracts first.

"""


# ---------------------------------------------------------------------------
# Source Trust / Provenance
# ---------------------------------------------------------------------------

class SourceTrustLevel(Enum):
    """
    Source trust hierarchy.
    Higher value = higher trust. System uses highest available trust.
    """
    STALE_PUBLIC      = 1   # Public source, data older than threshold
    VERIFIED_PUBLIC   = 2   # Public source, data fresh and validated
    AUTHORITATIVE     = 3   # Direct mission feed, ground truth


class SystemOperationalState(Enum):
    """
    Explicit system-wide operational posture.
    Never pretend NOMINAL when degraded.
    """
    NOMINAL   = "nominal"
    DEGRADED  = "degraded"
    CRITICAL  = "critical"
    UNKNOWN   = "unknown"


@dataclass(frozen=True)
class SourceProvenance:
    """
    Provenance record attached to every computed state.
    Answers: where did this data come from, when, and how much do we trust it.
    """
    source_name:           str
    source_trust_level:    SourceTrustLevel
    source_timestamp:      datetime     # When source claims data was valid
    ingestion_timestamp:   datetime     # When ChronoScope received it
    propagation_timestamp: datetime     # When ChronoScope processed it
    confidence_score:      float        # 0.0 to 1.0
    source_version:        str = "1.0"
    stale: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_score <= 1.0:
            raise ValueError(
                f"confidence_score {self.confidence_score} must be 0.0-1.0"
            )

    @classmethod
    def create(
        cls,
        source_name: str,
        source_trust_level: SourceTrustLevel,
        source_timestamp: datetime,
        confidence_score: float,
        stale: bool = False,
    ) -> SourceProvenance:
        now = datetime.now(timezone.utc)
        return cls(
            source_name=source_name,
            source_trust_level=source_trust_level,
            source_timestamp=source_timestamp,
            ingestion_timestamp=now,
            propagation_timestamp=now,
            confidence_score=confidence_score,
            stale=stale,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "source_trust_level": self.source_trust_level.value,
            "source_timestamp": self.source_timestamp.isoformat(),
            "ingestion_timestamp": self.ingestion_timestamp.isoformat(),
            "propagation_timestamp": self.propagation_timestamp.isoformat(),
            "confidence_score": self.confidence_score,
            "stale": self.stale,
            "source_version": self.source_version,
        }


@dataclass(frozen=True)
class DegradedCondition:
    """
    Explicit record of a degraded system condition.
    Surfaced to operators instead of pretending normal state.
    """
    condition_id:   str
    timestamp:      datetime
    condition_type: str    # source_unavailable | stale_source |
                           # invalid_propagation | missing_inputs
    source_name:    str
    description:    str
    severity:       str    # warning | error | critical
    resolved:       bool = False
    resolved_at:    datetime | None = None

    @classmethod
    def create(
        cls,
        condition_type: str,
        source_name: str,
        description: str,
        severity: str = "warning",
    ) -> DegradedCondition:
        return cls(
            condition_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            condition_type=condition_type,
            source_name=source_name,
            description=description,
            severity=severity,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "timestamp": self.timestamp.isoformat(),
            "condition_type": self.condition_type,
            "source_name": self.source_name,
            "description": self.description,
            "severity": self.severity,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PacketType(Enum):
    """CCSDS packet classification."""
    TELEMETRY = "TM"
    TELECOMMAND = "TC"
    HOUSEKEEPING = "HK"
    SCIENCE = "SCI"
    UNKNOWN = "UNK"


class MissionPhase(Enum):
    """Standard mission phase classifications."""
    PRE_LAUNCH = "pre_launch"
    LAUNCH = "launch"
    EARLY_ORBIT = "early_orbit"
    COMMISSIONING = "commissioning"
    NOMINAL = "nominal"
    SAFE_MODE = "safe_mode"
    CONTINGENCY = "contingency"
    DECOMMISSION = "decommission"


class AnomalySeverity(Enum):
    """Anomaly severity levels for AI detection output."""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReplayStatus(Enum):
    """Current state of a replay session."""
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    COMPLETE = "complete"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Core Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryPacket:
    """
    Immutable representation of a single telemetry packet.
    frozen=True enforces immutability — critical for deterministic replay.
    Once a packet is created it cannot be altered. Ever.
    """
    packet_id: str
    timestamp: datetime
    spacecraft_id: str
    packet_type: PacketType
    apid: int                          # CCSDS Application Process ID
    sequence_count: int                # CCSDS sequence counter
    raw_bytes: bytes                   # Original packet bytes
    parameters: dict[str, Any]        # Decoded engineering values
    source: str                        # Data source identifier

    def __post_init__(self) -> None:
        """Validate packet integrity on creation."""
        if not self.packet_id:
            raise ValueError("packet_id cannot be empty")
        if not self.spacecraft_id:
            raise ValueError("spacecraft_id cannot be empty")
        if self.apid < 0 or self.apid > 2047:
            raise ValueError(f"APID {self.apid} out of valid range 0-2047")
        if self.sequence_count < 0 or self.sequence_count > 16383:
            raise ValueError(f"Sequence count {self.sequence_count} out of range")
        if not isinstance(self.timestamp, datetime):
            raise ValueError("timestamp must be a datetime object")

    @classmethod
    def create(
        cls,
        spacecraft_id: str,
        packet_type: PacketType,
        apid: int,
        sequence_count: int,
        raw_bytes: bytes,
        parameters: dict[str, Any],
        source: str,
        timestamp: datetime | None = None,
    ) -> TelemetryPacket:
        """Factory method for clean packet creation."""
        return cls(
            packet_id=str(uuid.uuid4()),
            timestamp=timestamp or datetime.now(timezone.utc),
            spacecraft_id=spacecraft_id,
            packet_type=packet_type,
            apid=apid,
            sequence_count=sequence_count,
            raw_bytes=raw_bytes,
            parameters=parameters,
            source=source,
        )


@dataclass(frozen=True)
class MissionEvent:
    """
    Immutable record of a discrete mission event.
    Used for audit trail construction and replay anchoring.
    """
    event_id: str
    timestamp: datetime
    spacecraft_id: str
    mission_phase: MissionPhase
    event_type: str
    description: str
    operator_id: str | None
    parameters: dict[str, Any]
    source_packet_id: str | None = None

    @classmethod
    def create(
        cls,
        spacecraft_id: str,
        mission_phase: MissionPhase,
        event_type: str,
        description: str,
        parameters: dict[str, Any],
        operator_id: str | None = None,
        source_packet_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> MissionEvent:
        """Factory method for clean event creation."""
        return cls(
            event_id=str(uuid.uuid4()),
            timestamp=timestamp or datetime.now(timezone.utc),
            spacecraft_id=spacecraft_id,
            mission_phase=mission_phase,
            event_type=event_type,
            description=description,
            operator_id=operator_id,
            parameters=parameters,
            source_packet_id=source_packet_id,
        )


@dataclass(frozen=True)
class AnomalyFlag:
    """
    AI-generated anomaly detection result.
    Every flag must carry a human-readable reason.
    No black box outputs. Ever.
    """
    flag_id: str
    timestamp: datetime
    spacecraft_id: str
    severity: AnomalySeverity
    parameter_name: str
    observed_value: float
    expected_range: tuple[float, float]
    reason: str                        # Human readable explanation — mandatory
    confidence: float                  # 0.0 to 1.0
    source_packet_id: str
    acknowledged: bool = False
    acknowledged_by: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence {self.confidence} must be between 0.0 and 1.0")
        if not self.reason:
            raise ValueError("Anomaly reason cannot be empty — explainability is mandatory")
        if len(self.expected_range) != 2:
            raise ValueError("expected_range must be a tuple of (min, max)")


@dataclass
class MissionSession:
    """
    A bounded window of mission activity.
    This is the top-level container for replay sessions.
    Mutable because sessions accumulate data over time.
    """
    session_id: str
    spacecraft_id: str
    mission_phase: MissionPhase
    start_time: datetime
    end_time: datetime | None
    packets: list[TelemetryPacket] = field(default_factory=list)
    events: list[MissionEvent] = field(default_factory=list)
    anomalies: list[AnomalyFlag] = field(default_factory=list)
    replay_status: ReplayStatus = ReplayStatus.IDLE
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        spacecraft_id: str,
        mission_phase: MissionPhase,
        start_time: datetime,
        end_time: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MissionSession:
        """Factory method for clean session creation."""
        return cls(
            session_id=str(uuid.uuid4()),
            spacecraft_id=spacecraft_id,
            mission_phase=mission_phase,
            start_time=start_time,
            end_time=end_time,
            metadata=metadata or {},
        )

    @property
    def duration_seconds(self) -> float | None:
        """Return session duration in seconds if end time is known."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds()

    @property
    def packet_count(self) -> int:
        return len(self.packets)

    @property
    def anomaly_count(self) -> int:
        return len(self.anomalies)

    def add_packet(self, packet: TelemetryPacket) -> None:
        """Add a telemetry packet to this session."""
        self.packets.append(packet)

    def add_event(self, event: MissionEvent) -> None:
        """Add a mission event to this session."""
        self.events.append(event)

    def add_anomaly(self, anomaly: AnomalyFlag) -> None:
        """Add an anomaly flag to this session."""
        self.anomalies.append(anomaly)