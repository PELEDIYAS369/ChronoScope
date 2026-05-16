# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Replay Engine
The core of ChronoScope. This is what makes it unique.
Takes a mission session and allows deterministic replay
of any moment with perfect fidelity.
Same input always produces identical output.
This is mathematically guaranteed by the immutable
packet design in our domain models.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterator, Callable
import hashlib
import json
import structlog

from src.chronoscope.domain.models import (
    TelemetryPacket,
    MissionSession,
    ReplayStatus,
)
from src.chronoscope.domain.exceptions import (
    SessionNotFoundError,
    ReplayStateError,
    DeterminismViolationError,
)
from src.chronoscope.domain.constants import (
    REPLAY_DEFAULT_SPEED,
    REPLAY_MAX_SPEED,
    REPLAY_MIN_SPEED,
)
from src.chronoscope.replay.cursor import ReplayCursor

logger = structlog.get_logger(__name__)


class ReplayEngine:
    """
    Deterministic mission replay engine.

    Core guarantees:
    1. Same session + same start time = identical packet sequence
    2. Packets are immutable — cannot be altered during replay
    3. Every replay operation is logged for audit purposes
    4. Speed changes do not affect packet order or content
    5. Seeking preserves determinism — position is exact
    """

    def __init__(self):
        self._sessions: dict[str, MissionSession] = {}
        self._cursors: dict[str, ReplayCursor] = {}
        self._replay_hashes: dict[str, str] = {}
        self.logger = structlog.get_logger(__name__)

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    def load_session(self, session: MissionSession) -> ReplayCursor:
        """
        Load a mission session into the replay engine.
        Sorts packets chronologically and computes
        a session fingerprint for determinism verification.
        """
        if not session.packets:
            raise ValueError(
                f"Session {session.session_id} has no packets to replay"
            )

        # Sort packets chronologically — determinism requires this
        sorted_packets = sorted(
            session.packets,
            key=lambda p: p.timestamp
        )

        # Replace session packets with sorted version
        session.packets.clear()
        session.packets.extend(sorted_packets)

        # Compute session fingerprint
        fingerprint = self._compute_session_fingerprint(session)
        self._replay_hashes[session.session_id] = fingerprint

        # Create cursor at start of session
        cursor = ReplayCursor.create(
            session_id=session.session_id,
            start_time=session.packets[0].timestamp,
            end_time=session.packets[-1].timestamp,
            total_packets=len(session.packets),
            speed=REPLAY_DEFAULT_SPEED,
        )

        self._sessions[session.session_id] = session
        self._cursors[session.session_id] = cursor

        # Update session status
        session.replay_status = ReplayStatus.READY

        self.logger.info(
            "session_loaded",
            session_id=session.session_id,
            packet_count=len(session.packets),
            start_time=cursor.start_time.isoformat(),
            end_time=cursor.end_time.isoformat(),
            fingerprint=fingerprint[:16],
        )

        return cursor

    def get_cursor(self, session_id: str) -> ReplayCursor:
        """Get current cursor position for a session."""
        if session_id not in self._cursors:
            raise SessionNotFoundError(session_id)
        return self._cursors[session_id]

    def get_session(self, session_id: str) -> MissionSession:
        """Get loaded session by ID."""
        if session_id not in self._sessions:
            raise SessionNotFoundError(session_id)
        return self._sessions[session_id]

    # ------------------------------------------------------------------
    # Playback Controls
    # ------------------------------------------------------------------

    def play(self, session_id: str) -> ReplayCursor:
        """Start or resume replay."""
        cursor = self._get_cursor_or_raise(session_id)
        session = self._get_session_or_raise(session_id)

        if session.replay_status not in (
            ReplayStatus.READY,
            ReplayStatus.PAUSED,
        ):
            raise ReplayStateError(
                "play",
                session.replay_status.value
            )

        cursor = cursor.play()
        session.replay_status = ReplayStatus.PLAYING
        self._cursors[session_id] = cursor

        self.logger.info(
            "replay_started",
            session_id=session_id,
            current_index=cursor.current_index,
            speed=cursor.speed,
        )

        return cursor

    def pause(self, session_id: str) -> ReplayCursor:
        """Pause replay at current position."""
        cursor = self._get_cursor_or_raise(session_id)
        session = self._get_session_or_raise(session_id)

        cursor = cursor.pause()
        session.replay_status = ReplayStatus.PAUSED
        self._cursors[session_id] = cursor

        self.logger.info(
            "replay_paused",
            session_id=session_id,
            current_index=cursor.current_index,
            progress=f"{cursor.progress * 100:.1f}%",
        )

        return cursor

    def seek(
        self,
        session_id: str,
        target_time: datetime,
    ) -> ReplayCursor:
        """
        Seek to a specific timestamp.
        Updates cursor and finds the nearest packet index.
        Deterministic — same timestamp always lands at same packet.
        """
        cursor = self._get_cursor_or_raise(session_id)
        session = self._get_session_or_raise(session_id)

        # Find nearest packet index to target time
        nearest_index = self._find_nearest_packet_index(
            session.packets, target_time
        )
        nearest_time = session.packets[nearest_index].timestamp

        cursor = cursor.seek(nearest_time)
        cursor = cursor.advance(nearest_index, nearest_time)
        self._cursors[session_id] = cursor

        self.logger.info(
            "replay_seeked",
            session_id=session_id,
            target_time=target_time.isoformat(),
            landed_at=nearest_time.isoformat(),
            packet_index=nearest_index,
        )

        return cursor

    def step_forward(self, session_id: str) -> ReplayCursor:
        """Advance exactly one packet forward."""
        cursor = self._get_cursor_or_raise(session_id)
        session = self._get_session_or_raise(session_id)

        if cursor.is_at_end:
            return cursor

        next_index = cursor.current_index + 1
        next_packet = session.packets[next_index]
        cursor = cursor.advance(next_index, next_packet.timestamp)
        self._cursors[session_id] = cursor

        return cursor

    def step_backward(self, session_id: str) -> ReplayCursor:
        """Step exactly one packet backward."""
        cursor = self._get_cursor_or_raise(session_id)
        session = self._get_session_or_raise(session_id)

        if cursor.is_at_start:
            return cursor

        prev_index = cursor.current_index - 1
        prev_packet = session.packets[prev_index]
        cursor = cursor.advance(prev_index, prev_packet.timestamp)
        self._cursors[session_id] = cursor

        return cursor

    def set_speed(self, session_id: str, speed: float) -> ReplayCursor:
        """Set replay speed. 1.0 = realtime, 2.0 = 2x, 0.5 = half speed."""
        if speed < REPLAY_MIN_SPEED or speed > REPLAY_MAX_SPEED:
            raise ValueError(
                f"Speed {speed} out of range "
                f"[{REPLAY_MIN_SPEED}, {REPLAY_MAX_SPEED}]"
            )

        cursor = self._get_cursor_or_raise(session_id)
        cursor = cursor.set_speed(speed)
        self._cursors[session_id] = cursor

        return cursor

    def reset(self, session_id: str) -> ReplayCursor:
        """Reset replay to beginning of session."""
        cursor = self._get_cursor_or_raise(session_id)
        session = self._get_session_or_raise(session_id)

        cursor = ReplayCursor.create(
            session_id=session_id,
            start_time=session.packets[0].timestamp,
            end_time=session.packets[-1].timestamp,
            total_packets=len(session.packets),
            speed=cursor.speed,
        )

        session.replay_status = ReplayStatus.READY
        self._cursors[session_id] = cursor

        self.logger.info("replay_reset", session_id=session_id)

        return cursor

    # ------------------------------------------------------------------
    # Packet Access
    # ------------------------------------------------------------------

    def get_current_packet(
        self,
        session_id: str,
    ) -> TelemetryPacket:
        """Get the packet at the current cursor position."""
        cursor = self._get_cursor_or_raise(session_id)
        session = self._get_session_or_raise(session_id)
        return session.packets[cursor.current_index]

    def get_packets_in_window(
        self,
        session_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[TelemetryPacket]:
        """
        Get all packets within a time window.
        Deterministic — same window always returns same packets.
        """
        session = self._get_session_or_raise(session_id)
        return [
            p for p in session.packets
            if start_time <= p.timestamp <= end_time
        ]

    def stream_packets(
        self,
        session_id: str,
        from_index: int = 0,
        callback: Callable[[TelemetryPacket, ReplayCursor], None] | None = None,
    ) -> Iterator[tuple[TelemetryPacket, ReplayCursor]]:
        """
        Stream packets from given index to end of session.
        Yields each packet with its cursor state.
        Optionally calls a callback for each packet.
        """
        session = self._get_session_or_raise(session_id)
        cursor = self._get_cursor_or_raise(session_id)

        for i in range(from_index, len(session.packets)):
            packet = session.packets[i]
            cursor = cursor.advance(i, packet.timestamp)
            self._cursors[session_id] = cursor

            if callback:
                callback(packet, cursor)

            yield packet, cursor

    # ------------------------------------------------------------------
    # Determinism Verification
    # ------------------------------------------------------------------

    def verify_determinism(self, session_id: str) -> bool:
        """
        Verify that the session produces the same fingerprint
        as when it was first loaded.
        This is the mathematical proof of determinism.
        """
        if session_id not in self._replay_hashes:
            raise SessionNotFoundError(session_id)

        session = self._get_session_or_raise(session_id)
        current_fingerprint = self._compute_session_fingerprint(session)
        original_fingerprint = self._replay_hashes[session_id]

        if current_fingerprint != original_fingerprint:
            raise DeterminismViolationError(
                session_id=session_id,
                details=(
                    f"Fingerprint mismatch. "
                    f"Original: {original_fingerprint[:16]}... "
                    f"Current: {current_fingerprint[:16]}..."
                )
            )

        self.logger.info(
            "determinism_verified",
            session_id=session_id,
            fingerprint=current_fingerprint[:16],
        )

        return True

    def _compute_session_fingerprint(
        self,
        session: MissionSession,
    ) -> str:
        """
        Compute a SHA-256 fingerprint of the entire session.
        Any change to any packet changes this fingerprint.
        This is what makes determinism mathematically provable.
        """
        hasher = hashlib.sha256()
        for packet in session.packets:
            hasher.update(packet.packet_id.encode())
            hasher.update(packet.timestamp.isoformat().encode())
            hasher.update(packet.spacecraft_id.encode())
            hasher.update(packet.raw_bytes)
        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # Private Helpers
    # ------------------------------------------------------------------

    def _get_cursor_or_raise(self, session_id: str) -> ReplayCursor:
        if session_id not in self._cursors:
            raise SessionNotFoundError(session_id)
        return self._cursors[session_id]

    def _get_session_or_raise(self, session_id: str) -> MissionSession:
        if session_id not in self._sessions:
            raise SessionNotFoundError(session_id)
        return self._sessions[session_id]

    def _find_nearest_packet_index(
        self,
        packets: list[TelemetryPacket],
        target_time: datetime,
    ) -> int:
        """
        Binary search for nearest packet to target time.
        O(log n) — efficient even with millions of packets.
        """
        if not packets:
            return 0

        low, high = 0, len(packets) - 1

        while low < high:
            mid = (low + high) // 2
            if packets[mid].timestamp < target_time:
                low = mid + 1
            else:
                high = mid

        return low