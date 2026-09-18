"""SQLAlchemy persistence models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CaseRecord(Base):
    __tablename__ = "cases"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    pipeline_status: Mapped[str] = mapped_column(String(64), default="created")
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[str] = mapped_column(Text)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    events: Mapped[list["ProgressEvent"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    audit_entries: Mapped[list["AuditEntry"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class ProgressEvent(Base):
    __tablename__ = "progress_events"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_progress_session_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("cases.session_id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[CaseRecord] = relationship(back_populates="events")


class AuditEntry(Base):
    __tablename__ = "audit_entries"
    __table_args__ = (UniqueConstraint("case_id", "sequence", name="uq_audit_case_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    data_json: Mapped[str] = mapped_column(Text)
    previous_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))
    hmac_signature: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[CaseRecord] = relationship(back_populates="audit_entries")


class GitHubImportRecord(Base):
    __tablename__ = "github_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_identity: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    import_key: Mapped[str] = mapped_column(String(768), index=True)
    file_path: Mapped[str] = mapped_column(String(512), index=True)
    commit_sha: Mapped[str] = mapped_column(String(64), index=True)
    blob_sha: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="imported")
    rejected_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GitHubSyncState(Base):
    __tablename__ = "github_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_commit_sha: Mapped[str] = mapped_column(String(64), default="")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_imported: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[int] = mapped_column(Integer, default=0)
    last_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    last_rejected: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_imported: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_rejected: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class GitHubWebhookDelivery(Base):
    __tablename__ = "github_webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event: Mapped[str] = mapped_column(String(64), default="push")
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrainingOutcome(Base):
    __tablename__ = "training_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), unique=True)
    features_json: Mapped[str] = mapped_column(Text)
    outcome_won: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
