"""Strict structured-output schemas for LLM agents."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntakeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: Literal[
        "fraudulent",
        "unauthorized",
        "product_not_received",
        "product_unacceptable",
        "duplicate",
        "subscription_canceled",
        "credit_not_processed",
        "general",
    ]
    risk_level: Literal["low", "medium", "high", "critical"]
    intake_summary: str = Field(min_length=10, max_length=2000)
    dispute_phase: Literal["inquiry", "retrieval", "chargeback", "pre_arbitration", "arbitration"]
    classification_notes: str = Field(default="", max_length=2000)
    urgency_flags: list[str] = Field(default_factory=list)


class EvidenceScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    notes: str = Field(default="", max_length=500)


class EvidenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: list[EvidenceScore] = Field(default_factory=list, max_length=20)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=12)
    overall_evidence_strength: Literal["strong", "moderate", "weak"] = "moderate"
    notes: str = Field(default="", max_length=2000)


class StrategyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommended_action: Literal["contest", "accept", "escalate_to_human"]
    reasoning: str = Field(min_length=10, max_length=4000)
    key_arguments: list[str] = Field(default_factory=list, max_length=8)
    evidence_strength: Literal["strong", "moderate", "weak"]
    estimated_effort_hours: float = Field(ge=0.0, le=200.0)
    risk_of_escalation: float = Field(ge=0.0, le=1.0)


class ResponseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rebuttal_letter: str = Field(min_length=40, max_length=12000)
    evidence_summary: str = Field(min_length=5, max_length=8000)
    word_count: int = Field(ge=1, le=2500)


class FilingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filing_approved: bool
    validation_notes: str = ""
    missing_requirements: list[str] = Field(default_factory=list)
    filing_confidence: float = Field(ge=0.0, le=1.0)


class ModelFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prediction_accuracy: Literal["accurate", "over_confident", "under_confident", "unknown"] = "unknown"
    key_features_that_mattered: list[str] = Field(default_factory=list)
    missed_signals: list[str] = Field(default_factory=list)


class FeedbackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lessons_learned: list[str] = Field(default_factory=list)
    model_feedback: ModelFeedback = Field(default_factory=ModelFeedback)
    process_improvements: list[str] = Field(default_factory=list)
    outcome_analysis: str = ""
