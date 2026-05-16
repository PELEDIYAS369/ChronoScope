# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI SDK — Client
The main SDK entry point for external system integration.

Example usage:
    from src.chronoscope.sdk import ChronoScopeSDK

    sdk = ChronoScopeSDK(base_url="http://localhost:8000")

    # Register callbacks
    sdk.on_anomaly(lambda alert: send_to_slack(alert))
    sdk.on_critical(lambda alert: page_oncall(alert))

    # Ingest data
    session = sdk.ingest(spacecraft_id="DSCOVR", hours=2)

    # Get health
    health = sdk.health()
    print(health.status)

    # Stream alerts
    for alert in sdk.stream_alerts(session.session_id):
        print(alert.reason)
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
import uuid
import requests
import structlog

from src.chronoscope.sdk.models import (
    SDKAlert,
    SDKSession,
    SDKHealth,
    SDKConfig,
)
from src.chronoscope.sdk.webhooks import WebhookManager

logger = structlog.get_logger(__name__)


class ChronoScopeSDK:
    """
    ChronoScope AI Integration SDK.

    Provides a clean Python interface for external systems
    to integrate with ChronoScope AI.

    Supports:
    - Session management
    - Data ingestion
    - Anomaly alert callbacks
    - Webhook registration
    - Health monitoring
    - Audit verification
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._anomaly_callbacks: list[Callable[[SDKAlert], None]] = []
        self._critical_callbacks: list[Callable[[SDKAlert], None]] = []
        self._session_callbacks: list[Callable[[SDKSession], None]] = []
        self._webhook_manager = WebhookManager()
        self.logger = structlog.get_logger(__name__)

    # ------------------------------------------------------------------
    # Callback Registration
    # ------------------------------------------------------------------

    def on_anomaly(self, callback: Callable[[SDKAlert], None]) -> None:
        """Register a callback for any anomaly alert."""
        self._anomaly_callbacks.append(callback)

    def on_critical(self, callback: Callable[[SDKAlert], None]) -> None:
        """Register a callback for critical anomaly alerts only."""
        self._critical_callbacks.append(callback)

    def on_session(self, callback: Callable[[SDKSession], None]) -> None:
        """Register a callback when a new session is created."""
        self._session_callbacks.append(callback)

    def register_webhook(
        self,
        url: str,
        events: list[str] | None = None,
        secret: str = "",
    ) -> str:
        """
        Register a webhook URL to receive anomaly alerts.
        Returns webhook_id for management.

        events: list of event types to receive
                ["anomaly.critical", "anomaly.high", "anomaly.any",
                 "session.created", "audit.failed"]
        """
        webhook_id = self._webhook_manager.register(
            url=url,
            events=events or ["anomaly.any"],
            secret=secret,
        )
        self.logger.info("webhook_registered", url=url, webhook_id=webhook_id)
        return webhook_id

    def unregister_webhook(self, webhook_id: str) -> bool:
        """Remove a registered webhook."""
        return self._webhook_manager.unregister(webhook_id)

    def list_webhooks(self) -> list[dict[str, Any]]:
        """List all registered webhooks."""
        return self._webhook_manager.list_webhooks()

    # ------------------------------------------------------------------
    # Health & Status
    # ------------------------------------------------------------------

    def health(self) -> SDKHealth:
        """Get current system health."""
        try:
            data = self._get("/api/v1/health")
            return SDKHealth(
                status=data.get("status", "UNKNOWN"),
                sessions=data.get("sessions", 0),
                total_packets=data.get("total_packets", 0),
                total_anomalies=data.get("total_anomalies", 0),
                audit_intact=data.get("audit_intact", False),
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            self.logger.error("health_check_failed", error=str(e))
            return SDKHealth(
                status="UNREACHABLE",
                sessions=0,
                total_packets=0,
                total_anomalies=0,
                audit_intact=False,
                timestamp=datetime.now(timezone.utc),
            )

    def ping(self) -> bool:
        """Check if ChronoScope API is reachable."""
        try:
            r = requests.get(
                f"{self.base_url}/",
                timeout=5,
                headers=self._headers(),
            )
            return r.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[SDKSession]:
        """List all active sessions."""
        try:
            data = self._get("/api/v1/sessions")
            return [self._parse_session(s) for s in data]
        except Exception:
            return []

    def get_session(self, session_id: str) -> SDKSession | None:
        """Get a specific session by ID."""
        try:
            data = self._get(f"/api/v1/sessions/{session_id}")
            return self._parse_session(data)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Alert Streaming
    # ------------------------------------------------------------------

    def stream_alerts(
        self,
        session_id: str,
    ) -> Iterator[SDKAlert]:
        """
        Stream anomaly alerts for a session.
        Yields SDKAlert objects as they are available.
        Call this after running analysis.
        """
        try:
            data = self._get(f"/api/v1/sessions/{session_id}/anomalies")
            for item in data:
                alert = self._parse_alert(item, session_id)
                yield alert
                self._fire_callbacks(alert)
        except Exception as e:
            self.logger.error("stream_alerts_failed", error=str(e))

    def get_alerts(self, session_id: str) -> list[SDKAlert]:
        """Get all alerts for a session as a list."""
        return list(self.stream_alerts(session_id))

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def verify_audit(self) -> bool:
        """Verify the audit chain integrity."""
        try:
            data = self._get("/api/v1/audit")
            return data.get("chain_intact", False)
        except Exception:
            return False

    def export_audit(self) -> dict[str, Any]:
        """Export the full audit log."""
        try:
            return self._get("/api/v1/audit/export")
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _get(self, path: str) -> Any:
        r = requests.get(
            f"{self.base_url}{path}",
            timeout=self.timeout,
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict) -> Any:
        r = requests.post(
            f"{self.base_url}{path}",
            json=data,
            timeout=self.timeout,
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def _parse_session(self, data: dict) -> SDKSession:
        return SDKSession(
            session_id=data.get("session_id", ""),
            spacecraft_id=data.get("spacecraft_id", ""),
            mission_phase=data.get("mission_phase", ""),
            packet_count=data.get("packet_count", 0),
            anomaly_count=data.get("anomaly_count", 0),
            replay_status=data.get("replay_status", ""),
            start_time=datetime.now(timezone.utc),
        )

    def _parse_alert(self, data: dict, session_id: str) -> SDKAlert:
        return SDKAlert(
            alert_id=data.get("flag_id", str(uuid.uuid4())),
            timestamp=datetime.now(timezone.utc),
            severity=data.get("severity", "info"),
            spacecraft_id=data.get("spacecraft_id", ""),
            parameter=data.get("parameter", ""),
            observed_value=float(data.get("observed_value", 0)),
            expected_range=(0.0, 0.0),
            reason=data.get("reason", ""),
            confidence=float(data.get("confidence", 0)),
            urgency_hours=float(data.get("urgency_hours", 24)),
            suggested_actions=data.get("suggested_actions", []),
            session_id=session_id,
        )

    def _fire_callbacks(self, alert: SDKAlert) -> None:
        for cb in self._anomaly_callbacks:
            try:
                cb(alert)
            except Exception:
                pass
        if alert.is_critical:
            for cb in self._critical_callbacks:
                try:
                    cb(alert)
                except Exception:
                    pass
        self._webhook_manager.fire(alert)