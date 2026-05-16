# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — SDK Integration Demo
Shows any buyer exactly how to integrate ChronoScope
into their existing system in under 20 lines of code.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from src.chronoscope.sdk import ChronoScopeSDK, SDKAlert


def on_anomaly_detected(alert: SDKAlert):
    """This is what a buyer writes. Everything else is ChronoScope."""
    print(f"\n  🚨 ANOMALY: [{alert.severity.upper()}] {alert.parameter}")
    print(f"     Observed:  {alert.observed_value}")
    print(f"     Reason:    {alert.reason}")
    print(f"     Confidence:{alert.confidence * 100:.0f}%")
    if alert.top_action:
        action = alert.top_action
        rate = action.get("success_rate", 0) * 100
        print(f"     Action:    {action.get('title')} ({rate:.0f}% success)")


def on_critical_alert(alert: SDKAlert):
    """Called only for critical severity — page the on-call team."""
    print(f"\n  🔴 CRITICAL ALERT — paging on-call: {alert.parameter}")
    print(f"     {alert.reason}")


def run_sdk_demo():
    print("\nChronoScope AI — SDK Integration Demo")
    print("=" * 50)
    print("This shows how any external system integrates.")
    print("A buyer writes ~5 lines. ChronoScope does the rest.")
    print()

    # ── Initialize SDK ───────────────────────────────────────────────
    sdk = ChronoScopeSDK(base_url="http://localhost:8000")

    # ── Check connectivity ───────────────────────────────────────────
    print("Step 1: Check connectivity")
    reachable = sdk.ping()
    print(f"  API reachable: {reachable}")

    if not reachable:
        print("\n  Start the API first:")
        print("  uvicorn src.chronoscope.api.app:app --port 8000")
        print("\n  Running in offline demo mode...")
        _offline_demo()
        return

    # ── Register callbacks ───────────────────────────────────────────
    print("\nStep 2: Register anomaly callbacks")
    sdk.on_anomaly(on_anomaly_detected)
    sdk.on_critical(on_critical_alert)
    print("  ✓ Anomaly callback registered")
    print("  ✓ Critical callback registered")

    # ── Register webhook ─────────────────────────────────────────────
    print("\nStep 3: Register webhook (example)")
    webhook_id = sdk.register_webhook(
        url="https://hooks.example.com/chronoscope",
        events=["anomaly.critical", "anomaly.high"],
        secret="your-webhook-secret",
    )
    print(f"  ✓ Webhook registered: {webhook_id}")
    print(f"  ✓ Active webhooks: {len(sdk.list_webhooks())}")

    # ── Health check ─────────────────────────────────────────────────
    print("\nStep 4: System health")
    health = sdk.health()
    print(f"  Status:        {health.status}")
    print(f"  Sessions:      {health.sessions}")
    print(f"  Total packets: {health.total_packets}")
    print(f"  Audit intact:  {health.audit_intact}")
    print(f"  Healthy:       {health.is_healthy}")

    # ── List sessions ────────────────────────────────────────────────
    print("\nStep 5: Active sessions")
    sessions = sdk.list_sessions()
    if sessions:
        for s in sessions[:3]:
            print(f"  {s.session_id[:8]}... | {s.spacecraft_id} | "
                  f"{s.packet_count} packets | {s.anomaly_count} anomalies")
    else:
        print("  No sessions — run sale_demo.py first")

    # ── Stream alerts ────────────────────────────────────────────────
    if sessions:
        print(f"\nStep 6: Streaming alerts from session")
        session = sessions[-1]
        alerts = sdk.get_alerts(session.session_id)
        if alerts:
            print(f"  {len(alerts)} alerts found — callbacks fired above")
        else:
            print("  No alerts in this session")

    # ── Verify audit ─────────────────────────────────────────────────
    print("\nStep 7: Verify audit chain")
    intact = sdk.verify_audit()
    print(f"  Chain intact: {'✓ YES' if intact else '✗ BROKEN'}")

    print("\n" + "=" * 50)
    print("Integration complete.")
    print("A buyer writes the callbacks. ChronoScope does everything else.")
    print("=" * 50)


def _offline_demo():
    """Show SDK interface without a live server."""
    print("\nOffline SDK Demo — interface preview:")
    print()
    print("  from src.chronoscope.sdk import ChronoScopeSDK")
    print()
    print("  sdk = ChronoScopeSDK(base_url='http://your-server:8000')")
    print("  sdk.on_anomaly(lambda alert: your_system.alert(alert))")
    print("  sdk.on_critical(lambda alert: page_oncall(alert))")
    print("  sdk.register_webhook('https://your-system.com/hook')")
    print()
    print("  health = sdk.health()")
    print("  sessions = sdk.list_sessions()")
    print("  alerts = sdk.get_alerts(session_id)")
    print("  intact = sdk.verify_audit()")
    print()
    print("  That is the entire integration.")
    print("  5 lines to connect any system to ChronoScope.")


if __name__ == "__main__":
    run_sdk_demo()