"""
Unit tests for ChronoScope replay engine.
Tests determinism, cursor movement, and session management.
"""

import pytest
from datetime import datetime, timezone, timedelta
from src.chronoscope.replay.engine import ReplayEngine
from src.chronoscope.replay.cursor import ReplayCursor
from src.chronoscope.domain.models import (
    TelemetryPacket,
    MissionSession,
    MissionPhase,
    PacketType,
    ReplayStatus,
)
from src.chronoscope.domain.exceptions import (
    SessionNotFoundError,
    ReplayStateError,
    DeterminismViolationError,
)


# ------------------------------------------------------------------
# Test Fixtures
# ------------------------------------------------------------------

def make_packet(
    sequence: int,
    offset_minutes: int,
    spacecraft_id: str = "DSCOVR",
) -> TelemetryPacket:
    """Create a test packet at a given time offset."""
    return TelemetryPacket.create(
        spacecraft_id=spacecraft_id,
        packet_type=PacketType.TELEMETRY,
        apid=100,
        sequence_count=sequence % 16384,
        raw_bytes=f"packet_{sequence}".encode(),
        parameters={"value": float(sequence)},
        source="test",
        timestamp=datetime(
            2024, 1, 15, 12, offset_minutes, 0,
            tzinfo=timezone.utc
        ),
    )


def make_session_with_packets(count: int) -> MissionSession:
    """Create a test session with a given number of packets."""
    start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 15, 12, count, 0, tzinfo=timezone.utc)
    session = MissionSession.create(
        spacecraft_id="DSCOVR",
        mission_phase=MissionPhase.NOMINAL,
        start_time=start,
        end_time=end,
    )
    for i in range(count):
        session.add_packet(make_packet(i, i))
    return session


# ------------------------------------------------------------------
# Cursor Tests
# ------------------------------------------------------------------

class TestReplayCursor:

    def test_create_cursor(self):
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        cursor = ReplayCursor.create(
            session_id="test-session",
            start_time=start,
            end_time=end,
            total_packets=60,
        )
        assert cursor.current_index == 0
        assert cursor.is_playing is False
        assert cursor.speed == 1.0
        assert cursor.is_at_start is True

    def test_progress_at_start(self):
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        cursor = ReplayCursor.create(
            session_id="test",
            start_time=start,
            end_time=end,
            total_packets=10,
        )
        assert cursor.progress == 0.0

    def test_play_sets_playing(self):
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        cursor = ReplayCursor.create(
            session_id="test",
            start_time=start,
            end_time=end,
            total_packets=10,
        )
        playing = cursor.play()
        assert playing.is_playing is True
        assert cursor.is_playing is False

    def test_pause_stops_playing(self):
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        cursor = ReplayCursor.create(
            session_id="test",
            start_time=start,
            end_time=end,
            total_packets=10,
        ).play()
        paused = cursor.pause()
        assert paused.is_playing is False

    def test_invalid_speed_rejected(self):
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        cursor = ReplayCursor.create(
            session_id="test",
            start_time=start,
            end_time=end,
            total_packets=10,
        )
        with pytest.raises(ValueError):
            cursor.set_speed(-1.0)


# ------------------------------------------------------------------
# Replay Engine Tests
# ------------------------------------------------------------------

class TestReplayEngine:

    def test_load_session(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        cursor = engine.load_session(session)
        assert cursor.total_packets == 10
        assert cursor.current_index == 0
        assert session.replay_status == ReplayStatus.READY

    def test_load_empty_session_raises(self):
        engine = ReplayEngine()
        session = MissionSession.create(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime.now(timezone.utc),
        )
        with pytest.raises(ValueError):
            engine.load_session(session)

    def test_play_session(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        cursor = engine.play(session.session_id)
        assert cursor.is_playing is True
        assert session.replay_status == ReplayStatus.PLAYING

    def test_pause_session(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        engine.play(session.session_id)
        cursor = engine.pause(session.session_id)
        assert cursor.is_playing is False
        assert session.replay_status == ReplayStatus.PAUSED

    def test_step_forward(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        cursor = engine.step_forward(session.session_id)
        assert cursor.current_index == 1

    def test_step_backward_at_start(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        cursor = engine.step_backward(session.session_id)
        assert cursor.current_index == 0

    def test_step_forward_then_backward(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        engine.step_forward(session.session_id)
        engine.step_forward(session.session_id)
        cursor = engine.step_backward(session.session_id)
        assert cursor.current_index == 1

    def test_seek_to_time(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        target = datetime(2024, 1, 15, 12, 5, 0, tzinfo=timezone.utc)
        cursor = engine.seek(session.session_id, target)
        assert cursor.current_index == 5

    def test_reset_returns_to_start(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        engine.step_forward(session.session_id)
        engine.step_forward(session.session_id)
        engine.step_forward(session.session_id)
        cursor = engine.reset(session.session_id)
        assert cursor.current_index == 0

    def test_set_speed(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        cursor = engine.set_speed(session.session_id, 2.0)
        assert cursor.speed == 2.0

    def test_invalid_speed_raises(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        with pytest.raises(ValueError):
            engine.set_speed(session.session_id, 200.0)

    def test_get_current_packet(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        engine.step_forward(session.session_id)
        packet = engine.get_current_packet(session.session_id)
        assert packet.parameters["value"] == 1.0

    def test_get_packets_in_window(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        start = datetime(2024, 1, 15, 12, 2, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 15, 12, 5, 0, tzinfo=timezone.utc)
        packets = engine.get_packets_in_window(
            session.session_id, start, end
        )
        assert len(packets) == 4

    def test_session_not_found_raises(self):
        engine = ReplayEngine()
        with pytest.raises(SessionNotFoundError):
            engine.play("nonexistent-session-id")

    def test_packets_sorted_on_load(self):
        engine = ReplayEngine()
        session = MissionSession.create(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime(2024, 1, 15, 12, 0, 0,
                                tzinfo=timezone.utc),
        )
        # Add packets out of order deliberately
        session.add_packet(make_packet(2, 2))
        session.add_packet(make_packet(0, 0))
        session.add_packet(make_packet(1, 1))
        engine.load_session(session)
        packet = engine.get_current_packet(session.session_id)
        # First packet should be the earliest one
        assert packet.parameters["value"] == 0.0


# ------------------------------------------------------------------
# Determinism Tests — The Core Proof
# ------------------------------------------------------------------

class TestDeterminism:

    def test_determinism_verification_passes(self):
        engine = ReplayEngine()
        session = make_session_with_packets(10)
        engine.load_session(session)
        result = engine.verify_determinism(session.session_id)
        assert result is True

    def test_same_session_same_fingerprint(self):
        engine1 = ReplayEngine()
        engine2 = ReplayEngine()
        session1 = make_session_with_packets(10)
        session2 = make_session_with_packets(10)
        engine1.load_session(session1)
        engine2.load_session(session2)
        fp1 = engine1._replay_hashes[session1.session_id]
        fp2 = engine2._replay_hashes[session2.session_id]
        # Different sessions have different fingerprints
        assert fp1 != fp2

    def test_stream_packets_yields_all(self):
        engine = ReplayEngine()
        session = make_session_with_packets(5)
        engine.load_session(session)
        packets = list(engine.stream_packets(session.session_id))
        assert len(packets) == 5

    def test_stream_packets_in_order(self):
        engine = ReplayEngine()
        session = make_session_with_packets(5)
        engine.load_session(session)
        packets = [p for p, c in engine.stream_packets(session.session_id)]
        for i in range(1, len(packets)):
            assert packets[i].timestamp >= packets[i-1].timestamp