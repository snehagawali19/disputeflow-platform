"""Request and response models for the DisputeFlow API."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend.models.dispute import ISO_CURRENCIES


class SuppliedArtifact(BaseModel):
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
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=2, max_length=4000)


class CreateDisputeRequest(BaseModel):
    dispute_amount: float = Field(gt=0, le=50_000_000)
    dispute_currency: str = "INR"
    reason_code: Literal[
        "fraudulent",
        "unauthorized",
        "product_not_received",
        "product_unacceptable",
        "duplicate",
        "subscription_canceled",
        "credit_not_processed",
        "general",
    ] = "general"
    dispute_phase: Literal["inquiry", "retrieval", "chargeback", "pre_arbitration", "arbitration"] = "chargeback"
    merchant_name: str = Field(default="", max_length=200)
    merchant_id: str = Field(default="", max_length=64)
    customer_email: str = Field(default="", max_length=200)
    customer_id: str = Field(default="", max_length=64)
    payment_method: Literal["card", "upi", "netbanking", "wallet"] = "card"
    transaction_amount: Optional[float] = Field(default=None, ge=0, le=50_000_000)
    avs_match: Optional[bool] = None
    cvv_verified: Optional[bool] = None
    three_ds_authenticated: Optional[bool] = None
    ip_address: Optional[str] = Field(default=None, max_length=64)
    billing_address: Optional[str] = Field(default=None, max_length=500)
    shipping_address: Optional[str] = Field(default=None, max_length=500)
    device_fingerprint: Optional[str] = Field(default=None, max_length=128)
    artifacts: list[SuppliedArtifact] = Field(default_factory=list, max_length=20)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)

    def normalized_currency(self) -> str:
        code = self.dispute_currency.upper()
        if code not in ISO_CURRENCIES:
            raise ValueError(f"Unsupported currency: {self.dispute_currency}")
        return code


class HumanDecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "modify"]
    feedback: str = Field(default="", max_length=4000)
    outcome: Optional[Literal["won", "lost", "withdrawn"]] = None


class OutcomeRequest(BaseModel):
    outcome: Literal["won", "lost", "withdrawn"]
    feedback: str = Field(default="", max_length=4000)
