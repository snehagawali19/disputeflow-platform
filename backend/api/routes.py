"""FastAPI routes for DisputeFlow."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import authenticate_websocket, require_api_key
from backend.api.schemas import CreateDisputeRequest, HumanDecisionRequest, OutcomeRequest
from backend.core.config import get_settings
from backend.db.models import AuditEntry, CaseRecord
from backend.db.session import SessionLocal, get_session, init_db
from backend.agents.evidence_factory import evidence_from_artifacts
from backend.models.dispute import CaseSource, DisputeCase, DisputePhase, DisputeReasonCode, TransactionData
from backend.models.ml_predictor import get_model_status
from backend.repositories.disputes import DisputeRepository
from backend.services.github_client import GitHubAPIError, GitHubAuthError
from backend.services.github_normalize import GitHubImportError
from backend.services.github_source import GitHubDisputeSource, push_touches_disputes, verify_github_signature
from backend.services.workflow import InvalidTransition, WorkflowService
from backend.utils.audit import AuditTrail


settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = get_settings()
    if cfg.app_env != "test":
        cfg.require_llm_credentials()
        if cfg.llm_credentials_configured:
            logger.info(f"LLM ready: provider={cfg.llm_provider} model={cfg.llm_model}")
        else:
            logger.warning(
                "LLM not configured — set OPENROUTER_API_KEY in .env. "
                "API started in development mode; agent pipeline requires a real key."
            )
    await init_db()
    logger.info("DisputeFlow API ready")
    yield


app = FastAPI(
    title="DisputeFlow API",
    description="Multi-agent payment dispute resolution engine",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

websocket_connections: dict[str, list[WebSocket]] = {}


async def broadcast_progress(session_id: str, message: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for ws in websocket_connections.get(session_id, []):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        websocket_connections[session_id].remove(ws)


async def run_pipeline_in_background(session_id: str) -> None:
    """Run the workflow after the import response so the UI can stream real progress."""
    async with SessionLocal() as background_session:
        try:
            await WorkflowService(background_session, broadcast_progress).start(session_id)
        except Exception as exc:
            logger.exception(f"Background pipeline failed for {session_id}: {exc}")


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "DisputeFlow API",
        "version": "1.0.0",
        "description": "Multi-agent payment dispute resolution engine",
        "endpoints": {
            "POST /disputes": "Create a new dispute case",
            "GET /disputes": "List all dispute cases",
            "GET /disputes/{id}": "Get dispute case details",
            "POST /disputes/{id}/start": "Start the agent pipeline",
            "POST /disputes/{id}/decide": "Submit human decision",
            "POST /disputes/{id}/outcome": "Record final outcome",
            "GET /disputes/{id}/trace": "Get full agent trace",
            "POST /github/sync": "Import disputes from the configured GitHub repository",
            "POST /github/fetch-one-and-start": "Import one unprocessed GitHub case and run the pipeline",
            "GET /github/status": "GitHub source synchronization status",
            "GET /model/status": "Strategy-model availability and evaluation metadata",
            "POST /webhooks/github": "GitHub push webhook",
        },
    }


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict[str, Any]:
    ready = True
    details: dict[str, Any] = {"database": "ok"}
    try:
        async with SessionLocal() as session:
            await session.execute(select(CaseRecord).limit(1))
    except Exception as exc:
        ready = False
        details["database"] = str(exc)
    model_path = settings.model_path
    if os.path.exists(model_path) or settings.allow_heuristic_model:
        details["model"] = "ok" if os.path.exists(model_path) else "heuristic_allowed"
    else:
        ready = False
        details["model"] = "missing"
    if settings.app_env != "test":
        try:
            settings.require_llm_credentials()
            details["llm"] = settings.llm_provider
        except Exception as exc:
            ready = False
            details["llm"] = str(exc)
    return {"status": "ok" if ready else "degraded", "details": details}


@app.post("/disputes")
async def create_dispute(
    req: CreateDisputeRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    if not settings.allow_local_dispute_create:
        raise HTTPException(
            status_code=403,
            detail="Local dispute creation is disabled. Use POST /github/sync with DISPUTE_SOURCE=github.",
        )
    try:
        currency = req.normalized_currency()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo = DisputeRepository(session)
    if req.idempotency_key:
        existing = await session.get(CaseRecord, req.idempotency_key)
        if existing:
            case = repo.to_case(existing)
            return {
                "session_id": existing.session_id,
                "case_id": case.case_id,
                "status": "created",
                "idempotent": True,
                "message": "Existing dispute returned for idempotency key.",
            }

    session_id = req.idempotency_key or str(uuid.uuid4())
    transaction = TransactionData(
        amount=req.transaction_amount or req.dispute_amount,
        currency=currency,
        payment_method=req.payment_method,
        merchant_id=req.merchant_id,
        merchant_name=req.merchant_name,
        customer_id=req.customer_id,
        customer_email=req.customer_email,
        avs_match=req.avs_match,
        cvv_verified=req.cvv_verified,
        three_ds_authenticated=req.three_ds_authenticated,
        ip_address=req.ip_address,
        billing_address=req.billing_address,
        shipping_address=req.shipping_address,
        device_fingerprint=req.device_fingerprint,
    )
    case = DisputeCase(
        dispute_amount=req.dispute_amount,
        dispute_currency=currency,
        reason_code=DisputeReasonCode(req.reason_code),
        dispute_phase=DisputePhase(req.dispute_phase),
        transaction=transaction,
        evidence_items=evidence_from_artifacts([item.model_dump() for item in req.artifacts]),
        source=CaseSource(source_type="api"),
    )
    await repo.create(session_id, case)
    audit = AuditTrail(session, case.case_id, session_id)
    await audit.add_entry("api", "create", {"amount": req.dispute_amount, "reason": req.reason_code})
    await session.commit()
    return {
        "session_id": session_id,
        "case_id": case.case_id,
        "status": "created",
        "message": "Dispute case created. Call POST /disputes/{session_id}/start to begin processing.",
    }


@app.get("/disputes")
async def list_disputes(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> list[dict[str, Any]]:
    repo = DisputeRepository(session)
    rows = await repo.list_all()
    result = []
    for row in rows:
        case = repo.to_case(row)
        source = case.source
        result.append(
            {
                "session_id": row.session_id,
                "case_id": case.case_id,
                "status": case.status.value,
                "amount": case.dispute_amount,
                "currency": case.dispute_currency,
                "reason_code": case.reason_code.value,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "pipeline_status": row.pipeline_status,
                "source_type": source.source_type if source else "api",
                "source_repository": source.source_repository if source else "",
                "needs_human_review": case.status.value == "pending_human_review"
                or row.pipeline_status == "awaiting_human_review",
            }
        )
    return result


@app.get("/disputes/{session_id}")
async def get_dispute(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    repo = DisputeRepository(session)
    record = await repo.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    case = repo.to_case(record)
    source = case.source
    return {
        "session_id": session_id,
        "case": json.loads(case.model_dump_json()),
        "status": record.pipeline_status,
        "version": record.version,
        "source": source.model_dump() if source else {"source_type": "api"},
    }


@app.post("/disputes/{session_id}/start")
async def start_pipeline(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    service = WorkflowService(session, broadcast_progress)
    try:
        result = await service.start(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "session_id": session_id,
        "status": result["status"],
        "message": "Processing started. Connect to WebSocket for real-time updates.",
        "case_status": result["case"].status.value,
    }


@app.post("/disputes/{session_id}/decide")
async def submit_human_decision(
    session_id: str,
    req: HumanDecisionRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    service = WorkflowService(session, broadcast_progress)
    try:
        result = await service.decide(session_id, req.decision, req.feedback)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": result["status"], "decision": req.decision, "case_status": result["case"].status.value}


@app.post("/disputes/{session_id}/outcome")
async def record_outcome(
    session_id: str,
    req: OutcomeRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    service = WorkflowService(session, broadcast_progress)
    try:
        case = await service.record_outcome(session_id, req.outcome, req.feedback)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "outcome_recorded", "outcome": req.outcome, "case_status": case.status.value}


@app.get("/disputes/{session_id}/trace")
async def get_trace(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    repo = DisputeRepository(session)
    record = await repo.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    case = repo.to_case(record)
    audit_rows = (
        await session.scalars(
            select(AuditEntry).where(AuditEntry.session_id == session_id).order_by(AuditEntry.sequence.asc())
        )
    ).all()
    return {
        "session_id": session_id,
        "agent_trace": case.agent_trace,
        "error_log": case.error_log,
        "human_approvals": case.human_approvals,
        "audit": [
            {
                "sequence": row.sequence,
                "agent": row.agent,
                "action": row.action,
                "hash": row.entry_hash,
                "previous_hash": row.previous_hash,
                "data": json.loads(row.data_json) if row.data_json else {},
            }
            for row in audit_rows
        ],
        "audit_valid": await AuditTrail(session, case.case_id, session_id).verify_chain(),
        "source": case.source.model_dump() if case.source else {"source_type": "api"},
    }


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    after: int = Query(default=0),
) -> None:
    await authenticate_websocket(websocket)
    async with SessionLocal() as session:
        repo = DisputeRepository(session)
        if await repo.get(session_id) is None:
            await websocket.send_json({"event": "error", "error": "Session not found"})
            await websocket.close(code=4404)
            return
        history = await repo.list_events(session_id, after=after)
        await websocket.send_json({"event": "snapshot", "events": history})

    websocket_connections.setdefault(session_id, []).append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if session_id in websocket_connections and websocket in websocket_connections[session_id]:
            websocket_connections[session_id].remove(websocket)


@app.get("/github/status")
async def github_status(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    source = GitHubDisputeSource(session)
    return await source.status()


@app.get("/model/status")
async def model_status(_: str = Depends(require_api_key)) -> dict[str, Any]:
    return get_model_status()


@app.post("/github/sync")
async def github_sync(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    source = GitHubDisputeSource(session)
    try:
        result = await source.sync()
        await session.commit()
        return result
    except GitHubAuthError as exc:
        raise HTTPException(status_code=502, detail="GitHub authentication failed") from exc
    except GitHubAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/github/fetch-one-and-start")
async def github_fetch_one_and_start(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_api_key),
) -> dict[str, Any]:
    source = GitHubDisputeSource(session)
    try:
        selected = await source.fetch_one()
        await session.commit()
        asyncio.create_task(run_pipeline_in_background(selected["session_id"]))
        return {
            **selected,
            "status": "running",
            "message": "One GitHub case was imported. Analysis is now running in the background.",
        }
    except GitHubAuthError as exc:
        raise HTTPException(status_code=502, detail="GitHub authentication failed") from exc
    except (GitHubAPIError, GitHubImportError) as exc:
        raise HTTPException(status_code=502 if isinstance(exc, GitHubAPIError) else 422, detail=str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
) -> dict[str, Any]:
    secret = settings.github_webhook_secret
    if not secret:
        return {
            "accepted": False,
            "reason": "webhook_secret_not_configured",
            "message": "Set GITHUB_WEBHOOK_SECRET and use POST /github/sync until webhooks are enabled.",
        }
    body = await request.body()
    if not verify_github_signature(secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if (x_github_event or "") != "push":
        return {"accepted": False, "reason": "ignored_event"}
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
    delivery_id = x_github_delivery or hashlib_sha(body)
    source = GitHubDisputeSource(session)
    first = await source.record_delivery(delivery_id, "push", str((payload.get("after") or "")[:64]))
    if not first:
        state = await source.status()
        return {
            "accepted": True,
            "duplicate": True,
            "imported": 0,
            "updated": 0,
            "unchanged": state.get("last_unchanged", 0),
            "rejected": 0,
            "commit_sha": state.get("last_commit_sha") or payload.get("after"),
        }
    if not push_touches_disputes(payload):
        await session.commit()
        return {"accepted": True, "ignored": True, "reason": "no_dispute_paths"}
    commit_sha = str(payload.get("after") or "")
    try:
        result = await source.sync(commit_sha=commit_sha or None)
        await session.commit()
        result["accepted"] = True
        result["duplicate"] = False
        return result
    except (GitHubAuthError, GitHubAPIError) as exc:
        await session.commit()
        return {"accepted": True, "error": "github_sync_failed", "detail": str(exc)}


def hashlib_sha(body: bytes) -> str:
    import hashlib

    return hashlib.sha256(body).hexdigest()

