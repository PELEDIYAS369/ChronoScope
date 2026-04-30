"""
Integration test — reporter works end-to-end with controller.
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from src.chronoscope.controller import ChronoScopeController
from src.chronoscope.reporter import MissionReporter
from src.chronoscope.audit.log import AuditLog
from src.chronoscope.domain.models import MissionPhase


class TestReporterIntegration:

    def _make_controller(self):
        from src.chronoscope.ai.detector import AnomalyDetector, build_dscovr_rules
        from src.chronoscope.ingestion.noaa_dscovr import NOAADscovrIngester
        audit = AuditLog()
        controller = ChronoScopeController(audit_log=audit)
        return controller, audit

    def test_generate_report_from_empty_session(self):
        controller, audit = self._make_controller()
        session = controller.create_session(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 15, 2, tzinfo=timezone.utc),
        )
        reporter = MissionReporter(audit_log=audit)
        report = reporter.generate(session)
        assert report.spacecraft_id == "DSCOVR"
        assert report.total_packets == 0
        assert report.audit_chain_intact is True
        assert report.health_rating == "NOMINAL"

    def test_report_json_round_trip(self):
        controller, audit = self._make_controller()
        session = controller.create_session(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 15, 2, tzinfo=timezone.utc),
        )
        reporter = MissionReporter(audit_log=audit)
        report = reporter.generate(session)
        raw = report.to_json()
        parsed = json.loads(raw)
        assert parsed["session_id"] == session.session_id
        assert parsed["spacecraft_id"] == "DSCOVR"
        assert "health_rating" in parsed

    def test_report_markdown_renders(self):
        controller, audit = self._make_controller()
        session = controller.create_session(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 15, 2, tzinfo=timezone.utc),
        )
        reporter = MissionReporter(audit_log=audit)
        report = reporter.generate(session)
        md = report.to_markdown()
        assert "DSCOVR" in md
        assert "ChronoScope AI" in md
        assert "NOMINAL" in md

    def test_audit_entries_captured_in_report(self):
        controller, audit = self._make_controller()
        session = controller.create_session(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
        )
        reporter = MissionReporter(audit_log=audit)
        report = reporter.generate(session)
        assert report.audit_entries >= 2

    def test_report_with_fingerprint(self):
        controller, audit = self._make_controller()
        session = controller.create_session(
            spacecraft_id="DSCOVR",
            mission_phase=MissionPhase.NOMINAL,
            start_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
        )
        reporter = MissionReporter(audit_log=audit)
        report = reporter.generate(
            session,
            fingerprint="deadbeef1234",
            determinism_verified=True,
        )
        assert report.session_fingerprint == "deadbeef1234"
        assert report.determinism_verified is True
        d = report.to_dict()
        assert d["determinism"]["verified"] is True