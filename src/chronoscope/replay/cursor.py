"""
ChronoScope AI — Replay Cursor
Tracks the current position within a replay session.
The cursor is the pointer that moves through time.
Moving it forward or backward is what replay means.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.chronoscope.domain.models import TelemetryPacket


@dataclass
class ReplayCursor:
    """
    Tracks position within a replay session.
    Think of this as the playhead on a timeline.
    It knows where we are, where we came from,
    and what packets are visible at this moment.
    """
    session_id: str
    start_time: datetime
    end_time: datetime
    current_time: datetime
    current_index: int = 0
    speed: float = 1.0
    is_playing: bool = False
    total_packets: int = 0

    def __post_init__(self) -> None:
        if self.speed <= 0:
            raise ValueError(f"Speed must be positive, got {self.speed}")
        if self.current_time < self.start_time:
            raise ValueError("Current time cannot be before start time")
        if self.current_time > self.end_time:
            raise ValueError("Current time cannot be after end time")

    @classmethod
    def create(
        cls,
        session_id: str,
        start_time: datetime,
        end_time: datetime,
        total_packets: int,
        speed: float = 1.0,
    ) -> ReplayCursor:
        """Create a new cursor positioned at the start of the session."""
        return cls(
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            current_time=start_time,
            current_index=0,
            speed=speed,
            is_playing=False,
            total_packets=total_packets,
        )

    @property
    def progress(self) -> float:
        """Return progress through session as 0.0 to 1.0."""
        total = (self.end_time - self.start_time).total_seconds()
        if total <= 0:
            return 0.0
        elapsed = (self.current_time - self.start_time).total_seconds()
        return min(max(elapsed / total, 0.0), 1.0)

    @property
    def is_at_start(self) -> bool:
        return self.current_index == 0

    @property
    def is_at_end(self) -> bool:
        return self.current_index >= self.total_packets - 1

    @property
    def elapsed_seconds(self) -> float:
        return (self.current_time - self.start_time).total_seconds()

    @property
    def remaining_seconds(self) -> float:
        return (self.end_time - self.current_time).total_seconds()

    def advance(self, to_index: int, to_time: datetime) -> ReplayCursor:
        """Return new cursor advanced to given index and time."""
        return ReplayCursor(
            session_id=self.session_id,
            start_time=self.start_time,
            end_time=self.end_time,
            current_time=to_time,
            current_index=to_index,
            speed=self.speed,
            is_playing=self.is_playing,
            total_packets=self.total_packets,
        )

    def seek(self, target_time: datetime) -> ReplayCursor:
        """Return new cursor seeked to target time."""
        clamped = max(self.start_time, min(target_time, self.end_time))
        return ReplayCursor(
            session_id=self.session_id,
            start_time=self.start_time,
            end_time=self.end_time,
            current_time=clamped,
            current_index=self.current_index,
            speed=self.speed,
            is_playing=self.is_playing,
            total_packets=self.total_packets,
        )

    def set_speed(self, speed: float) -> ReplayCursor:
        """Return new cursor with updated speed."""
        if speed <= 0:
            raise ValueError(f"Speed must be positive, got {speed}")
        return ReplayCursor(
            session_id=self.session_id,
            start_time=self.start_time,
            end_time=self.end_time,
            current_time=self.current_time,
            current_index=self.current_index,
            speed=speed,
            is_playing=self.is_playing,
            total_packets=self.total_packets,
        )

    def play(self) -> ReplayCursor:
        """Return new cursor in playing state."""
        return ReplayCursor(
            session_id=self.session_id,
            start_time=self.start_time,
            end_time=self.end_time,
            current_time=self.current_time,
            current_index=self.current_index,
            speed=self.speed,
            is_playing=True,
            total_packets=self.total_packets,
        )

    def pause(self) -> ReplayCursor:
        """Return new cursor in paused state."""
        return ReplayCursor(
            session_id=self.session_id,
            start_time=self.start_time,
            end_time=self.end_time,
            current_time=self.current_time,
            current_index=self.current_index,
            speed=self.speed,
            is_playing=False,
            total_packets=self.total_packets,
        )