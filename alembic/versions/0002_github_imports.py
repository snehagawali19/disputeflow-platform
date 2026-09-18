"""github import tables

Revision ID: 0002_github_imports
Revises: 0001_initial
Create Date: 2026-09-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_github_imports"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_imports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_identity", sa.String(512), nullable=False),
        sa.Column("import_key", sa.String(768), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("blob_sha", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="imported"),
        sa.Column("rejected_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_github_imports_source_identity", "github_imports", ["source_identity"], unique=True)
    op.create_index("ix_github_imports_import_key", "github_imports", ["import_key"], unique=False)
    op.create_table(
        "github_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_commit_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cumulative_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cumulative_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "github_webhook_deliveries",
        sa.Column("delivery_id", sa.String(128), primary_key=True),
        sa.Column("event", sa.String(64), nullable=False, server_default="push"),
        sa.Column("commit_sha", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("github_webhook_deliveries")
    op.drop_table("github_sync_state")
    op.drop_index("ix_github_imports_import_key", table_name="github_imports")
    op.drop_index("ix_github_imports_source_identity", table_name="github_imports")
    op.drop_table("github_imports")
