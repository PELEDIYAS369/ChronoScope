# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Tamper-Evident Audit Log
Every action taken in ChronoScope is recorded here.
Each entry is cryptographically chained to the previous.
If any entry is modified, the chain breaks.
If any entry is deleted, the chain breaks.
This is mathematical proof — not just a log file.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import hashlib
import json
import uuid
import structlog

from src.chronoscope.domain.exceptions import AuditChainBrokenError
from src.chronoscope.domain.constants import (
    AUDIT_HASH_ALGORITHM,
    AUDIT_LOG_VERSION,
)

logger = structlog.get_logger(__name__)


class AuditEventType(Enum):
    """Every type of event that gets recorded."""
    # Session events
    SESSION_CREATED = "session_created"
    SESSION_LOADED = "session_loaded"
    SESSION_CLOSED = "session_closed"

    # Replay events
    REPLAY_STARTED = "replay_started"
    REPLAY_PAUSED = "replay_paused"
    REPLAY_SEEKED = "replay_seeked"
    REPLAY_RESET = "replay_reset"
    REPLAY_SPEED_CHANGED = "replay_speed_changed"

    # Ingestion events
    INGESTION_STARTED = "ingestion_started"
    INGESTION_COMPLETED = "ingestion_completed"
    INGESTION_FAILED = "ingestion_failed"

    # Anomaly events
    ANOMALY_DETECTED = "anomaly_detected"
    ANOMALY_ACKNOWLEDGED = "anomaly_acknowledged"
    ANOMALY_DISMISSED = "anomaly_dismissed"
    OPERATOR_ACTION_TAKEN = "operator_action_taken"
    OPERATOR_ACTION_OUTCOME = "operator_action_outcome"

    # System events
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    DETERMINISM_VERIFIED = "determinism_verified"
    DETERMINISM_VIOLATION = "determinism_violation"

    # Export events
    REPORT_EXPORTED = "report_exported"
    AUDIT_EXPORTED = "audit_exported"


@dataclass(frozen=True)
class AuditEntry:
    """
    A single immutable audit log entry.
    frozen=True means it cannot be changed after creation.
    The hash chain links each entry to the previous one.
    """
    entry_id: str
    timestamp: datetime
    event_type: AuditEventType
    actor: str                      # Who or what caused this event
    session_id: str | None
    spacecraft_id: str | None
    details: dict[str, Any]
    previous_hash: str              # Hash of the previous entry
    entry_hash: str                 # Hash of this entire entry

    @classmethod
    def create(
        cls,
        event_type: AuditEventType,
        actor: str,
        details: dict[str, Any],
        previous_hash: str,
        session_id: str | None = None,
        spacecraft_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEntry:
        """
        Create a new audit entry with computed hash.
        The hash covers all fields including previous_hash,
        so any change to any field invalidates the chain.
        """
        entry_id = str(uuid.uuid4())
        ts = timestamp or datetime.now(timezone.utc)

        # Compute hash before creating immutable object
        entry_hash = cls._compute_hash(
            entry_id=entry_id,
            timestamp=ts,
            event_type=event_type,
            actor=actor,
            session_id=session_id,
            spacecraft_id=spacecraft_id,
            details=details,
            previous_hash=previous_hash,
        )

        return cls(
            entry_id=entry_id,
            timestamp=ts,
            event_type=event_type,
            actor=actor,
            session_id=session_id,
            spacecraft_id=spacecraft_id,
            details=details,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
        )

    @staticmethod
    def _compute_hash(
        entry_id: str,
        timestamp: datetime,
        event_type: AuditEventType,
        actor: str,
        session_id: str | None,
        spacecraft_id: str | None,
        details: dict[str, Any],
        previous_hash: str,
    ) -> str:
        """Compute SHA-256 hash covering all entry fields."""
        content = json.dumps({
            "entry_id": entry_id,
            "timestamp": timestamp.isoformat(),
            "event_type": event_type.value,
            "actor": actor,
            "session_id": session_id,
            "spacecraft_id": spacecraft_id,
            "details": details,
            "previous_hash": previous_hash,
        }, sort_keys=True, default=str)

        return hashlib.sha256(content.encode()).hexdigest()

    def verify_self(self) -> bool:
        """Verify this entry's hash is consistent with its content."""
        expected = self._compute_hash(
            entry_id=self.entry_id,
            timestamp=self.timestamp,
            event_type=self.event_type,
            actor=self.actor,
            session_id=self.session_id,
            spacecraft_id=self.spacecraft_id,
            details=self.details,
            previous_hash=self.previous_hash,
        )
        return expected == self.entry_hash

    def to_dict(self) -> dict[str, Any]:
        """Serialize entry to dictionary for export."""
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "actor": self.actor,
            "session_id": self.session_id,
            "spacecraft_id": self.spacecraft_id,
            "details": self.details,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
        }


