# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — API Schemas
Pydantic models for all API request and response bodies.
These define the contract between ChronoScope and any client.
"""

from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    spacecraft_id: str = Field(..., min_length=1, max_length=64)
    mission_phase: str = Field(default="nominal")
    start_time: datetime
    end_time: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="api_user")


class IngestRequest(BaseModel):
    start_time: datetime
    end_time: datetime
    actor: str = Field(default="api_user")


class SeekRequest(BaseModel):
    target_time: datetime
    actor: str = Field(default="api_user")


class SetSpeedRequest(BaseModel):
    speed: float = Field(..., gt=0, le=100)
    actor: str = Field(default="api_user")


class OperatorDecisionRequest(BaseModel):
    flag_id: str
    action_id: str
    actor: str


class OutcomeRequest(BaseModel):
    flag_id: str
    success: bool
    description: str
    actor: str = Field(default="api_user")


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------

class SessionResponse(BaseModel):
    session_id: str
    spacecraft_id: str
    mission_phase: str
    packet_count: int
    anomaly_count: int
    replay_status: str
    start_time: datetime
    end_time: datetime | None
    metadata: dict[str, Any]


class IngestionResponse(BaseModel):
    success: bool
    source: str
    packets_ingested: int
    packets_failed: int
    success_rate: float
    duration_seconds: float
    errors: list[str]
    session_id: str | None


class CursorResponse(BaseModel):
    session_id: str
    current_index: int
    current_time: datetime
    total_packets: int
    progress: float
    speed: float
    is_playing: bool
    is_at_start: bool
    is_at_end: bool


class SuggestedActionResponse(BaseModel):
    action_id: str
    title: str
    description: str
    steps: list[str]
    success_rate: float
    success_rate_pct: str
    time_required_minutes: float
    risk_if_skipped: str
    priority: int


class AnomalyReportResponse(BaseModel):
    flag_id: str
    timestamp: datetime
    spacecraft_id: str
    severity: str
    parameter: str
    observed_value: float
    expected_range: list[float]
    confidence: float
    what_happened: str
    why_it_matters: str
    urgency_hours: float
    suggested_actions: list[SuggestedActionResponse]
    recommended_action_id: str
    operator_decision: str | None
    operator_actor: str | None
    outcome: str | None
    outcome_success: bool | None


class AuditSummaryResponse(BaseModel):
    log_id: str
    total_entries: int
    unique_actors: int
    event_breakdown: dict[str, int]
    latest_hash: str
    chain_intact: bool


class SystemStatusResponse(BaseModel):
    sessions: int
    audit_entries: int
    audit_chain_intact: bool
    ai_reports: int
    ai_unacknowledged: int
    ai_critical: int
    detector_rules: int
    ingester: str


class HealthResponse(BaseModel):
    status: str
    version: str
    data_source_available: bool