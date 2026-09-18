"""Validate GitHub dispute JSON and map it onto DisputeCase without inventing evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.agents.evidence_factory import evidence_from_artifacts, evidence_from_supplied_facts
from backend.models.dispute import (
    CaseSource,
    DisputeCase,
    DisputePhase,
    DisputeReasonCode,
    TransactionData,
)

REASON_MAP = {
    "10.4": DisputeReasonCode.FRAUDULENT,
    "4837": DisputeReasonCode.FRAUDULENT,
    "13.1": DisputeReasonCode.PRODUCT_NOT_RECEIVED,
    "4855": DisputeReasonCode.PRODUCT_NOT_RECEIVED,
    "13.3": DisputeReasonCode.PRODUCT_UNACCEPTABLE,
    "4853": DisputeReasonCode.PRODUCT_UNACCEPTABLE,
    "13.6": DisputeReasonCode.CREDIT_NOT_PROCESSED,
    "4860": DisputeReasonCode.CREDIT_NOT_PROCESSED,
    "12.6.1": DisputeReasonCode.DUPLICATE,
    "duplicate": DisputeReasonCode.DUPLICATE,
    "13.2": DisputeReasonCode.SUBSCRIPTION_CANCELED,
    "4841": DisputeReasonCode.SUBSCRIPTION_CANCELED,
    "subscription_canceled": DisputeReasonCode.SUBSCRIPTION_CANCELED,
    "credit_not_processed": DisputeReasonCode.CREDIT_NOT_PROCESSED,
}

ARTIFACT_TYPE_MAP = {
    "invoice": "transaction_receipt",
    "receipt": "transaction_receipt",
    "shipping_confirmation": "delivery_proof",
    "tracking_events": "delivery_proof",
    "delivery_confirmation": "delivery_proof",
    "signature_confirmation": "signed_delivery",
    "customer_communication": "customer_communication",
    "refund_confirmation": "refund_policy",
    "refund_policy": "refund_policy",
    "terms_acceptance": "terms_of_service",
    "usage_activity": "usage_log",
    "authentication_result": "3ds_authentication",
    "order_record": "service_documentation",
}


class GitHubImportError(ValueError):
    pass


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    artifact_id: str
    type: str
    title: str
    repository_path: str
    sha256: str


class GitHubDisputeDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dispute_id: str
    processor: str = ""
    processor_transaction_id: str = ""
    payment_intent_id: str = ""
    order_id: str = ""
    customer_id: str = ""
    merchant_name: str = ""
    customer: dict[str, Any] = Field(default_factory=dict)
    dispute_amount: float
    transaction_amount: float | None = None
    currency: str = "USD"
    reason_code: str
    reason_description: str = ""
    dispute_phase: str = "chargeback"
    dispute_status: str = ""
    dispute_received_at: str | None = None
    response_deadline: str | None = None
    authorization_at: str | None = None
    capture_at: str | None = None
    payment_method: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    network_context: dict[str, Any] = Field(default_factory=dict)
    order: dict[str, Any] = Field(default_factory=dict)
    shipping: dict[str, Any] | None = None
    customer_communications: list[dict[str, Any]] = Field(default_factory=list)
    refund_history: list[dict[str, Any]] = Field(default_factory=list)
    usage_records: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    expected_demo_outcome: str = ""


def _safe_repo_path(path: str) -> str:
    normalized = path.replace("\\", "/").lstrip("/")
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or ".." in posix.parts:
        raise GitHubImportError(f"Unsafe artifact path: {path}")
    if not normalized.startswith("disputes/artifacts/"):
        raise GitHubImportError(f"Artifact must live under disputes/artifacts/: {path}")
    return normalized


def map_reason(code: str) -> DisputeReasonCode:
    key = code.strip()
    return REASON_MAP.get(key, REASON_MAP.get(key.lower(), DisputeReasonCode.GENERAL))


def map_phase(value: str) -> DisputePhase:
    raw = (value or "chargeback").strip().lower().replace("-", "_")
    try:
        return DisputePhase(raw)
    except ValueError:
        return DisputePhase.CHARGEBACK


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _avs_match(avs: str | None) -> bool | None:
    if not avs:
        return None
    return avs.upper() in {"Y", "D", "M", "X"}


def _cvv_match(cvv: str | None) -> bool | None:
    if not cvv:
        return None
    return cvv.upper() in {"M", "Y"}


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _newline_variants(payload: bytes) -> list[bytes]:
    """GitHub stores LF; Windows generators often hash CRLF. Accept both."""
    variants = [payload]
    if b"\r\n" in payload:
        variants.append(payload.replace(b"\r\n", b"\n"))
    elif b"\n" in payload:
        variants.append(payload.replace(b"\n", b"\r\n"))
    unique: list[bytes] = []
    seen: set[bytes] = set()
    for item in variants:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def artifact_evidence_type(artifact_type: str, path: str) -> str:
    stem = PurePosixPath(path).stem.lower()
    return ARTIFACT_TYPE_MAP.get(artifact_type, ARTIFACT_TYPE_MAP.get(stem, "other"))


def parse_document(raw: bytes) -> GitHubDisputeDocument:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubImportError("Invalid JSON") from exc
    if not isinstance(data, dict):
        raise GitHubImportError("Invalid JSON")
    try:
        return GitHubDisputeDocument.model_validate(data)
    except ValidationError as exc:
        raise GitHubImportError(f"Invalid dispute document: {exc}") from exc


def verify_artifact_hash(path: str, content: bytes, expected: str) -> None:
    want = expected.strip().lower()
    if any(sha256_hex(candidate) == want for candidate in _newline_variants(content)):
        return
    raise GitHubImportError(f"Invalid artifact hash for {path}")


def _clip(text: str, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def build_case(
    document: GitHubDisputeDocument,
    *,
    artifacts: dict[str, bytes],
    repository: str,
    ref: str,
    commit_sha: str,
    file_path: str,
    blob_sha: str,
    import_key: str,
) -> DisputeCase:
    for artifact in document.artifacts:
        path = _safe_repo_path(artifact.repository_path)
        if path not in artifacts:
            raise GitHubImportError(f"Missing artifact path: {path}")
        verify_artifact_hash(path, artifacts[path], artifact.sha256)

    three_ds = (document.verification.get("three_d_secure") or {}) if document.verification else {}
    avs = document.verification.get("avs_result") if document.verification else None
    cvv = document.verification.get("cvv_result") if document.verification else None
    network = document.network_context or {}
    customer = document.customer or {}
    payment = document.payment_method or {}
    method = str(payment.get("type") or "card")
    if method not in {"card", "upi", "netbanking", "wallet"}:
        method = "card"

    txn_time = _parse_dt(document.authorization_at) or _parse_dt(document.capture_at)
    transaction = TransactionData(
        transaction_id=document.processor_transaction_id or document.dispute_id,
        amount=document.transaction_amount if document.transaction_amount is not None else document.dispute_amount,
        currency=document.currency,
        payment_method=method,  # type: ignore[arg-type]
        timestamp=txn_time or datetime.now(),
        merchant_name=document.merchant_name,
        customer_id=document.customer_id,
        customer_email=str(customer.get("email") or ""),
        billing_address="synthetic-billing-match" if network.get("billing_shipping_match") is True else (
            "synthetic-billing-mismatch" if network.get("billing_shipping_match") is False else None
        ),
        shipping_address="synthetic-shipping-record" if document.shipping else None,
        ip_address=network.get("ip_address"),
        device_fingerprint=network.get("device_id"),
        avs_match=_avs_match(str(avs) if avs is not None else None),
        cvv_verified=_cvv_match(str(cvv) if cvv is not None else None),
        three_ds_authenticated=bool(three_ds.get("authenticated")) if three_ds else None,
    )

    extra_artifacts: list[dict[str, str]] = []
    for artifact in document.artifacts:
        path = _safe_repo_path(artifact.repository_path)
        extra_artifacts.append(
            {
                "evidence_type": artifact_evidence_type(artifact.type, path),
                "title": artifact.title,
                "content": artifacts[path].decode("utf-8", errors="replace")[:4000],
            }
        )

    shipping = document.shipping or {}
    if shipping:
        status = str(shipping.get("shipping_status") or "")
        tracking = str(shipping.get("tracking_number") or "").strip()
        extra_artifacts.append(
            {
                "evidence_type": "delivery_proof" if tracking and status.lower() == "delivered" else "other",
                "title": "Shipping record from GitHub case file",
                "content": _clip(
                    f"Shipping status={status or 'unspecified'}; tracking={tracking or 'not supplied'}; "
                    f"carrier={shipping.get('carrier') or 'unspecified'}; "
                    f"delivered_at={shipping.get('delivered_at') or 'not supplied'}; "
                    f"signature_obtained={shipping.get('signature_obtained')}."
                ),
            }
        )
    for comm in document.customer_communications:
        extra_artifacts.append(
            {
                "evidence_type": "customer_communication",
                "title": "Customer communication from GitHub case file",
                "content": _clip(
                    f"{comm.get('channel')} {comm.get('direction')} at {comm.get('occurred_at')}: {comm.get('summary')}"
                ),
            }
        )
    for refund in document.refund_history:
        extra_artifacts.append(
            {
                "evidence_type": "refund_policy",
                "title": "Refund history from GitHub case file",
                "content": _clip(json.dumps(refund, default=str)),
            }
        )
    for usage in document.usage_records:
        extra_artifacts.append(
            {
                "evidence_type": "usage_log",
                "title": "Usage record from GitHub case file",
                "content": _clip(json.dumps(usage, default=str)),
            }
        )

    case = DisputeCase(
        case_id=document.dispute_id,
        dispute_amount=document.dispute_amount,
        dispute_currency=document.currency,
        reason_code=map_reason(document.reason_code),
        dispute_phase=map_phase(document.dispute_phase),
        transaction=transaction,
        source=CaseSource(
            source_type="github",
            source_repository=repository,
            source_ref=ref,
            source_commit_sha=commit_sha,
            source_file_path=file_path,
            source_blob_sha=blob_sha,
            processor=document.processor,
            processor_reason_code=document.reason_code,
            expected_demo_outcome=document.expected_demo_outcome,
            artifact_count=len(document.artifacts),
            import_key=import_key,
        ),
    )
    case.evidence_items = evidence_from_supplied_facts(case) + evidence_from_artifacts(extra_artifacts)
    return case