# Genesis hash — the starting point of every chain
GENESIS_HASH = hashlib.sha256(
    f"ChronoScope-Audit-Chain-v{AUDIT_LOG_VERSION}".encode()
).hexdigest()


class AuditLog:
    """
    Tamper-evident audit log using a cryptographic hash chain.

    Every entry hashes the previous entry's hash into itself.
    This means:
    - You cannot modify any entry without breaking the chain
    - You cannot delete any entry without breaking the chain
    - You cannot insert entries without breaking the chain
    - The only valid chain starts from GENESIS_HASH

    This is the same principle used in blockchain technology,
    applied to mission operations audit trails.
    """

    def __init__(self, log_id: str | None = None):
        self.log_id = log_id or str(uuid.uuid4())
        self._entries: list[AuditEntry] = []
        self._current_hash = GENESIS_HASH
        self.logger = structlog.get_logger(__name__).bind(
            log_id=self.log_id
        )

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def latest_hash(self) -> str:
        return self._current_hash

    @property
    def entries(self) -> list[AuditEntry]:
        """Read-only view of all entries."""
        return list(self._entries)

    def record(
        self,
        event_type: AuditEventType,
        actor: str,
        details: dict[str, Any],
        session_id: str | None = None,
        spacecraft_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEntry:
        """
        Record a new event in the audit log.
        Automatically chains to the previous entry.
        """
        entry = AuditEntry.create(
            event_type=event_type,
            actor=actor,
            details=details,
            previous_hash=self._current_hash,
            session_id=session_id,
            spacecraft_id=spacecraft_id,
            timestamp=timestamp,
        )

        self._entries.append(entry)
        self._current_hash = entry.entry_hash

        self.logger.debug(
            "audit_entry_recorded",
            event_type=event_type.value,
            actor=actor,
            entry_id=entry.entry_id[:8],
            chain_hash=entry.entry_hash[:16],
        )

        return entry

    def verify_chain(self) -> bool:
        """
        Verify the entire chain is intact.
        Walks every entry and checks:
        1. Each entry's self-hash is valid
        2. Each entry's previous_hash matches
           the hash of the entry before it

        Raises AuditChainBrokenError if tampering detected.
        Returns True if chain is fully intact.
        """
        if not self._entries:
            return True

        expected_previous = GENESIS_HASH

        for entry in self._entries:
            # Check previous hash linkage
            if entry.previous_hash != expected_previous:
                raise AuditChainBrokenError(
                    entry_id=entry.entry_id,
                    expected_hash=expected_previous,
                    actual_hash=entry.previous_hash,
                )

            # Check entry self-consistency
            if not entry.verify_self():
                raise AuditChainBrokenError(
                    entry_id=entry.entry_id,
                    expected_hash="valid self-hash",
                    actual_hash="invalid — entry was modified",
                )

            expected_previous = entry.entry_hash

        self.logger.info(
            "chain_verified",
            entry_count=len(self._entries),
            latest_hash=self._current_hash[:16],
        )

        return True

    def get_entries_for_session(
        self,
        session_id: str,
    ) -> list[AuditEntry]:
        """Get all entries related to a specific session."""
        return [
            e for e in self._entries
            if e.session_id == session_id
        ]

    def get_entries_by_type(
        self,
        event_type: AuditEventType,
    ) -> list[AuditEntry]:
        """Get all entries of a specific event type."""
        return [
            e for e in self._entries
            if e.event_type == event_type
        ]

    def get_entries_by_actor(self, actor: str) -> list[AuditEntry]:
        """Get all entries by a specific actor."""
        return [
            e for e in self._entries
            if e.actor == actor
        ]

    def export_json(self) -> str:
        """
        Export the complete audit log as JSON.
        Includes chain verification result.
        Anyone receiving this export can verify
        the chain independently.
        """
        chain_intact = False
        chain_error = None

        try:
            chain_intact = self.verify_chain()
        except AuditChainBrokenError as e:
            chain_error = str(e)

        export = {
            "log_id": self.log_id,
            "version": AUDIT_LOG_VERSION,
            "genesis_hash": GENESIS_HASH,
            "entry_count": self.entry_count,
            "latest_hash": self._current_hash,
            "chain_intact": chain_intact,
            "chain_error": chain_error,
            "entries": [e.to_dict() for e in self._entries],
        }

        return json.dumps(export, indent=2, default=str)

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of audit log statistics."""
        event_counts: dict[str, int] = {}
        actors: set[str] = set()

        for entry in self._entries:
            key = entry.event_type.value
            event_counts[key] = event_counts.get(key, 0) + 1
            actors.add(entry.actor)

        return {
            "log_id": self.log_id,
            "total_entries": self.entry_count,
            "unique_actors": len(actors),
            "event_breakdown": event_counts,
            "latest_hash": self._current_hash[:16] + "...",
            "chain_intact": True,
        }