# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — API Routes
All REST endpoints. Every endpoint is authenticated.
Every significant operation is audit-logged by the controller.
"""

from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
import structlog

from src.chronoscope.controller import ChronoScopeController
from src.chronoscope.domain.models import MissionPhase
from src.chronoscope.domain.exceptions import (
    SessionNotFoundError,
    ReplayStateError,
)
from src.chronoscope.api.schemas import (
    CreateSessionRequest,
    IngestRequest,
    SeekRequest,
    SetSpeedRequest,
    OperatorDecisionRequest,
    OutcomeRequest,
    SessionResponse,
    IngestionResponse,
    CursorResponse,
    AnomalyReportResponse,
    SuggestedActionResponse,
    AuditSummaryResponse,
    SystemStatusResponse,
    HealthResponse,
)
from src.chronoscope.api.security import verify_api_key

logger = structlog.get_logger(__name__)
router = APIRouter()

# Shared controller instance
_controller: ChronoScopeController | None = None


def get_controller() -> ChronoScopeController:
    global _controller
    if _controller is None:
        _controller = ChronoScopeController()
    return _controller


def cursor_to_response(cursor, session_id: str) -> CursorResponse:
    return CursorResponse(
        session_id=session_id,
        current_index=cursor.current_index,
        current_time=cursor.current_time,
        total_packets=cursor.total_packets,
        progress=cursor.progress,
        speed=cursor.speed,
        is_playing=cursor.is_playing,
        is_at_start=cursor.is_at_start,
        is_at_end=cursor.is_at_end,
    )


def session_to_response(session) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        spacecraft_id=session.spacecraft_id,
        mission_phase=session.mission_phase.value,
        packet_count=session.packet_count,
        anomaly_count=session.anomaly_count,
        replay_status=session.replay_status.value,
        start_time=session.start_time,
        end_time=session.end_time,
        metadata=session.metadata,
    )


def report_to_response(report) -> AnomalyReportResponse:
    actions = [
        SuggestedActionResponse(
            action_id=a.action_id,
            title=a.title,
            description=a.description,
            steps=a.steps,
            success_rate=a.success_rate,
            success_rate_pct=f"{a.success_rate * 100:.1f}%",
            time_required_minutes=a.time_required_minutes,
            risk_if_skipped=a.risk_if_skipped,
            priority=a.priority,
        )
        for a in report.suggested_actions
    ]
    return AnomalyReportResponse(
        flag_id=report.flag.flag_id,
        timestamp=report.flag.timestamp,
        spacecraft_id=report.flag.spacecraft_id,
        severity=report.flag.severity.value,
        parameter=report.flag.parameter_name,
        observed_value=report.flag.observed_value,
        expected_range=list(report.flag.expected_range),
        confidence=report.flag.confidence,
        what_happened=report.what_happened,
        why_it_matters=report.why_it_matters,
        urgency_hours=report.urgency_hours,
        similar_events_count=report.similar_events_count,
        suggested_actions=actions,
        recommended_action_id=report.recommended_action_id,
        operator_decision=report.operator_decision,
        operator_actor=report.operator_actor,
        outcome=report.outcome,
        outcome_success=report.outcome_success,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """System health check. No auth required."""
    cs = get_controller()
    available = cs._ingester.is_available()
    return HealthResponse(
        status="ok",
        version="1.0.0",
        data_source_available=available,
    )


@router.get(
    "/status",
    response_model=SystemStatusResponse,
    tags=["System"],
    dependencies=[Depends(verify_api_key)],
)
async def system_status():
    """Full system status. Auth required."""
    cs = get_controller()
    return SystemStatusResponse(**cs.status())


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Sessions"],
    dependencies=[Depends(verify_api_key)],
)
async def create_session(req: CreateSessionRequest):
    """Create a new mission session."""
    cs = get_controller()
    try:
        phase = MissionPhase(req.mission_phase)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mission_phase: {req.mission_phase}",
        )
    session = cs.create_session(
        spacecraft_id=req.spacecraft_id,
        mission_phase=phase,
        start_time=req.start_time,
        end_time=req.end_time,
        metadata=req.metadata,
        actor=req.actor,
    )
    return session_to_response(session)


@router.get(
    "/sessions",
    tags=["Sessions"],
    dependencies=[Depends(verify_api_key)],
)
async def list_sessions():
    """List all sessions."""
    cs = get_controller()
    return cs.list_sessions()


@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    tags=["Sessions"],
    dependencies=[Depends(verify_api_key)],
)
async def get_session(session_id: str):
    """Get session details."""
    cs = get_controller()
    try:
        session = cs.get_session(session_id)
        return session_to_response(session)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/{session_id}/ingest",
    response_model=IngestionResponse,
    tags=["Ingestion"],
    dependencies=[Depends(verify_api_key)],
)
async def ingest(session_id: str, req: IngestRequest):
    """Ingest telemetry data into a session."""
    cs = get_controller()
    try:
        result = cs.ingest(
            session_id=session_id,
            start_time=req.start_time,
            end_time=req.end_time,
            actor=req.actor,
        )
        return IngestionResponse(
            success=result.success,
            source=result.source,
            packets_ingested=result.packets_ingested,
            packets_failed=result.packets_failed,
            success_rate=result.success_rate,
            duration_seconds=result.duration_seconds,
            errors=result.errors,
            session_id=result.session_id,
        )
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/{session_id}/replay/load",
    response_model=CursorResponse,
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def load_replay(session_id: str, actor: str = "api_user"):
    cs = get_controller()
    try:
        cursor = cs.load_replay(session_id=session_id, actor=actor)
        return cursor_to_response(cursor, session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/sessions/{session_id}/replay/play",
    response_model=CursorResponse,
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def play(session_id: str, actor: str = "api_user"):
    cs = get_controller()
    try:
        cursor = cs.play(session_id, actor=actor)
        return cursor_to_response(cursor, session_id)
    except (SessionNotFoundError, ReplayStateError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/sessions/{session_id}/replay/pause",
    response_model=CursorResponse,
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def pause(session_id: str, actor: str = "api_user"):
    cs = get_controller()
    try:
        cursor = cs.pause(session_id, actor=actor)
        return cursor_to_response(cursor, session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/sessions/{session_id}/replay/seek",
    response_model=CursorResponse,
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def seek(session_id: str, req: SeekRequest):
    cs = get_controller()
    try:
        cursor = cs.seek(session_id, req.target_time, actor=req.actor)
        return cursor_to_response(cursor, session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/sessions/{session_id}/replay/step-forward",
    response_model=CursorResponse,
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def step_forward(session_id: str):
    cs = get_controller()
    try:
        cursor = cs.step_forward(session_id)
        return cursor_to_response(cursor, session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/sessions/{session_id}/replay/step-back",
    response_model=CursorResponse,
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def step_back(session_id: str):
    cs = get_controller()
    try:
        cursor = cs.step_backward(session_id)
        return cursor_to_response(cursor, session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/sessions/{session_id}/replay/speed",
    response_model=CursorResponse,
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def set_speed(session_id: str, req: SetSpeedRequest):
    cs = get_controller()
    try:
        cursor = cs.set_speed(session_id, req.speed, actor=req.actor)
        return cursor_to_response(cursor, session_id)
    except (SessionNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/sessions/{session_id}/replay/cursor",
    response_model=CursorResponse,
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def get_cursor(session_id: str):
    cs = get_controller()
    try:
        cursor = cs._replay.get_cursor(session_id)
        return cursor_to_response(cursor, session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/sessions/{session_id}/replay/verify",
    tags=["Replay"],
    dependencies=[Depends(verify_api_key)],
)
async def verify_determinism(session_id: str):
    cs = get_controller()
    try:
        result = cs.verify_determinism(session_id)
        return {"determinism_verified": result, "session_id": session_id}
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# AI Analysis
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/{session_id}/analyze",
    response_model=list[AnomalyReportResponse],
    tags=["AI"],
    dependencies=[Depends(verify_api_key)],
)
async def analyze(session_id: str, actor: str = "ai_engine"):
    cs = get_controller()
    try:
        reports = cs.analyze(session_id=session_id, actor=actor)
        return [report_to_response(r) for r in reports]
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/anomalies",
    response_model=list[AnomalyReportResponse],
    tags=["AI"],
    dependencies=[Depends(verify_api_key)],
)
async def list_anomalies(unacknowledged_only: bool = False):
    cs = get_controller()
    if unacknowledged_only:
        reports = cs._detector.get_unacknowledged_reports()
    else:
        reports = cs._detector.reports
    return [report_to_response(r) for r in reports]


@router.post(
    "/anomalies/decide",
    tags=["AI"],
    dependencies=[Depends(verify_api_key)],
)
async def operator_decision(req: OperatorDecisionRequest):
    cs = get_controller()
    report = cs.operator_decides(
        flag_id=req.flag_id,
        action_id=req.action_id,
        actor=req.actor,
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Anomaly flag not found")
    return {"recorded": True, "flag_id": req.flag_id, "actor": req.actor}


@router.post(
    "/anomalies/outcome",
    tags=["AI"],
    dependencies=[Depends(verify_api_key)],
)
async def record_outcome(req: OutcomeRequest):
    cs = get_controller()
    report = cs.record_outcome(
        flag_id=req.flag_id,
        success=req.success,
        description=req.description,
        actor=req.actor,
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Anomaly flag not found")
    return {
        "recorded": True,
        "flag_id": req.flag_id,
        "success": req.success,
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@router.get(
    "/audit/summary",
    response_model=AuditSummaryResponse,
    tags=["Audit"],
    dependencies=[Depends(verify_api_key)],
)
async def audit_summary():
    cs = get_controller()
    summary = cs.audit_summary()
    return AuditSummaryResponse(**summary)


@router.get(
    "/audit/export",
    tags=["Audit"],
    dependencies=[Depends(verify_api_key)],
)
async def export_audit():
    import json
    cs = get_controller()
    return json.loads(cs.export_audit())


@router.post(
    "/audit/verify",
    tags=["Audit"],
    dependencies=[Depends(verify_api_key)],
)
async def verify_audit():
    cs = get_controller()
    result = cs.verify_audit_chain()
    return {"chain_intact": result}