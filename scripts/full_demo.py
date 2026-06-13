"""
ChronoScope AI — Full End-to-End Demo
Demonstrates the complete system pipeline:

  Live DSCOVR data
       ↓
  Mission Session
       ↓
  Deterministic Replay Engine
       ↓
  AI Anomaly Detection
       ↓
  Suggested Actions + Success Rates
       ↓
  Operator Decision Simulation
       ↓
  Tamper-Evident Audit Trail
       ↓
  Chain Verification
       ↓
  Full Audit Export

This is the demo you show to a buyer or SBIR reviewer.
Every step uses real NASA spacecraft data.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone, timedelta
from src.chronoscope.controller import ChronoScopeController
from src.chronoscope.domain.models import MissionPhase
from src.chronoscope.domain.constants import SPACECRAFT_DSCOVR
from src.chronoscope.ai.detector import AnomalyReport


def sep(title: str = "") -> None:
    if title:
        print(f"\n{'=' * 60}")
        print(f"  {title}")
        print(f"{'=' * 60}")
    else:
        print("-" * 60)


def run():
    sep("ChronoScope AI — Full System Demo")
    print("  Real NASA DSCOVR spacecraft data")
    print("  Complete pipeline: Ingest → Replay → AI → Audit")
    print("  No synthetic data. No placeholders.")

    # ----------------------------------------------------------------
    # Step 1 — Initialize the system
    # ----------------------------------------------------------------
    sep("Step 1: System Initialization")
    cs = ChronoScopeController()
    status = cs.status()
    print(f"  Ingester:       {status['ingester']}")
    print(f"  Detection rules:{status['detector_rules']}")
    print(f"  Audit entries:  {status['audit_entries']}")
    print(f"  Chain intact:   {status['audit_chain_intact']}")

    # ----------------------------------------------------------------
    # Step 2 — Create mission session
    # ----------------------------------------------------------------
    sep("Step 2: Create Mission Session")
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=6)

    session = cs.create_session(
        spacecraft_id=SPACECRAFT_DSCOVR,
        mission_phase=MissionPhase.NOMINAL,
        start_time=start_time,
        end_time=end_time,
        metadata={"demo": True, "purpose": "full_system_demo"},
        actor="demo_operator",
    )

    print(f"  Session ID:     {session.session_id[:18]}...")
    print(f"  Spacecraft:     {session.spacecraft_id}")
    print(f"  Phase:          {session.mission_phase.value}")
    print(f"  Window:         6 hours of live solar wind data")

    # ----------------------------------------------------------------
    # Step 3 — Ingest real data
    # ----------------------------------------------------------------
    sep("Step 3: Ingest Real DSCOVR Telemetry")
    print("  Connecting to NOAA Space Weather Prediction Center...")

    result = cs.ingest(
        session_id=session.session_id,
        start_time=start_time,
        end_time=end_time,
        actor="demo_operator",
    )

    print(f"  Status:         {'SUCCESS' if result.success else 'FAILED'}")
    print(f"  Packets:        {result.packets_ingested}")
    print(f"  Failed:         {result.packets_failed}")
    print(f"  Success rate:   {result.success_rate * 100:.1f}%")
    print(f"  Duration:       {result.duration_seconds:.2f}s")

    if not result.success:
        print("  ERROR: No data ingested. Check internet connection.")
        sys.exit(1)

    # ----------------------------------------------------------------
    # Step 4 — Load replay engine
    # ----------------------------------------------------------------
    sep("Step 4: Load Replay Engine")
    cursor = cs.load_replay(
        session_id=session.session_id,
        actor="demo_operator",
    )

    print(f"  Packets loaded: {cursor.total_packets}")
    print(f"  Start:          {cursor.start_time.strftime('%H:%M:%S UTC')}")
    print(f"  End:            {cursor.end_time.strftime('%H:%M:%S UTC')}")
    print(f"  Status:         {session.replay_status.value}")

    # ----------------------------------------------------------------
    # Step 5 — Replay operations
    # ----------------------------------------------------------------
    sep("Step 5: Replay Operations")

    # Play
    cursor = cs.play(session.session_id, actor="demo_operator")
    print(f"  PLAY  — Status: playing, speed: {cursor.speed}x")

    # Pause
    cursor = cs.pause(session.session_id, actor="demo_operator")
    print(f"  PAUSE — Index: {cursor.current_index}, "
          f"progress: {cursor.progress * 100:.1f}%")

    # Step forward 5 packets
    for _ in range(5):
        cursor = cs.step_forward(session.session_id)
    print(f"  STEP  — Advanced to index {cursor.current_index}")

    # Seek to midpoint
    midpoint = start_time + (end_time - start_time) / 2
    cursor = cs.seek(session.session_id, midpoint, actor="demo_operator")
    print(f"  SEEK  — Jumped to index {cursor.current_index}, "
          f"time {cursor.current_time.strftime('%H:%M:%S UTC')}")

    # Set speed
    cursor = cs.set_speed(session.session_id, 10.0, actor="demo_operator")
    print(f"  SPEED — Set to {cursor.speed}x realtime")

    # Verify determinism
    det_result = cs.verify_determinism(session.session_id)
    print(f"  DETERMINISM — Verified: {det_result}")

    # ----------------------------------------------------------------
    # Step 6 — Sample packets
    # ----------------------------------------------------------------
    sep("Step 6: Sample Telemetry at Current Position")
    current = cs._replay.get_current_packet(session.session_id)
    print(f"  Packet ID:    {current.packet_id[:18]}...")
    print(f"  Timestamp:    {current.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Spacecraft:   {current.spacecraft_id}")
    print(f"  APID:         0x{current.apid:02X}")
    print(f"  Parameters:")
    for key, val in current.parameters.items():
        if isinstance(val, float):
            print(f"    {key}: {val:.3f}")
        else:
            print(f"    {key}: {val}")

    # ----------------------------------------------------------------
    # Step 7 — AI anomaly detection
    # ----------------------------------------------------------------
    sep("Step 7: AI Anomaly Detection")
    print(f"  Analyzing {session.packet_count} packets "
          f"against {cs._detector.rule_count} rules...")

    reports = cs.analyze(
        session_id=session.session_id,
        actor="ai_engine",
    )

    print(f"\n  Analysis complete.")
    print(f"  Anomalies detected: {len(reports)}")
    print(f"  Session anomaly count: {session.anomaly_count}")

    if reports:
        # Show the most severe report
        critical = [r for r in reports
                    if r.flag.severity.value == "critical"]
        high = [r for r in reports
                if r.flag.severity.value == "high"]
        show = (critical or high or reports)[0]

        print(f"\n  Most significant anomaly:")
        print(f"  {'─' * 50}")
        print(f"  Parameter:  {show.flag.parameter_name}")
        print(f"  Observed:   {show.flag.observed_value:.3f}")
        print(f"  Severity:   {show.flag.severity.value.upper()}")
        print(f"  Confidence: {show.flag.confidence * 100:.1f}%")
        print(f"  Urgency:    {show.urgency_hours:.1f} hours")
        print(f"\n  What happened:")
        for line in show.what_happened.split(". "):
            if line.strip():
                print(f"    {line.strip()}.")
        print(f"\n  Suggested actions:")
        for action in sorted(show.suggested_actions,
                             key=lambda a: a.priority):
            rec = " ← RECOMMENDED" if (
                action.action_id == show.recommended_action_id
            ) else ""
            print(f"    {action.priority}. {action.title}{rec}")
            print(f"       Est. success rate: ~{action.success_rate * 100:.0f}%")
            print(f"       Time needed:  {action.time_required_minutes:.0f} min")

    else:
        print("  All parameters nominal — no anomalies detected.")
        print("  (Try a larger time window if solar wind is quiet today)")

    # ----------------------------------------------------------------
    # Step 8 — Simulate operator decision
    # ----------------------------------------------------------------
    sep("Step 8: Operator Decision + Outcome")
    if reports:
        report = reports[0]
        recommended = report.recommended_action

        print(f"  Anomaly:      {report.flag.parameter_name}")
        print(f"  AI suggests:  {recommended.title if recommended else 'N/A'}")
        rate_txt = (f"~{recommended.success_rate * 100:.0f}% (est.)"
                    if recommended else "N/A")
        print(f"  Est. success rate: {rate_txt}")
        print(f"\n  [SIMULATED] Operator accepts recommendation...")

        cs.operator_decides(
            flag_id=report.flag.flag_id,
            action_id=report.recommended_action_id,
            actor="flight_controller_demo",
            session_id=session.session_id,
        )

        cs.record_outcome(
            flag_id=report.flag.flag_id,
            success=True,
            description="Recommended action executed. System nominal.",
            actor="flight_controller_demo",
            session_id=session.session_id,
        )

        updated = cs._detector.reports[0]
        print(f"  Decision recorded: {updated.operator_decision[:18]}...")
        print(f"  Actor:             {updated.operator_actor}")
        print(f"  Outcome:           {updated.outcome}")
        print(f"  Success:           {updated.outcome_success}")
    else:
        print("  No anomalies — operator decision step skipped.")

    # ----------------------------------------------------------------
    # Step 9 — Audit chain verification
    # ----------------------------------------------------------------
    sep("Step 9: Audit Chain Verification")
    chain_ok = cs.verify_audit_chain()
    summary = cs.audit_summary()

    print(f"  Chain intact:    {chain_ok}")
    print(f"  Total entries:   {summary['total_entries']}")
    print(f"  Unique actors:   {summary['unique_actors']}")
    print(f"\n  Event breakdown:")
    for event_type, count in sorted(
        summary["event_breakdown"].items()
    ):
        print(f"    {event_type:<35} {count}")

    # ----------------------------------------------------------------
    # Step 10 — Export audit log
    # ----------------------------------------------------------------
    sep("Step 10: Audit Log Export")
    audit_json = cs.export_audit()
    import json
    parsed = json.loads(audit_json)
    print(f"  Log ID:         {parsed['log_id'][:18]}...")
    print(f"  Version:        {parsed['version']}")
    print(f"  Entries:        {parsed['entry_count']}")
    print(f"  Chain intact:   {parsed['chain_intact']}")
    print(f"  Latest hash:    {parsed['latest_hash'][:24]}...")

    # Save to file
    output_path = "evidence/audit_demo.json"
    os.makedirs("evidence", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(audit_json)
    print(f"\n  Audit saved to: {output_path}")

    # ----------------------------------------------------------------
    # Final summary
    # ----------------------------------------------------------------
    sep("Demo Complete — Final Summary")
    final_status = cs.status()
    print(f"  Sessions:            {final_status['sessions']}")
    print(f"  Packets processed:   {session.packet_count}")
    print(f"  Anomalies detected:  {session.anomaly_count}")
    print(f"  Audit entries:       {final_status['audit_entries']}")
    print(f"  Chain intact:        {final_status['audit_chain_intact']}")
    print(f"  AI reports:          {final_status['ai_reports']}")
    print(f"  Unacknowledged:      {final_status['ai_unacknowledged']}")
    print()
    print("  ChronoScope AI is working.")
    print("  Real data. Real replay. Real audit. Real AI.")
    print("=" * 60)


if __name__ == "__main__":
    run()