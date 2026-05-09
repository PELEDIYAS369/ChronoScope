"""
Unit tests for operational event bus.
"""

import pytest
from datetime import datetime, timezone, timedelta
from src.chronoscope.observability.events import EventBus, EventType, OperationalEvent


class TestOperationalEvent:

    def test_create_event(self):
        event = OperationalEvent.create(
            event_type=EventType.SOURCE_INGESTED,
            source="noaa_dscovr",
            details={"packets": 223},
        )
        assert event.event_type == EventType.SOURCE_INGESTED
        assert event.source == "noaa_dscovr"
        assert event.details["packets"] == 223
        assert event.event_id is not None

    def test_event_is_immutable(self):
        event = OperationalEvent.create(
            EventType.SOURCE_INGESTED, "test", {}
        )
        with pytest.raises(Exception):
            event.source = "modified"

    def test_to_dict(self):
        event = OperationalEvent.create(
            EventType.ALERT_CREATED,
            source="DSCOVR",
            details={"alert_id": "abc"},
        )
        d = event.to_dict()
        assert "event_id" in d
        assert "event_type" in d
        assert "timestamp" in d
        assert "source" in d
        assert "details" in d


class TestEventBus:

    def test_emit_event(self):
        bus = EventBus()
        event = bus.emit(EventType.SOURCE_INGESTED, "noaa_dscovr", {})
        assert bus.total_events == 1
        assert event.event_type == EventType.SOURCE_INGESTED

    def test_emit_source_ingested(self):
        bus = EventBus()
        event = bus.emit_source_ingested("noaa_dscovr", 223, 0.45, "session-abc")
        assert event.event_type == EventType.SOURCE_INGESTED
        assert event.details["packets_ingested"] == 223
        assert event.details["duration_seconds"] == 0.45

    def test_emit_source_failed(self):
        bus = EventBus()
        event = bus.emit_source_failed("noaa_dscovr", "Timeout", "network")
        assert event.event_type == EventType.SOURCE_FAILED
        assert event.details["reason"] == "Timeout"
        assert event.details["error_type"] == "network"

    def test_emit_rule_evaluated(self):
        bus = EventBus()
        event = bus.emit_rule_evaluated(
            "dscovr-temp-extreme", "DSCOVR", True, "ion_temperature_k"
        )
        assert event.event_type == EventType.RULE_EVALUATED
        assert event.details["triggered"] is True

    def test_emit_alert_created(self):
        bus = EventBus()
        event = bus.emit_alert_created(
            "alert-001", "DSCOVR", "medium",
            "ion_temperature_k", "dscovr-temp-extreme"
        )
        assert event.event_type == EventType.ALERT_CREATED
        assert event.details["severity"] == "medium"

    def test_emit_alert_resolved(self):
        bus = EventBus()
        event = bus.emit_alert_resolved("alert-001", "DSCOVR", "operator-001")
        assert event.event_type == EventType.ALERT_RESOLVED
        assert event.details["resolved_by"] == "operator-001"

    def test_emit_system_degraded(self):
        bus = EventBus()
        event = bus.emit_system_degraded(
            "stale_source", "noaa_dscovr", "Data 6h old", "warning"
        )
        assert event.event_type == EventType.SYSTEM_DEGRADED
        assert event.details["severity"] == "warning"

    def test_get_events_in_window(self):
        bus = EventBus()
        now = datetime.now(timezone.utc)
        bus.emit(EventType.SOURCE_INGESTED, "src1", {})
        bus.emit(EventType.SOURCE_FAILED, "src2", {})
        start = now - timedelta(minutes=1)
        end = now + timedelta(minutes=1)
        events = bus.get_events_in_window(start, end)
        assert len(events) == 2

    def test_get_events_in_window_excludes_outside(self):
        bus = EventBus()
        bus.emit(EventType.SOURCE_INGESTED, "src1", {})
        future_start = datetime.now(timezone.utc) + timedelta(hours=1)
        future_end = datetime.now(timezone.utc) + timedelta(hours=2)
        events = bus.get_events_in_window(future_start, future_end)
        assert len(events) == 0

    def test_get_events_by_type(self):
        bus = EventBus()
        bus.emit(EventType.SOURCE_INGESTED, "src1", {})
        bus.emit(EventType.SOURCE_INGESTED, "src2", {})
        bus.emit(EventType.SOURCE_FAILED, "src3", {})
        ingested = bus.get_events_by_type(EventType.SOURCE_INGESTED)
        assert len(ingested) == 2

    def test_clear_before(self):
        bus = EventBus()
        bus.emit(EventType.SOURCE_INGESTED, "src1", {})
        bus.emit(EventType.SOURCE_INGESTED, "src2", {})
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        cleared = bus.clear_before(future)
        assert cleared == 2
        assert bus.total_events == 0

    def test_total_events(self):
        bus = EventBus()
        for _ in range(5):
            bus.emit(EventType.SOURCE_INGESTED, "src", {})
        assert bus.total_events == 5