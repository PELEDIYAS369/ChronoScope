"""
Unit tests for ChronoScope audit log.
Tests hash chain integrity, tamper detection, and export.
"""

import pytest
import json
from datetime import datetime, timezone
from src.chronoscope.audit.log import (
    AuditLog,
    AuditEntry,
    AuditEventType,
    GENESIS_HASH,
)
from src.chronoscope.domain.exceptions import AuditChainBrokenError


class TestAuditEntry:

    def test_create_entry(self):
        entry = AuditEntry.create(
            event_type=AuditEventType.SESSION_CREATED,
            actor="system",
            details={"session_id": "abc123"},
            previous_hash=GENESIS_HASH,
        )
        assert entry.entry_id is not None
        assert entry.event_type == AuditEventType.SESSION_CREATED
        assert entry.actor == "system"
        assert entry.previous_hash == GENESIS_HASH
        assert entry.entry_hash is not None
        assert len(entry.entry_hash) == 64  # SHA-256 hex

    def test_entry_is_immutable(self):
        entry = AuditEntry.create(
            event_type=AuditEventType.SESSION_CREATED,
            actor="system",
            details={},
            previous_hash=GENESIS_HASH,
        )
        with pytest.raises(Exception):
            entry.actor = "hacker"

    def test_entry_self_verify_passes(self):
        entry = AuditEntry.create(
            event_type=AuditEventType.REPLAY_STARTED,
            actor="controller_01",
            details={"speed": 1.0},
            previous_hash=GENESIS_HASH,
        )
        assert entry.verify_self() is True

    def test_two_entries_have_different_hashes(self):
        e1 = AuditEntry.create(
            event_type=AuditEventType.SESSION_CREATED,
            actor="system",
            details={"session": "1"},
            previous_hash=GENESIS_HASH,
        )
        e2 = AuditEntry.create(
            event_type=AuditEventType.SESSION_CREATED,
            actor="system",
            details={"session": "2"},
            previous_hash=GENESIS_HASH,
        )
        assert e1.entry_hash != e2.entry_hash

    def test_to_dict_contains_all_fields(self):
        entry = AuditEntry.create(
            event_type=AuditEventType.ANOMALY_DETECTED,
            actor="ai_engine",
            details={"parameter": "voltage"},
            previous_hash=GENESIS_HASH,
            session_id="session-001",
            spacecraft_id="DSCOVR",
        )
        d = entry.to_dict()
        assert "entry_id" in d
        assert "timestamp" in d
        assert "event_type" in d
        assert "entry_hash" in d
        assert "previous_hash" in d
        assert d["spacecraft_id"] == "DSCOVR"


