"""
Unit tests for hourly operational report generator.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from src.chronoscope.observability.events import EventBus, EventType
from src.chronoscope.reporting.hourly import HourlyReportGenerator


def make_bus_with_data() -> EventBus:
    bus = EventBus()
    bus.emit_source_ingested("noaa_dscovr", 223, 0.45, "session-abc")
    bus.emit_source_ingested("ace_spacecraft", 198, 0.52, "session-def")
    bus.emit_source_failed("opensky_network", "Rate limited", "http_429")
    bus.emit_rule_evaluated(
        "dscovr-temp-extreme", "DSCOVR", True, "ion_temperature_k"
    )
    bus.emit_rule_evaluated(
        "dscovr-speed-high", "DSCOVR", False, "bulk_speed_km_s"
    )
    bus.emit_alert_created(
        "alert-001", "DSCOVR", "medium",
        "ion_temperature_k", "dscovr-temp-extreme"
    )
    bus.emit_system_degraded(
        "stale_source", "opensky_network",
        "OpenSky data 3h old", "warning"
    )
    return bus


class TestHourlyReportGenerator:

    def test_generate_report(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert report.report_id is not None
        assert report.total_ingestions == 2
        assert report.failed_ingestions == 1
        assert report.total_alerts == 1
        assert report.resolved_alerts == 0

    def test_report_window_defaults_to_last_hour(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        diff = report.window_end - report.window_start
        assert abs(diff.total_seconds() - 3600) < 5

    def test_report_custom_window(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        start = datetime.now(timezone.utc) - timedelta(hours=2)
        end = datetime.now(timezone.utc)
        report = gen.generate(window_start=start, window_end=end)
        assert report.window_start == start
        assert report.window_end == end

    def test_posture_nominal_when_no_failures(self):
        bus = EventBus()
        bus.emit_source_ingested("noaa_dscovr", 100, 0.3, "session-abc")
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert report.operational_posture == "NOMINAL"

    def test_posture_degraded_when_failures(self):
        bus = EventBus()
        bus.emit_source_failed("noaa_dscovr", "Timeout", "network")
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert report.operational_posture == "DEGRADED"

    def test_posture_degraded_when_system_degraded(self):
        bus = EventBus()
        bus.emit_system_degraded(
            "stale_source", "noaa_dscovr", "Stale", "warning"
        )
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert report.operational_posture == "DEGRADED"

    def test_posture_critical_when_critical_event(self):
        bus = EventBus()
        bus.emit_system_degraded(
            "source_unavailable", "noaa_dscovr", "Down", "critical"
        )
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert report.operational_posture == "CRITICAL"

    def test_source_summary_populated(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert "noaa_dscovr" in report.source_summary
        assert report.source_summary["noaa_dscovr"]["ingestions"] == 1
        assert report.source_summary["noaa_dscovr"]["total_packets"] == 223

    def test_rule_summary_populated(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert "dscovr-temp-extreme" in report.rule_summary
        assert report.rule_summary["dscovr-temp-extreme"] == 1

    def test_empty_window_returns_nominal(self):
        bus = EventBus()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert report.total_ingestions == 0
        assert report.total_alerts == 0
        assert report.operational_posture == "NOMINAL"

    def test_to_json_valid(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        raw = gen.to_json(report)
        parsed = json.loads(raw)
        assert parsed["report_type"] == "hourly_operational"
        assert "summary" in parsed
        assert "source_activity" in parsed
        assert "operational_posture" in parsed

    def test_to_json_summary_correct(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        parsed = json.loads(gen.to_json(report))
        summary = parsed["summary"]
        assert summary["total_ingestions"] == 2
        assert summary["failed_ingestions"] == 1
        assert summary["alerts_created"] == 1

    def test_to_markdown_contains_sections(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        md = gen.to_markdown(report)
        assert "# ChronoScope AI" in md
        assert "## Executive Summary" in md
        assert "## Source Activity Summary" in md
        assert "## Rule Evaluation Summary" in md
        assert "## Alerts Created" in md
        assert "## Degraded Conditions" in md
        assert "## Operational Posture Summary" in md

    def test_to_markdown_includes_report_id(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        md = gen.to_markdown(report)
        assert report.report_id in md

    def test_save_creates_files(self, tmp_path):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        json_path, md_path = gen.save(report, output_dir=str(tmp_path))
        assert (tmp_path / json_path.split("\\")[-1].split("/")[-1]).exists() or \
               len(list(tmp_path.glob("*.json"))) > 0
        assert len(list(tmp_path.glob("*.md"))) > 0

    def test_alerts_resolved_tracked(self):
        bus = EventBus()
        bus.emit_alert_created(
            "a1", "DSCOVR", "high", "bulk_speed_km_s", "dscovr-speed-high"
        )
        bus.emit_alert_resolved("a1", "DSCOVR", "operator-001")
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert report.total_alerts == 1
        assert report.resolved_alerts == 1

    def test_multiple_sources_in_summary(self):
        bus = EventBus()
        bus.emit_source_ingested("noaa_dscovr", 100, 0.3, "s1")
        bus.emit_source_ingested("ace_spacecraft", 200, 0.4, "s2")
        bus.emit_source_ingested("opensky_network", 50, 1.2, "s3")
        gen = HourlyReportGenerator(bus)
        report = gen.generate()
        assert len(report.source_summary) == 3

    def test_report_id_unique_per_generation(self):
        bus = make_bus_with_data()
        gen = HourlyReportGenerator(bus)
        r1 = gen.generate()
        r2 = gen.generate()
        assert r1.report_id != r2.report_id