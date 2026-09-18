"""
DisputeFlow core data models.
Adapted from agentguard-cb evidence schemas + FinancialAnalysisAgent state patterns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DisputePhase(str, Enum):
    INQUIRY = "inquiry"
    RETRIEVAL = "retrieval"
    CHARGEBACK = "chargeback"
    PRE_ARBITRATION = "pre_arbitration"
    ARBITRATION = "arbitration"


class DisputeStatus(str, Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    EVIDENCE_GATHERED = "evidence_gathered"
    STRATEGY_SET = "strategy_set"
    RESPONSE_DRAFTED = "response_drafted"
    FILED = "filed"
    AWAITING_OUTCOME = "awaiting_outcome"
    WON = "won"
    LOST = "lost"
    ACCEPTED = "accepted"
    PENDING_HUMAN_REVIEW = "pending_human_review"
    FAILED = "failed"


class DisputeReasonCode(str, Enum):
    FRAUDULENT = "fraudulent"
    UNAUTHORIZED = "unauthorized"
    PRODUCT_NOT_RECEIVED = "product_not_received"
    PRODUCT_UNACCEPTABLE = "product_unacceptable"
    DUPLICATE = "duplicate"
    SUBSCRIPTION_CANCELED = "subscription_canceled"
    CREDIT_NOT_PROCESSED = "credit_not_processed"
    GENERAL = "general"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


ISO_CURRENCIES = {
    "INR", "USD", "EUR", "GBP", "AUD", "CAD", "SGD", "AED", "JPY", "CHF",
}


class TransactionData(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    transaction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    amount: float = Field(ge=0, le=50_000_000)
    currency: str = "INR"
    payment_method: Literal["card", "upi", "netbanking", "wallet"] = "card"
    timestamp: datetime = Field(default_factory=utcnow)
    merchant_id: str = ""
    merchant_name: str = ""
    customer_id: str = ""
    customer_email: str = ""
    billing_address: Optional[str] = None
    shipping_address: Optional[str] = None
    ip_address: Optional[str] = None
    device_fingerprint: Optional[str] = None
    avs_match: Optional[bool] = None
    cvv_verified: Optional[bool] = None
    three_ds_authenticated: Optional[bool] = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        code = value.upper()
        if code not in ISO_CURRENCIES:
            raise ValueError(f"Unsupported currency: {value}")
        return code


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    evidence_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    evidence_type: Literal[
        "delivery_proof",
        "customer_communication",
        "refund_policy",
        "transaction_receipt",
        "service_documentation",
        "ip_address_log",
        "device_fingerprint",
        "avs_cvv_verification",
        "3ds_authentication",
        "signed_delivery",
        "usage_log",
        "terms_of_service",
        "other",
    ]
    title: str
    content: str
    source: str = "supplied_transaction"
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=utcnow)


class DisputeStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    recommended_action: Literal["contest", "accept", "escalate_to_human"] = "contest"
    win_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""
    key_arguments: list[str] = Field(default_factory=list)
    evidence_strength: Literal["strong", "moderate", "weak"] = "moderate"
    estimated_effort_hours: float = Field(default=0.0, ge=0.0, le=200.0)
    risk_of_escalation: float = Field(default=0.0, ge=0.0, le=1.0)
    model_inputs: list[str] = Field(default_factory=list)


class DisputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    response_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rebuttal_letter: str = ""
    evidence_summary: str = ""
    compiled_evidence_ids: list[str] = Field(default_factory=list)
    response_format: Literal["text", "pdf", "structured_json"] = "text"
    word_count: int = Field(default=0, ge=0)
    generated_at: datetime = Field(default_factory=utcnow)


class CaseSource(BaseModel):
    """Provenance for GitHub-imported or API-created cases."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    source_type: Literal["github", "local", "api"] = "api"
    source_repository: str = ""
    source_ref: str = ""
    source_commit_sha: str = ""
    source_file_path: str = ""
    source_blob_sha: str = ""
    processor: str = ""
    processor_reason_code: str = ""
    expected_demo_outcome: str = ""
    artifact_count: int = Field(default=0, ge=0)
    import_key: str = ""


class FilingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    filing_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filed_at: Optional[datetime] = None
    filing_deadline: Optional[datetime] = None
    filing_channel: Literal["api", "dashboard", "email", "manual"] = "api"
    confirmation_reference: str = ""
    status: Literal["pending", "submitted", "confirmed", "rejected"] = "pending"
    simulated: bool = True
    notes: str = ""


class DisputeCase(BaseModel):
    """Master model representing a complete dispute case flowing through the pipeline."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    case_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    dispute_phase: DisputePhase = DisputePhase.CHARGEBACK
    status: DisputeStatus = DisputeStatus.OPEN
    reason_code: DisputeReasonCode = DisputeReasonCode.GENERAL
    risk_level: RiskLevel = RiskLevel.MEDIUM
    dispute_amount: float = Field(default=0.0, ge=0, le=50_000_000)
    dispute_currency: str = "INR"

    transaction: Optional[TransactionData] = None
    source: Optional[CaseSource] = None

    intake_summary: str = ""
    intake_classification: str = ""
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    strategy: Optional[DisputeStrategy] = None
    response: Optional[DisputeResponse] = None
    filing: Optional[FilingRecord] = None

    actual_outcome: Optional[Literal["won", "lost", "withdrawn"]] = None
    outcome_reason: str = ""
    feedback_notes: str = ""
    lessons_learned: list[str] = Field(default_factory=list)

    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    human_approvals: list[dict[str, Any]] = Field(default_factory=list)
    error_log: list[str] = Field(default_factory=list)

    @field_validator("dispute_currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        code = value.upper()
        if code not in ISO_CURRENCIES:
            raise ValueError(f"Unsupported currency: {value}")
        return code

    @model_validator(mode="after")
    def touch_updated_at(self) -> "DisputeCase":
        return self

    def append_trace(self, entry: dict[str, Any]) -> None:
        self.agent_trace.append(entry)
        self.updated_at = utcnow()