class TestAuditLog:

    def test_empty_log_verifies(self):
        log = AuditLog()
        assert log.verify_chain() is True

    def test_record_single_entry(self):
        log = AuditLog()
        entry = log.record(
            event_type=AuditEventType.SESSION_CREATED,
            actor="system",
            details={"test": True},
        )
        assert log.entry_count == 1
        assert entry.previous_hash == GENESIS_HASH

    def test_chain_links_correctly(self):
        log = AuditLog()
        e1 = log.record(
            event_type=AuditEventType.SESSION_CREATED,
            actor="system",
            details={},
        )
        e2 = log.record(
            event_type=AuditEventType.REPLAY_STARTED,
            actor="controller",
            details={},
        )
        # Second entry's previous_hash must equal first entry's hash
        assert e2.previous_hash == e1.entry_hash

    def test_three_entry_chain(self):
        log = AuditLog()
        e1 = log.record(AuditEventType.SESSION_CREATED, "system", {})
        e2 = log.record(AuditEventType.INGESTION_STARTED, "ingester", {})
        e3 = log.record(AuditEventType.REPLAY_STARTED, "controller", {})
        assert e2.previous_hash == e1.entry_hash
        assert e3.previous_hash == e2.entry_hash

    def test_verify_chain_passes_clean_log(self):
        log = AuditLog()
        for i in range(10):
            log.record(
                AuditEventType.REPLAY_SEEKED,
                "controller",
                {"step": i},
            )
        assert log.verify_chain() is True

    def test_tamper_detection(self):
        """
        Simulate tampering by directly mutating internal state.
        The chain verification must catch this.
        """
        log = AuditLog()
        log.record(AuditEventType.SESSION_CREATED, "system", {})
        log.record(AuditEventType.REPLAY_STARTED, "controller", {})

        # Tamper: replace entry hash to simulate modification
        # We do this by accessing the internal list directly
        # In a real attack someone would try to modify the stored data
        original_entries = log._entries
        # Simulate a tampered entry with wrong previous_hash
        tampered = AuditEntry(
            entry_id=original_entries[1].entry_id,
            timestamp=original_entries[1].timestamp,
            event_type=original_entries[1].event_type,
            actor=original_entries[1].actor,
            session_id=original_entries[1].session_id,
            spacecraft_id=original_entries[1].spacecraft_id,
            details=original_entries[1].details,
            previous_hash="0" * 64,  # Wrong previous hash
            entry_hash=original_entries[1].entry_hash,
        )
        log._entries[1] = tampered

        with pytest.raises(AuditChainBrokenError):
            log.verify_chain()

    def test_get_entries_for_session(self):
        log = AuditLog()
        log.record(
            AuditEventType.SESSION_CREATED,
            "system",
            {},
            session_id="session-A",
        )
        log.record(
            AuditEventType.REPLAY_STARTED,
            "controller",
            {},
            session_id="session-A",
        )
        log.record(
            AuditEventType.SESSION_CREATED,
            "system",
            {},
            session_id="session-B",
        )
        entries_a = log.get_entries_for_session("session-A")
        assert len(entries_a) == 2
        entries_b = log.get_entries_for_session("session-B")
        assert len(entries_b) == 1

    def test_get_entries_by_type(self):
        log = AuditLog()
        log.record(AuditEventType.SESSION_CREATED, "system", {})
        log.record(AuditEventType.SESSION_CREATED, "system", {})
        log.record(AuditEventType.REPLAY_STARTED, "controller", {})
        sessions = log.get_entries_by_type(AuditEventType.SESSION_CREATED)
        assert len(sessions) == 2

    def test_get_entries_by_actor(self):
        log = AuditLog()
        log.record(AuditEventType.REPLAY_STARTED, "alice", {})
        log.record(AuditEventType.REPLAY_PAUSED, "bob", {})
        log.record(AuditEventType.REPLAY_SEEKED, "alice", {})
        alice_entries = log.get_entries_by_actor("alice")
        assert len(alice_entries) == 2

    def test_export_json_valid(self):
        log = AuditLog()
        log.record(AuditEventType.SESSION_CREATED, "system",
                   {"session": "test-001"}, session_id="test-001")
        log.record(AuditEventType.INGESTION_COMPLETED, "ingester",
                   {"packets": 232}, session_id="test-001")
        exported = log.export_json()
        parsed = json.loads(exported)
        assert parsed["entry_count"] == 2
        assert parsed["chain_intact"] is True
        assert len(parsed["entries"]) == 2
        assert "genesis_hash" in parsed

    def test_export_json_entries_have_hashes(self):
        log = AuditLog()
        log.record(AuditEventType.ANOMALY_DETECTED, "ai_engine",
                   {"parameter": "speed"})
        exported = json.loads(log.export_json())
        entry = exported["entries"][0]
        assert len(entry["entry_hash"]) == 64
        assert len(entry["previous_hash"]) == 64

    def test_get_summary(self):
        log = AuditLog()
        log.record(AuditEventType.SESSION_CREATED, "system", {})
        log.record(AuditEventType.REPLAY_STARTED, "alice", {})
        log.record(AuditEventType.ANOMALY_DETECTED, "ai_engine", {})
        summary = log.get_summary()
        assert summary["total_entries"] == 3
        assert summary["unique_actors"] == 3

    def test_latest_hash_updates_with_each_entry(self):
        log = AuditLog()
        h0 = log.latest_hash
        log.record(AuditEventType.SESSION_CREATED, "system", {})
        h1 = log.latest_hash
        log.record(AuditEventType.REPLAY_STARTED, "controller", {})
        h2 = log.latest_hash
        assert h0 != h1
        assert h1 != h2
        assert h0 != h2

    def test_anomaly_event_with_full_details(self):
        log = AuditLog()
        log.record(
            event_type=AuditEventType.ANOMALY_DETECTED,
            actor="ai_engine",
            details={
                "parameter": "solar_wind_speed",
                "observed": 687.0,
                "expected_max": 600.0,
                "severity": "HIGH",
                "confidence": 0.91,
                "suggested_action": "switch_to_safe_mode",
                "action_success_rate": 0.942,
            },
            session_id="session-mars-001",
            spacecraft_id="DSCOVR",
        )
        assert log.verify_chain() is True
        entries = log.get_entries_by_type(AuditEventType.ANOMALY_DETECTED)
        assert len(entries) == 1
        assert entries[0].details["confidence"] == 0.91

    def test_operator_action_recorded(self):
        log = AuditLog()
        log.record(AuditEventType.ANOMALY_DETECTED, "ai_engine",
                   {"severity": "HIGH"}, session_id="s1")
        log.record(
            event_type=AuditEventType.OPERATOR_ACTION_TAKEN,
            actor="flight_controller_chen",
            details={
                "action": "switch_to_safe_mode",
                "reason": "Accepted AI recommendation",
                "anomaly_flag_id": "flag-001",
            },
            session_id="s1",
        )
        log.record(
            event_type=AuditEventType.OPERATOR_ACTION_OUTCOME,
            actor="system",
            details={
                "action": "switch_to_safe_mode",
                "outcome": "success",
                "spacecraft_nominal": True,
            },
            session_id="s1",
        )
        assert log.verify_chain() is True
        assert log.entry_count == 3
        actions = log.get_entries_by_type(
            AuditEventType.OPERATOR_ACTION_TAKEN
        )
        assert actions[0].actor == "flight_controller_chen"