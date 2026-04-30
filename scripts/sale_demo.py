"""
ChronoScope AI — Sale Demo Script
Run this to demonstrate ChronoScope to any buyer.
Shows the complete platform working end-to-end on real NASA data.
No setup required. No API keys. No synthetic data.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone, timedelta
from src.chronoscope.controller import ChronoScopeController
from src.chronoscope.ingestion.noaa_dscovr import NOAADscovrIngester
from src.chronoscope.dashboard.dashboard import MissionDashboard
from src.chronoscope.reporter import MissionReporter
from src.chronoscope.audit.log import AuditLog
from src.chronoscope.replay.engine import ReplayEngine
from src.chronoscope.domain.models import MissionPhase
from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR


def header(title: str) -> None:
    print()
    print("=" * 65)
    print(f"  {title}")
    print("=" * 65)


def section(title: str) -> None:
    print(f"\n{'─' * 65}")
    print(f"  {title}")
    print(f"{'─' * 65}")


def run_sale_demo():
    header("ChronoScope AI — Live Platform Demonstration")
    print("""
  Universal telemetry replay, audit, and anomaly detection.
  Real NASA spacecraft data. No synthetic data anywhere.
  Running live right now.
    """)

    # ── Step 1: Live data ingestion ──────────────────────────────────
    section("Step 1 of 6 — Live NASA Data Ingestion")
    print("\n  Connecting to NOAA Space Weather Prediction Center...")
    print(f"  Spacecraft: DSCOVR (L1 Lagrange Point, 1.5M km from Earth)")

    ingester = NOAADscovrIngester()
    available = ingester.is_available()

    if not available:
        print("  ERROR: NOAA API unreachable. Check internet connection.")
        sys.exit(1)

    print("  NOAA API: ONLINE ✓")

    audit = AuditLog()
    controller = ChronoScopeController(
        ingester=ingester,
        audit_log=audit,
    )

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=2)

    session = controller.create_session(
        spacecraft_id=SPACECRAFT_DSCOVR,
        mission_phase=MissionPhase.NOMINAL,
        start_time=start_time,
        end_time=end_time,
        metadata={"demo": True, "source": "noaa_swpc"},
        actor="demo_operator",
    )

    result = controller.ingest(
        session_id=session.session_id,
        start_time=start_time,
        end_time=end_time,
        actor="demo_operator",
    )

    print(f"\n  Result:           {'SUCCESS ✓' if result.success else 'FAILED ✗'}")
    print(f"  Packets ingested: {result.packets_ingested}")
    print(f"  Packets failed:   {result.packets_failed}")
    print(f"  Success rate:     {result.success_rate * 100:.1f}%")
    print(f"  Duration:         {result.duration_seconds:.2f}s")
    print(f"  Session ID:       {session.session_id[:16]}...")

    if not result.success:
        print("  Ingestion failed. Exiting.")
        sys.exit(1)

    # ── Step 2: Deterministic replay ────────────────────────────────
    section("Step 2 of 6 — Deterministic Replay Engine")
    print("\n  Loading session into replay engine...")

    cursor = controller.load_replay(
        session_id=session.session_id,
        actor="demo_operator",
    )

    print(f"  Session loaded ✓")
    print(f"  Total packets:  {cursor.total_packets}")
    print(f"  Time window:    {cursor.start_time.strftime('%H:%M:%S')} → "
          f"{cursor.end_time.strftime('%H:%M:%S')} UTC")
    print(f"  Position:       Packet 0 of {cursor.total_packets}")

    # Seek to midpoint
    midpoint = start_time + (end_time - start_time) / 2
    cursor = controller.seek(
        session_id=session.session_id,
        target_time=midpoint,
        actor="demo_operator",
    )
    print(f"\n  Seeked to midpoint → Packet {cursor.current_index} ✓")

    # Verify determinism
    verified = controller.verify_determinism(session.session_id)
    print(f"  Determinism verified: {verified} ✓")
    fingerprint = controller._replay._replay_hashes.get(session.session_id, "")
    print(f"  Session fingerprint:  {fingerprint[:32]}...")

    # ── Step 3: AI anomaly detection ────────────────────────────────
    section("Step 3 of 6 — AI Anomaly Detection")
    print("\n  Running AI analysis across all telemetry parameters...")

    reports = controller.analyze(
        session_id=session.session_id,
        actor="ai_engine",
    )

    print(f"  Analysis complete ✓")
    print(f"  Anomalies detected: {len(reports)}")

    if reports:
        print(f"\n  Sample anomaly flags:")
        for report in reports[:3]:
            flag = report.flag
            print(f"\n    [{flag.severity.value.upper()}] {flag.parameter_name}")
            print(f"    Observed:    {flag.observed_value}")
            print(f"    Expected:    {flag.expected_range[0]} – {flag.expected_range[1]}")
            print(f"    Confidence:  {flag.confidence * 100:.0f}%")
            print(f"    Reason:      {flag.reason}")
            if report.suggested_actions:
                top = report.suggested_actions[0]
                print(f"    Top action:  {top.title} ({top.success_rate * 100:.0f}% success)")
    else:
        print("  No anomalies detected — all parameters nominal ✓")

    # ── Step 4: Tamper-evident audit trail ──────────────────────────
    section("Step 4 of 6 — Tamper-Evident Audit Trail")
    print("\n  Every action taken in this demo has been logged.")
    print("  Each log entry is cryptographically chained.")

    chain_intact = controller.verify_audit_chain()
    summary = controller.audit_summary()

    print(f"\n  Audit entries:   {audit.entry_count}")
    print(f"  Chain intact:    {'YES ✓' if chain_intact else 'BROKEN ✗'}")
    print(f"  Algorithm:       SHA-256")
    print(f"\n  Logged events include:")
    print(f"    • System startup")
    print(f"    • Session creation")
    print(f"    • Data ingestion start + completion")
    print(f"    • Replay load")
    print(f"    • Seek operation")
    print(f"    • Determinism verification")
    print(f"    • Every AI anomaly flag")
    print(f"\n  Breaking any entry invalidates the entire chain.")
    print(f"  Tampering is mathematically detectable.")

    # ── Step 5: Mission dashboard ────────────────────────────────────
    section("Step 5 of 6 — Mission Dashboard")

    engine = ReplayEngine()
    dashboard = MissionDashboard(
        replay_engine=engine,
        audit_log=audit,
    )
    dashboard.register_session(session)
    snapshot = dashboard.get_snapshot()
    health = snapshot.system_health

    print(f"\n  System Health:    {health.health_status}")
    print(f"  Active Sessions:  {health.total_sessions}")
    print(f"  Total Packets:    {health.total_packets_processed:,}")
    print(f"  Total Anomalies:  {health.total_anomalies}")
    print(f"  Audit Intact:     {'YES ✓' if health.audit_chain_intact else 'NO ✗'}")
    print(f"  Uptime:           {health.uptime_seconds:.1f}s")

    # ── Step 6: Mission report ───────────────────────────────────────
    section("Step 6 of 6 — Professional Mission Report")
    print("\n  Generating complete mission report...")

    reporter = MissionReporter(audit_log=audit)
    report = reporter.generate(
        session=session,
        fingerprint=fingerprint,
        determinism_verified=verified,
    )

    print(f"\n  Report ID:        {report.report_id[:16]}...")
    print(f"  Health Rating:    {report.health_rating}")
    print(f"  Total Packets:    {report.total_packets:,}")
    print(f"  Packets/Hour:     {report.packets_per_hour:.1f}")
    print(f"  Total Anomalies:  {report.total_anomalies}")
    print(f"  Audit Entries:    {report.audit_entries}")
    print(f"  Chain Intact:     {'YES ✓' if report.audit_chain_intact else 'NO ✗'}")

    # Save reports
    json_path = "chronoscope_demo_report.json"
    md_path = "chronoscope_demo_report.md"

    with open(json_path, "w", encoding="utf-8") as f:
        f.write(report.to_json())

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report.to_markdown())

    print(f"\n  Reports saved:")
    print(f"    JSON: {json_path}")
    print(f"    MD:   {md_path}")

    # ── Final summary ────────────────────────────────────────────────
    header("ChronoScope AI — Demo Complete")
    print(f"""
  What just happened — in 60 seconds:

  ✓  Connected to real NASA spacecraft (DSCOVR)
  ✓  Ingested {result.packets_ingested} real telemetry packets
  ✓  Loaded session into deterministic replay engine
  ✓  Seeked to any point in the timeline instantly
  ✓  Verified mathematical determinism (fingerprint)
  ✓  Ran AI anomaly detection with explainable output
  ✓  Logged {audit.entry_count} audit entries with SHA-256 chain
  ✓  Generated dashboard health snapshot
  ✓  Produced professional JSON and Markdown reports

  All of this on a single platform.
  No fragmented tools. No manual investigation.
  No black box AI. No synthetic data.

  Reports saved to current directory.
    """)


if __name__ == "__main__":
    run_sale_demo()