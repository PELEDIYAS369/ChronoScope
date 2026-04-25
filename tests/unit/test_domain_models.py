"""
Unit tests for ChronoScope domain models.
These tests verify the contracts that everything else depends on.
"""

import pytest
from datetime import datetime, timezone
from src.chronoscope.domain.models import (
    TelemetryPacket,
    MissionEvent,
    AnomalyFlag,
    MissionSession,
    PacketType,
    MissionPhase,
    AnomalySeverity,
    ReplayStatus,
)
from src.chronoscope.domain.exceptions import (
    PacketValidationError,
    AuditChainBrokenError,
    DeterminismViolationError,
)


# ---------------------------------------------------------------------------
# TelemetryPacket Tests
# ---------------------------------------------------------------------------

class TestTelemetryPacket:

    def test_create_valid_packet(self):
        packet = TelemetryPacket.create(
            spacecraft_id="DSCOVR",
            packet_type=PacketType.TELEMETRY,
            apid=100,
            sequence_count=1,
            raw_bytes=b"\x00\x64\x00\x01\x00\x00",
            parameters={"voltage": 12.4, "temperature": 22.1},
            source="nasa_public",
        )
        assert packet.spacecraft_id == "DSCOVR"
        assert packet.apid == 100
        assert packet.packet_type == PacketType.TELEMETRY
        assert packet.packet_id is not None
        assert packet.timestamp is not None

    def test_packet_is_immutable(self):
        packet = TelemetryPacket.create(
            spacecraft_id="DSCOVR",
            packet_type=PacketType.TELEMETRY,
            apid=100,
            sequence_count=1,
            raw_bytes=b"\x00\x64\x00\x01\x00\x00",
            parameters={},
            source="test",
        )
        with pytest.raises(Exception):
            packet.spacecraft_id = "MODIFIED"

    def test_invalid_apid_rejected(self):
        with pytest.raises(ValueError, match="APID"):
            TelemetryPacket.create(
                spacecraft_id="DSCOVR",
                packet_type=PacketType.TELEMETRY,
                apid=9999,
                sequence_count=1,
                raw_bytes=b"\x00",
                parameters={},
                source="test",
            )

    def test_empty_spacecraft_id_rejected(self):
        with pytest.raises(ValueError, match="spacecraft_id"):
            TelemetryPacket.create(
                spacecraft_id="",
                packet_type=PacketType.TELEMETRY,
                apid=100,
                sequence_count=1,
                raw_bytes=b"\x00",
                parameters={},
                source="test",
            )

    def test_two_packets_have_unique_ids(self):
        p1 = TelemetryPacket.create(
            spacecraft_id="DSCOVR",
            packet_type=PacketType.TELEMETRY,
            apid=100,
            sequence_count=1,
            raw_bytes=b"\x00",
            parameters={},
            source="test",
        )
        p2 = TelemetryPacket.create(
            spacecraft_id="DSCOVR",
            packet_type=PacketType.TELEMETRY,
            apid=100,
            sequence_count=2,
            raw_bytes=b"\x00",
            parameters={},
            source="test",
        )
        assert p1.packet_id != p2.packet_id


# ---------------------------------------------------------------------------
# AnomalyFlag Tests
# ---------------------------------------------------------------------------

class TestAnomalyFlag:

    def test_anomaly_requires_reason(self):
        with pytest.raises(ValueError, match="reason"):
            AnomalyFlag(
                flag_id="test",
                timestamp=datetime.now(timezone.utc),
                spacecraft_id="DSCOVR",
                severity=AnomalySeverity.HIGH,
                parameter_name="voltage",
                observed_value=15.9,
                expected_range=(10.0, 15.0),
                reason="",
                confidence=0.95,
                source_packet_id="pkt-001",
            )

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="Confidence"):
            AnomalyFlag(
                flag_id="test",
                timestamp=datetime.now(timezone.utc),
                spacecraft_id="DSCOVR",
                severity=AnomalySeverity.HIGH,
                parameter_name="voltage",
                observed_value=15.9,
                expected_range=(10.0, 15.0),
                reason="Voltage exceeded upper limit",
                confidence=1.5,
                source_packet_id="pkt-001",
            )


# ---------------------------------------------------------------------------
# MissionSession Tests
# ---------------------------------------------------------------------------

class TestMissionSession:

    def test_create_session(self):
        session = MissionSession.create(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime.now(timezone.utc),
        )
        assert session.spacecraft_id == "DSCOVR"
        assert session.replay_status == ReplayStatus.IDLE
        assert session.packet_count == 0
        assert session.anomaly_count == 0

    def test_add_packet_to_session(self):
        session = MissionSession.create(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime.now(timezone.utc),
        )
        packet = TelemetryPacket.create(
            spacecraft_id="DSCOVR",
            packet_type=PacketType.TELEMETRY,
            apid=100,
            sequence_count=1,
            raw_bytes=b"\x00",
            parameters={},
            source="test",
        )
        session.add_packet(packet)
        assert session.packet_count == 1

    def test_session_duration(self):
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        session = MissionSession.create(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=start,
            end_time=end,
        )
        assert session.duration_seconds == 3600.0


# ---------------------------------------------------------------------------
# Exception Tests
# ---------------------------------------------------------------------------

class TestExceptions:

    def test_audit_chain_broken_error(self):
        with pytest.raises(AuditChainBrokenError):
            raise AuditChainBrokenError(
                entry_id="entry-001",
                expected_hash="abc123",
                actual_hash="xyz789",
            )

    def test_determinism_violation_error(self):
        with pytest.raises(DeterminismViolationError):
            raise DeterminismViolationError(
                session_id="session-001",
                details="Output hash mismatch",
            )