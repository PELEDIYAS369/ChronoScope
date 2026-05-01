"""
ChronoScope AI SDK — Webhook Manager
Delivers anomaly alerts to registered external URLs.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import hashlib
import hmac
import json
import uuid
import threading
import requests
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class WebhookRegistration:
    webhook_id: str
    url: str
    events: list[str]
    secret: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    delivery_count: int = 0
    failure_count: int = 0


class WebhookManager:
    """
    Manages webhook registrations and delivers alerts.
    Delivers asynchronously so it never blocks the main thread.
    Signs payloads with HMAC-SHA256 if secret is provided.
    """

    def __init__(self):
        self._webhooks: dict[str, WebhookRegistration] = {}

    def register(
        self,
        url: str,
        events: list[str],
        secret: str = "",
    ) -> str:
        webhook_id = str(uuid.uuid4())[:8]
        self._webhooks[webhook_id] = WebhookRegistration(
            webhook_id=webhook_id,
            url=url,
            events=events,
            secret=secret,
        )
        return webhook_id

    def unregister(self, webhook_id: str) -> bool:
        if webhook_id in self._webhooks:
            del self._webhooks[webhook_id]
            return True
        return False

    def list_webhooks(self) -> list[dict[str, Any]]:
        return [
            {
                "webhook_id": w.webhook_id,
                "url": w.url,
                "events": w.events,
                "delivery_count": w.delivery_count,
                "failure_count": w.failure_count,
                "created_at": w.created_at.isoformat(),
            }
            for w in self._webhooks.values()
        ]

    def fire(self, alert: Any) -> None:
        """Fire webhooks for an alert asynchronously."""
        for webhook in self._webhooks.values():
            if self._should_fire(webhook, alert):
                t = threading.Thread(
                    target=self._deliver,
                    args=(webhook, alert),
                    daemon=True,
                )
                t.start()

    def _should_fire(self, webhook: WebhookRegistration, alert: Any) -> bool:
        events = webhook.events
        if "anomaly.any" in events:
            return True
        severity = getattr(alert, "severity", "")
        if f"anomaly.{severity}" in events:
            return True
        return False

    def _deliver(self, webhook: WebhookRegistration, alert: Any) -> None:
        try:
            payload = alert.to_dict() if hasattr(alert, "to_dict") else {}
            body = json.dumps(payload)
            headers = {
                "Content-Type": "application/json",
                "X-ChronoScope-Event": f"anomaly.{payload.get('severity', 'unknown')}",
                "X-ChronoScope-Delivery": str(uuid.uuid4()),
            }
            if webhook.secret:
                sig = hmac.new(
                    webhook.secret.encode(),
                    body.encode(),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-ChronoScope-Signature"] = f"sha256={sig}"

            r = requests.post(
                webhook.url,
                data=body,
                headers=headers,
                timeout=10,
            )
            webhook.delivery_count += 1
            logger.info(
                "webhook_delivered",
                webhook_id=webhook.webhook_id,
                status=r.status_code,
            )
        except Exception as e:
            webhook.failure_count += 1
            logger.warning(
                "webhook_delivery_failed",
                webhook_id=webhook.webhook_id,
                error=str(e),
            )