"""Idempotent GitHub dispute ingestion. GitHub is the source of truth; DB stores snapshots."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, get_settings
from backend.db.models import GitHubImportRecord, GitHubSyncState, GitHubWebhookDelivery
from backend.repositories.disputes import DisputeRepository
from backend.services.github_client import GitHubAPIError, GitHubAuthError, GitHubClient
from backend.services.github_normalize import GitHubImportError, build_case, parse_document
from backend.utils.audit import AuditTrail

INPUT_PREFIX = "disputes/input/"
ARTIFACT_PREFIX = "disputes/artifacts/"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def import_key(owner: str, repo: str, commit_sha: str, file_path: str) -> str:
    return f"github:{owner}/{repo}:{commit_sha}:{file_path}"


def source_identity(owner: str, repo: str, file_path: str) -> str:
    return f"github:{owner}/{repo}:{file_path}"


def verify_github_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    prefix = "sha256="
    if not header.startswith(prefix):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len(prefix) :], expected)


def push_touches_disputes(payload: dict[str, Any]) -> bool:
    commits = payload.get("commits") or []
    keys = ("added", "modified", "removed")
    for commit in commits:
        for key in keys:
            for path in commit.get(key) or []:
                if str(path).startswith(INPUT_PREFIX) or str(path).startswith(ARTIFACT_PREFIX):
                    return True
    return False


class GitHubDisputeSource:
    def __init__(self, session: AsyncSession, settings: Settings | None = None, client: GitHubClient | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.client = client or GitHubClient(self.settings)
        self.repo = DisputeRepository(session)

    async def sync(self, commit_sha: str | None = None, file_path: str | None = None) -> dict[str, Any]:
        owner, repo = self.settings.github_owner_repo
        if not owner or not repo:
            raise GitHubAPIError("GITHUB_OWNER and GITHUB_REPO (or GITHUB_REPO_URL) must be configured")
        sha = commit_sha or await self.client.get_ref_commit()
        files = await self.client.list_input_files(sha)
        if file_path:
            files = [item for item in files if item["path"] == file_path]
            if not files:
                raise GitHubAPIError(f"GitHub case not found: {file_path}")
        imported = updated = unchanged = rejected = 0
        for item in files:
            path = item["path"]
            blob_sha = item["sha"]
            key = import_key(owner, repo, sha, path)
            identity = source_identity(owner, repo, path)
            existing = await self.session.scalar(
                select(GitHubImportRecord).where(GitHubImportRecord.source_identity == identity)
            )
            if existing and existing.blob_sha == blob_sha and existing.status == "imported":
                unchanged += 1
                continue
            try:
                raw, fetched_blob = await self.client.get_file(path, sha)
                blob_sha = fetched_blob or blob_sha
                document = parse_document(raw)
                artifact_bytes: dict[str, bytes] = {}
                for artifact in document.artifacts:
                    try:
                        content, _ = await self.client.get_file(artifact.repository_path, sha)
                    except FileNotFoundError as exc:
                        raise GitHubImportError(f"Missing artifact path: {artifact.repository_path}") from exc
                    artifact_bytes[artifact.repository_path.replace("\\", "/").lstrip("/")] = content
                case = build_case(
                    document,
                    artifacts=artifact_bytes,
                    repository=f"{owner}/{repo}",
                    ref=self.settings.github_ref,
                    commit_sha=sha,
                    file_path=path,
                    blob_sha=blob_sha,
                    import_key=key,
                )
                audit_payload = {
                    "import_key": key,
                    "commit_sha": sha,
                    "blob_sha": blob_sha,
                    "file_path": path,
                }
                if existing and existing.session_id:
                    record = await self.repo.get(existing.session_id)
                    if record is not None and record.pipeline_status not in {"created", "failed"}:
                        unchanged += 1
                        continue
                    if record is None:
                        await self.repo.create(existing.session_id, case)
                        await AuditTrail(self.session, case.case_id, existing.session_id).add_entry(
                            "github", "import", audit_payload
                        )
                        imported += 1
                    else:
                        await self.repo.save(record, case, pipeline_status="created")
                        await AuditTrail(self.session, case.case_id, record.session_id).add_entry(
                            "github", "update", audit_payload
                        )
                        updated += 1
                    existing.import_key = key
                    existing.commit_sha = sha
                    existing.blob_sha = blob_sha
                    existing.status = "imported"
                    existing.rejected_reason = ""
                    existing.updated_at = utcnow()
                elif existing:
                    session_id = str(uuid.uuid4())
                    await self.repo.create(session_id, case)
                    await AuditTrail(self.session, case.case_id, session_id).add_entry(
                        "github", "import", audit_payload
                    )
                    existing.import_key = key
                    existing.commit_sha = sha
                    existing.blob_sha = blob_sha
                    existing.session_id = session_id
                    existing.status = "imported"
                    existing.rejected_reason = ""
                    existing.updated_at = utcnow()
                    imported += 1
                else:
                    session_id = str(uuid.uuid4())
                    await self.repo.create(session_id, case)
                    await AuditTrail(self.session, case.case_id, session_id).add_entry(
                        "github", "import", audit_payload
                    )
                    self.session.add(
                        GitHubImportRecord(
                            source_identity=identity,
                            import_key=key,
                            file_path=path,
                            commit_sha=sha,
                            blob_sha=blob_sha,
                            session_id=session_id,
                            status="imported",
                            rejected_reason="",
                        )
                    )
                    imported += 1
            except (GitHubImportError, GitHubAPIError, GitHubAuthError) as exc:
                rejected += 1
                if existing:
                    existing.status = "rejected"
                    existing.rejected_reason = str(exc)
                    existing.commit_sha = sha
                    existing.blob_sha = blob_sha
                    existing.import_key = key
                    existing.updated_at = utcnow()
                else:
                    self.session.add(
                        GitHubImportRecord(
                            source_identity=identity,
                            import_key=key,
                            file_path=path,
                            commit_sha=sha,
                            blob_sha=blob_sha,
                            session_id=None,
                            status="rejected",
                            rejected_reason=str(exc),
                        )
                    )

        state = await self.session.get(GitHubSyncState, 1)
        if state is None:
            state = GitHubSyncState(id=1)
            self.session.add(state)
        state.last_commit_sha = sha
        state.last_sync_at = utcnow()
        state.last_imported = imported
        state.last_updated = updated
        state.last_unchanged = unchanged
        state.last_rejected = rejected
        state.cumulative_imported = (state.cumulative_imported or 0) + imported
        state.cumulative_rejected = (state.cumulative_rejected or 0) + rejected
        state.last_error = ""
        await self.session.flush()
        return {
            "imported": imported,
            "updated": updated,
            "unchanged": unchanged,
            "rejected": rejected,
            "commit_sha": sha,
        }

    async def fetch_one(self) -> dict[str, Any]:
        """Import the next valid, unprocessed case without bulk-importing the corpus."""
        owner, repo = self.settings.github_owner_repo
        if not owner or not repo:
            raise GitHubAPIError("GITHUB_OWNER and GITHUB_REPO (or GITHUB_REPO_URL) must be configured")

        sha = await self.client.get_ref_commit()
        files = await self.client.list_input_files(sha)
        rejected: list[str] = []
        for item in files:
            path = item["path"]
            identity = source_identity(owner, repo, path)
            existing = await self.session.scalar(
                select(GitHubImportRecord).where(GitHubImportRecord.source_identity == identity)
            )
            if existing and existing.status == "imported" and existing.session_id:
                record = await self.repo.get(existing.session_id)
                if record is not None:
                    if record.pipeline_status in {"created", "failed"}:
                        return {
                            "session_id": existing.session_id,
                            "case_id": self.repo.to_case(record).case_id,
                            "file_path": path,
                            "commit_sha": sha,
                            "import": {"imported": 0, "updated": 0, "unchanged": 1, "rejected": 0},
                        }
                    continue

            result = await self.sync(commit_sha=sha, file_path=path)
            if result["imported"] or result["updated"]:
                record = await self.session.scalar(
                    select(GitHubImportRecord).where(GitHubImportRecord.source_identity == identity)
                )
                if record and record.session_id:
                    imported_case = await self.repo.get(record.session_id)
                    imported_case_id = self.repo.to_case(imported_case).case_id if imported_case else ""
                    return {
                        "session_id": record.session_id,
                        "case_id": imported_case_id,
                        "file_path": path,
                        "commit_sha": sha,
                        "import": result,
                    }
            if result["rejected"]:
                record = await self.session.scalar(
                    select(GitHubImportRecord).where(GitHubImportRecord.source_identity == identity)
                )
                rejected.append(f"{path}: {record.rejected_reason if record else 'rejected'}")

        detail = "; ".join(rejected[:3])
        raise GitHubImportError(
            "No valid unprocessed GitHub case is available. "
            + (f"Validation errors: {detail}" if detail else "All cases have already been processed.")
        )

    async def status(self) -> dict[str, Any]:
        owner, repo = self.settings.github_owner_repo
        state = await self.session.get(GitHubSyncState, 1)
        imported_count = (
            await self.session.scalars(select(GitHubImportRecord).where(GitHubImportRecord.status == "imported"))
        ).all()
        rejected_count = (
            await self.session.scalars(select(GitHubImportRecord).where(GitHubImportRecord.status == "rejected"))
        ).all()
        return {
            "source": self.settings.dispute_source,
            "repository": f"{owner}/{repo}" if owner else "",
            "ref": self.settings.github_ref,
            "last_commit_sha": state.last_commit_sha if state else "",
            "last_sync_at": state.last_sync_at.isoformat() if state and state.last_sync_at else None,
            "imported_count": len(list(imported_count)),
            "rejected_count": len(list(rejected_count)),
            "last_imported": state.last_imported if state else 0,
            "last_updated": state.last_updated if state else 0,
            "last_unchanged": state.last_unchanged if state else 0,
            "last_rejected": state.last_rejected if state else 0,
            "webhook_configured": bool(self.settings.github_webhook_secret),
            "allow_local_create": self.settings.allow_local_dispute_create,
        }

    async def record_delivery(self, delivery_id: str, event: str, commit_sha: str) -> bool:
        existing = await self.session.get(GitHubWebhookDelivery, delivery_id)
        if existing:
            return False
        self.session.add(GitHubWebhookDelivery(delivery_id=delivery_id, event=event, commit_sha=commit_sha))
        await self.session.flush()
        return True
