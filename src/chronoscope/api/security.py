"""
ChronoScope AI — API Security
API key authentication and rate limiting.
Simple but production-appropriate for initial deployment.
"""

from __future__ import annotations
from datetime import datetime, timezone
from collections import defaultdict
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
import structlog

logger = structlog.get_logger(__name__)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# In production this comes from environment variables or a secrets manager.
# For demo purposes we use a hardcoded set.
VALID_API_KEYS: set[str] = {
    "chronoscope-demo-key-2026",
    "chronoscope-dev-key-local",
}

# Rate limiting — requests per minute per API key
RATE_LIMIT_RPM = 60
_request_counts: dict[str, list[datetime]] = defaultdict(list)


def verify_api_key(api_key: str | None = Security(API_KEY_HEADER)) -> str:
    """
    Verify API key is present and valid.
    Returns the key if valid, raises 401 if not.
    """
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Provide X-API-Key header.",
        )

    if api_key not in VALID_API_KEYS:
        logger.warning("invalid_api_key_attempt", key_prefix=api_key[:8])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    # Rate limiting check
    now = datetime.now(timezone.utc)
    window_start = now.timestamp() - 60  # 1 minute window

    # Clean old requests
    _request_counts[api_key] = [
        ts for ts in _request_counts[api_key]
        if ts.timestamp() > window_start
    ]

    if len(_request_counts[api_key]) >= RATE_LIMIT_RPM:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_RPM} requests/minute.",
        )

    _request_counts[api_key].append(now)
    return api_key