"""
ChronoScope AI — Integration SDK
Public SDK for external systems to integrate with ChronoScope.

Usage:
    from src.chronoscope.sdk import ChronoScopeSDK

    sdk = ChronoScopeSDK(base_url="http://localhost:8000", api_key="your-key")
    sdk.on_anomaly(lambda alert: print(alert))
    sdk.start()
"""

from src.chronoscope.sdk.client import ChronoScopeSDK
from src.chronoscope.sdk.webhooks import WebhookManager
from src.chronoscope.sdk.models import (
    SDKAlert,
    SDKSession,
    SDKHealth,
    SDKConfig,
)

__all__ = [
    "ChronoScopeSDK",
    "WebhookManager",
    "SDKAlert",
    "SDKSession",
    "SDKHealth",
    "SDKConfig",
]

__version__ = "1.0.0"