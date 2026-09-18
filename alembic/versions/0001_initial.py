"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("pipeline_status", sa.String(64), nullable=False, server_default="created"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "progress_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("cases.session_id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "sequence", name="uq_progress_session_seq"),
    )
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.String(64), sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column("hmac_signature", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "sequence", name="uq_audit_case_seq"),
    )
    op.create_table(
        "training_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.String(64), nullable=False, unique=True),
        sa.Column("features_json", sa.Text(), nullable=False),
        sa.Column("outcome_won", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("training_outcomes")
    op.drop_table("audit_entries")
    op.drop_table("progress_events")
    op.drop_table("cases")
