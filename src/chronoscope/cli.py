"""
ChronoScope AI — Command Line Interface
Run ChronoScope operations from the terminal.
Useful for scripting, automation, and quick inspection.
"""

from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def cmd_status(args: argparse.Namespace) -> int:
    """Show system status and health."""
    from src.chronoscope.controller import ChronoScopeController
    controller = ChronoScopeController()
    health = controller.get_health()
    print("\nChronoScope AI — System Status")
    print("=" * 40)
    print(f"  Status:           {health.get('status', 'UNKNOWN')}")
    print(f"  Sessions loaded:  {health.get('sessions_loaded', 0)}")
    print(f"  Total packets:    {health.get('total_packets', 0)}")
    print(f"  Total anomalies:  {health.get('total_anomalies', 0)}")
    print(f"  Audit intact:     {health.get('audit_intact', False)}")
    print(f"  Uptime:           {health.get('uptime_seconds', 0):.1f}s")
    print()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest telemetry data into a new session."""
    from src.chronoscope.controller import ChronoScopeController
    from src.chronoscope.ingestion.noaa_dscovr import NOAADscovrIngester

    controller = ChronoScopeController()
    ingester = NOAADscovrIngester()

    hours = getattr(args, "hours", 2)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=hours)

    spacecraft = getattr(args, "spacecraft", "DSCOVR")

    print(f"\nIngesting {hours}h of {spacecraft} telemetry...")
    print(f"  Source: NOAA SWPC")
    print(f"  Window: {start_time.strftime('%Y-%m-%d %H:%M')} → "
          f"{end_time.strftime('%H:%M')} UTC")

    result = controller.ingest(
        spacecraft_id=spacecraft,
        start_time=start_time,
        end_time=end_time,
        ingester=ingester,
    )

    if result["success"]:
        print(f"\n  ✓ {result['packets_ingested']} packets ingested")
        print(f"  ✓ Session: {result['session_id']}")
        print(f"  ✓ Duration: {result['duration_seconds']:.2f}s")
    else:
        print(f"\n  ✗ Ingestion failed")
        for err in result.get("errors", []):
            print(f"    - {err}")
        return 1

    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay a session and print packet summary."""
    from src.chronoscope.controller import ChronoScopeController

    session_id = args.session_id
    controller = ChronoScopeController()

    print(f"\nReplaying session: {session_id}")
    print("=" * 40)

    try:
        summary = controller.replay_summary(session_id)
        print(f"  Packets:    {summary['packet_count']}")
        print(f"  Duration:   {summary['duration_seconds']:.1f}s")
        print(f"  Start:      {summary['start_time']}")
        print(f"  End:        {summary['end_time']}")
        print(f"  Anomalies:  {summary['anomaly_count']}")
        print(f"  Fingerprint:{summary['fingerprint'][:16]}...")
    except Exception as e:
        print(f"  Error: {e}")
        return 1

    return 0


def cmd_anomalies(args: argparse.Namespace) -> int:
    """List anomalies for a session."""
    from src.chronoscope.controller import ChronoScopeController

    session_id = args.session_id
    controller = ChronoScopeController()

    try:
        anomalies = controller.get_anomalies(session_id)
        if not anomalies:
            print(f"\nNo anomalies found for session {session_id}")
            return 0

        print(f"\nAnomalies for session {session_id[:8]}...")
        print("=" * 60)
        for flag in anomalies:
            print(f"\n  [{flag['severity'].upper()}] {flag['parameter']}")
            print(f"  Observed: {flag['observed_value']}")
            print(f"  Reason:   {flag['reason']}")
            print(f"  Time:     {flag['timestamp']}")
            if flag.get("suggested_actions"):
                print(f"  Actions:")
                for action in flag["suggested_actions"][:2]:
                    rate = action.get("success_rate", 0) * 100
                    print(f"    → {action['title']} ({rate:.0f}% success)")
    except Exception as e:
        print(f"  Error: {e}")
        return 1

    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Verify and display audit log status."""
    from src.chronoscope.controller import ChronoScopeController

    controller = ChronoScopeController()

    print("\nChronoScope AI — Audit Log Verification")
    print("=" * 40)

    try:
        audit_status = controller.get_audit_status()
        intact = audit_status.get("chain_intact", False)
        entries = audit_status.get("entry_count", 0)
        print(f"  Chain intact:  {'✓ YES' if intact else '✗ BROKEN'}")
        print(f"  Total entries: {entries}")
        print(f"  Algorithm:     {audit_status.get('algorithm', 'sha256')}")
        if not intact:
            print("\n  WARNING: Audit chain integrity compromised.")
            print("  This may indicate tampering.")
            return 1
    except Exception as e:
        print(f"  Error: {e}")
        return 1

    print("\n  Audit log is tamper-evident and intact.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export session data to JSON."""
    from src.chronoscope.controller import ChronoScopeController

    session_id = args.session_id
    output_file = getattr(args, "output", f"chronoscope_export_{session_id[:8]}.json")
    controller = ChronoScopeController()

    print(f"\nExporting session {session_id[:8]}... to {output_file}")

    try:
        data = controller.export_session(session_id)
        with open(output_file, "w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"  ✓ Exported {data['packet_count']} packets")
        print(f"  ✓ Saved to {output_file}")
    except Exception as e:
        print(f"  Error: {e}")
        return 1

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chronoscope",
        description="ChronoScope AI — Mission telemetry replay and audit platform",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # status
    subparsers.add_parser("status", help="Show system status")

    # ingest
    ingest_parser = subparsers.add_parser("ingest", help="Ingest telemetry")
    ingest_parser.add_argument(
        "--spacecraft", default="DSCOVR", help="Spacecraft ID"
    )
    ingest_parser.add_argument(
        "--hours", type=float, default=2.0,
        help="Hours of data to ingest"
    )

    # replay
    replay_parser = subparsers.add_parser("replay", help="Replay a session")
    replay_parser.add_argument("session_id", help="Session ID to replay")

    # anomalies
    anom_parser = subparsers.add_parser("anomalies", help="List anomalies")
    anom_parser.add_argument("session_id", help="Session ID")

    # audit
    subparsers.add_parser("audit", help="Verify audit log")

    # export
    export_parser = subparsers.add_parser("export", help="Export session to JSON")
    export_parser.add_argument("session_id", help="Session ID")
    export_parser.add_argument("--output", help="Output file path")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "status": cmd_status,
        "ingest": cmd_ingest,
        "replay": cmd_replay,
        "anomalies": cmd_anomalies,
        "audit": cmd_audit,
        "export": cmd_export,
    }

    handler = commands.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())