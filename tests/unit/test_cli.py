"""
Unit tests for ChronoScope CLI.
"""

import pytest
from unittest.mock import patch, MagicMock
from src.chronoscope.cli import build_parser, main


class TestCLIParser:

    def test_status_command(self):
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_ingest_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["ingest"])
        assert args.command == "ingest"
        assert args.spacecraft == "DSCOVR"
        assert args.hours == 2.0

    def test_ingest_custom_args(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--spacecraft", "ACE", "--hours", "6"])
        assert args.spacecraft == "ACE"
        assert args.hours == 6.0

    def test_replay_command(self):
        parser = build_parser()
        args = parser.parse_args(["replay", "session-abc-123"])
        assert args.command == "replay"
        assert args.session_id == "session-abc-123"

    def test_anomalies_command(self):
        parser = build_parser()
        args = parser.parse_args(["anomalies", "session-xyz"])
        assert args.command == "anomalies"
        assert args.session_id == "session-xyz"

    def test_audit_command(self):
        parser = build_parser()
        args = parser.parse_args(["audit"])
        assert args.command == "audit"

    def test_export_command(self):
        parser = build_parser()
        args = parser.parse_args(["export", "session-001"])
        assert args.command == "export"
        assert args.session_id == "session-001"

    def test_export_with_output(self):
        parser = build_parser()
        args = parser.parse_args(["export", "session-001", "--output", "out.json"])
        assert args.output == "out.json"

    def test_no_command_returns_zero(self):
        result = main([])
        assert result == 0


class TestCLICommands:

    def test_status_command_runs(self):
        mock_controller = MagicMock()
        mock_controller.get_health.return_value = {
            "status": "NOMINAL",
            "sessions_loaded": 2,
            "total_packets": 500,
            "total_anomalies": 3,
            "audit_intact": True,
            "uptime_seconds": 3600.0,
        }
        with patch(
            "src.chronoscope.cli.ChronoScopeController",
            return_value=mock_controller,
        ):
            result = main(["status"])
        assert result == 0

    def test_audit_command_intact(self):
        mock_controller = MagicMock()
        mock_controller.get_audit_status.return_value = {
            "chain_intact": True,
            "entry_count": 42,
            "algorithm": "sha256",
        }
        with patch(
            "src.chronoscope.cli.ChronoScopeController",
            return_value=mock_controller,
        ):
            result = main(["audit"])
        assert result == 0

    def test_audit_command_broken_returns_one(self):
        mock_controller = MagicMock()
        mock_controller.get_audit_status.return_value = {
            "chain_intact": False,
            "entry_count": 10,
            "algorithm": "sha256",
        }
        with patch(
            "src.chronoscope.cli.ChronoScopeController",
            return_value=mock_controller,
        ):
            result = main(["audit"])
        assert result == 1

    def test_replay_command_runs(self):
        mock_controller = MagicMock()
        mock_controller.replay_summary.return_value = {
            "packet_count": 100,
            "duration_seconds": 3600.0,
            "start_time": "2024-01-15T00:00:00+00:00",
            "end_time": "2024-01-15T01:00:00+00:00",
            "anomaly_count": 2,
            "fingerprint": "abc123def456abc123def456abc123de",
        }
        with patch(
            "src.chronoscope.cli.ChronoScopeController",
            return_value=mock_controller,
        ):
            result = main(["replay", "session-abc"])
        assert result == 0

    def test_anomalies_command_no_anomalies(self):
        mock_controller = MagicMock()
        mock_controller.get_anomalies.return_value = []
        with patch(
            "src.chronoscope.cli.ChronoScopeController",
            return_value=mock_controller,
        ):
            result = main(["anomalies", "session-abc"])
        assert result == 0

    def test_anomalies_command_with_results(self):
        mock_controller = MagicMock()
        mock_controller.get_anomalies.return_value = [
            {
                "severity": "high",
                "parameter": "voltage",
                "observed_value": 15.9,
                "reason": "Exceeded upper limit",
                "timestamp": "2024-01-15T12:00:00+00:00",
                "suggested_actions": [
                    {"title": "Reduce load", "success_rate": 0.92}
                ],
            }
        ]
        with patch(
            "src.chronoscope.cli.ChronoScopeController",
            return_value=mock_controller,
        ):
            result = main(["anomalies", "session-abc"])
        assert result == 0

    def test_export_command_runs(self):
        mock_controller = MagicMock()
        mock_controller.export_session.return_value = {
            "session_id": "session-abc",
            "packet_count": 50,
            "packets": [],
        }
        with patch(
            "src.chronoscope.cli.ChronoScopeController",
            return_value=mock_controller,
        ), patch("builtins.open", MagicMock()):
            result = main(["export", "session-abc"])
        assert result == 0

    def test_replay_command_error_returns_one(self):
        mock_controller = MagicMock()
        mock_controller.replay_summary.side_effect = Exception("Session not found")
        with patch(
            "src.chronoscope.cli.ChronoScopeController",
            return_value=mock_controller,
        ):
            result = main(["replay", "bad-session-id"])
        assert result == 1