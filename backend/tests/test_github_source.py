"""GitHub ingestion, webhook, and provenance tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest

from backend.core.config import get_settings
from backend.models.agent_outputs import (
    EvidenceResult,
    FeedbackResult,
    FilingResult,
    IntakeResult,
    ResponseResult,
    StrategyResult,
)
from backend.services.github_client import GitHubAuthError
from backend.services.github_normalize import GitHubImportError, verify_artifact_hash
from backend.services.github_source import GitHubDisputeSource


class FakeGitHubClient:
    def __init__(self, files: dict[str, tuple[bytes, str]], commit: str = "abc123def"):
        self.files = files
        self.commit = commit
        self.owner = "demo-owner"
        self.repo = "demo-repo"
        self.ref = "main"
        self.input_path = "disputes/input"

    async def get_ref_commit(self, ref: str | None = None) -> str:
        return self.commit

    async def list_input_files(self, commit_sha: str) -> list[dict]:
        items = []
        for path, (content, sha) in self.files.items():
            if path.startswith("disputes/input/") and path.endswith(".json"):
                items.append({"path": path, "sha": sha, "name": path.rsplit("/", 1)[-1]})
        return items

    async def get_file(self, path: str, commit_sha: str) -> tuple[bytes, str]:
        if path not in self.files:
            raise GitHubAuthError("missing") if path == "missing-auth" else FileNotFoundError(path)
        return self.files[path]


def _file(payload: dict | str, sha: str | None = None) -> tuple[bytes, str]:
    raw = payload if isinstance(payload, bytes) else (
        payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    )
    return raw, sha or hashlib.sha1(raw).hexdigest()


def _artifact(rel: str, body: dict) -> tuple[str, dict, tuple[bytes, str]]:
    raw, sha = _file(body)
    digest = hashlib.sha256(raw).hexdigest()
    meta = {
        "artifact_id": "art-1",
        "type": "invoice",
        "title": "Synthetic invoice",
        "repository_path": rel,
        "sha256": digest,
    }
    return rel, meta, (raw, sha)


def _dispute(**overrides):
    did = overrides.pop("dispute_id", None) or f"df-test-{uuid.uuid4().hex[:10]}"
    rel, meta, blob = _artifact(f"disputes/artifacts/{did}/invoice.json", {"ok": True})
    doc = {
        "dispute_id": did,
        "processor": "stripe",
        "processor_transaction_id": "ch_demo_001",
        "merchant_name": "Demo Merchant",
        "customer": {"name": "Synthetic Customer 001", "email": "customer001@example.test"},
        "customer_id": "cus_demo_001",
        "dispute_amount": 50.0,
        "transaction_amount": 50.0,
        "currency": "USD",
        "reason_code": "13.1",
        "reason_description": "Merchandise or services not received",
        "dispute_phase": "chargeback",
        "payment_method": {"type": "card", "brand": "visa", "last4": "4242", "funding": "credit"},
        "verification": {
            "avs_result": "Y",
            "cvv_result": "M",
            "three_d_secure": {"supported": True, "authenticated": True, "version": "2.2.0", "result": "authenticated"},
        },
        "network_context": {"ip_address": "192.0.2.1", "device_id": "device-demo-001", "billing_shipping_match": True},
        "order": {"product_description": "Synthetic product", "quantity": 1},
        "shipping": {
            "carrier": "UPS",
            "tracking_number": "1ZDEMO001",
            "shipping_status": "delivered",
            "delivered_at": "2026-08-24T15:30:00Z",
            "signature_obtained": True,
        },
        "customer_communications": [
            {"channel": "email", "occurred_at": "2026-08-25T09:00:00Z", "direction": "outbound", "summary": "Synthetic note"}
        ],
        "refund_history": [],
        "usage_records": [],
        "artifacts": [meta],
        "expected_demo_outcome": "contest",
    }
    doc.update(overrides)
    return doc, rel, blob


def _intake(**kwargs):
    data = dict(
        reason_code="product_not_received",
        risk_level="low",
        intake_summary="Imported GitHub case with supplied checkout facts only.",
        dispute_phase="chargeback",
        classification_notes="ok",
        urgency_flags=[],
    )
    data.update(kwargs)
    return IntakeResult(**data)


def _patch_source(monkeypatch, files, commit="abc123def"):
    def patched(self, session, settings=None, client=None):
        from backend.repositories.disputes import DisputeRepository

        self.session = session
        self.settings = get_settings()
        self.client = FakeGitHubClient(files, commit=commit)
        self.repo = DisputeRepository(session)

    monkeypatch.setattr(GitHubDisputeSource, "__init__", patched)


@pytest.mark.asyncio
async def test_github_sync_success_and_unchanged(client, api_headers, monkeypatch):
    doc, rel, blob = _dispute()
    monkeypatch.setattr(get_settings(), "github_owner", "demo-owner")
    monkeypatch.setattr(get_settings(), "github_repo", "demo-repo")
    path = f"disputes/input/{uuid.uuid4().hex}.json"
    files = {path: _file(doc, "blob-1"), rel: blob}
    _patch_source(monkeypatch, files)
    first = await client.post("/github/sync", headers=api_headers)
    assert first.status_code == 200
    body = first.json()
    assert body["imported"] == 1
    assert body["rejected"] == 0
    assert body["commit_sha"] == "abc123def"
    listed = await client.get("/disputes", headers=api_headers)
    github_rows = [row for row in listed.json() if row["source_type"] == "github"]
    assert github_rows
    second = await client.post("/github/sync", headers=api_headers)
    assert second.json()["unchanged"] == 1
    assert second.json()["imported"] == 0
    session_id = github_rows[0]["session_id"]
    trace = await client.get(f"/disputes/{session_id}/trace", headers=api_headers)
    detail = await client.get(f"/disputes/{session_id}", headers=api_headers)
    source = detail.json()["source"]
    assert source["source_commit_sha"] == "abc123def"
    assert source["source_blob_sha"] == "blob-1"
    assert any(entry.get("data", {}).get("commit_sha") == "abc123def" for entry in trace.json()["audit"])
    assert any(entry.get("data", {}).get("blob_sha") == "blob-1" for entry in trace.json()["audit"])


@pytest.mark.asyncio
async def test_fetch_one_reuses_an_imported_case_that_has_not_run(client, api_headers, monkeypatch):
    """A manual sync must not make the next fetch-and-analyze action unusable."""
    monkeypatch.setattr(get_settings(), "github_owner", "demo-owner")
    monkeypatch.setattr(get_settings(), "github_repo", "demo-repo")
    doc, rel, blob = _dispute()
    path = f"disputes/input/{uuid.uuid4().hex}.json"
    files = {path: _file(doc, "blob-fetch"), rel: blob}
    _patch_source(monkeypatch, files)

    async def no_background_run(_session_id: str) -> None:
        return None

    monkeypatch.setattr("backend.api.routes.run_pipeline_in_background", no_background_run)
    synced = await client.post("/github/sync", headers=api_headers)
    assert synced.status_code == 200

    fetched = await client.post("/github/fetch-one-and-start", headers=api_headers)
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["status"] == "running"
    assert body["file_path"] == path
    assert body["import"]["unchanged"] == 1


def test_artifact_hash_accepts_github_lf_vs_windows_crlf():
    crlf = b'{\r\n  "order_id": "order-demo-001"\r\n}\r\n'
    lf = crlf.replace(b"\r\n", b"\n")
    verify_artifact_hash("invoice.json", lf, hashlib.sha256(crlf).hexdigest())
    verify_artifact_hash("invoice.json", crlf, hashlib.sha256(lf).hexdigest())
    with pytest.raises(GitHubImportError):
        verify_artifact_hash("invoice.json", lf, "0" * 64)


@pytest.mark.asyncio
async def test_sync_accepts_lf_artifact_when_manifest_hashed_crlf(client, api_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "github_owner", "demo-owner")
    monkeypatch.setattr(get_settings(), "github_repo", "demo-repo")
    doc, rel, blob = _dispute()
    lf = blob[0].replace(b"\r\n", b"\n") if b"\r\n" in blob[0] else blob[0]
    crlf = lf.replace(b"\n", b"\r\n")
    doc["artifacts"][0]["sha256"] = hashlib.sha256(crlf).hexdigest()
    path = f"disputes/input/{uuid.uuid4().hex}.json"
    files = {path: _file(doc, "blob-lf"), rel: (lf, blob[1])}
    _patch_source(monkeypatch, files)
    first = await client.post("/github/sync", headers=api_headers)
    assert first.status_code == 200
    assert first.json()["imported"] == 1
    assert first.json()["rejected"] == 0


@pytest.mark.asyncio
async def test_github_auth_failure(client, api_headers, monkeypatch):
    class Boom(FakeGitHubClient):
        async def get_ref_commit(self, ref=None):
            raise GitHubAuthError("GitHub authentication failed")

    def patched(self, session, settings=None, client=None):
        from backend.repositories.disputes import DisputeRepository

        self.session = session
        self.settings = get_settings()
        self.client = Boom({})
        self.repo = DisputeRepository(session)

    monkeypatch.setattr(GitHubDisputeSource, "__init__", patched)
    monkeypatch.setattr(get_settings(), "github_owner", "demo-owner")
    monkeypatch.setattr(get_settings(), "github_repo", "demo-repo")
    response = await client.post("/github/sync", headers=api_headers)
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_invalid_json_and_missing_artifact_and_bad_hash(client, api_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "github_owner", "o")
    monkeypatch.setattr(get_settings(), "github_repo", "r")

    def run_with(files):
        def patched(self, session, settings=None, client=None):
            from backend.repositories.disputes import DisputeRepository

            self.session = session
            self.settings = get_settings()
            self.client = FakeGitHubClient(files)
            self.repo = DisputeRepository(session)

        return patched

    monkeypatch.setattr(GitHubDisputeSource, "__init__", run_with({f"disputes/input/{uuid.uuid4().hex}.json": _file("{not-json", "b1")}))
    bad_json = await client.post("/github/sync", headers=api_headers)
    assert bad_json.json()["rejected"] == 1

    doc, rel, blob = _dispute()
    files = {f"disputes/input/{uuid.uuid4().hex}.json": _file(doc, "b2")}
    monkeypatch.setattr(GitHubDisputeSource, "__init__", run_with(files))
    missing = await client.post("/github/sync", headers=api_headers)
    assert missing.json()["rejected"] >= 1

    doc, rel, blob = _dispute()
    doc["artifacts"][0]["sha256"] = "0" * 64
    files = {f"disputes/input/{uuid.uuid4().hex}.json": _file(doc, "b3"), rel: blob}
    monkeypatch.setattr(GitHubDisputeSource, "__init__", run_with(files))
    hashed = await client.post("/github/sync", headers=api_headers)
    assert hashed.json()["rejected"] == 1


@pytest.mark.asyncio
async def test_changed_file_updates(client, api_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "github_owner", "o")
    monkeypatch.setattr(get_settings(), "github_repo", "r")
    doc, rel, blob = _dispute()
    path = f"disputes/input/{uuid.uuid4().hex}.json"
    files = {path: _file(doc, "blob-a"), rel: blob}

    def patched(self, session, settings=None, client=None):
        from backend.repositories.disputes import DisputeRepository

        self.session = session
        self.settings = get_settings()
        self.client = FakeGitHubClient(files)
        self.repo = DisputeRepository(session)

    monkeypatch.setattr(GitHubDisputeSource, "__init__", patched)
    await client.post("/github/sync", headers=api_headers)
    doc["dispute_amount"] = 77.0
    files[path] = _file(doc, "blob-b")
    updated = await client.post("/github/sync", headers=api_headers)
    assert updated.json()["updated"] == 1


@pytest.mark.asyncio
async def test_webhook_duplicate_and_push(client, api_headers, monkeypatch):
    secret = "webhook-secret-for-tests"
    monkeypatch.setattr(get_settings(), "github_webhook_secret", secret)
    monkeypatch.setattr(get_settings(), "github_owner", "o")
    monkeypatch.setattr(get_settings(), "github_repo", "r")
    doc, rel, blob = _dispute()
    path = f"disputes/input/{uuid.uuid4().hex}.json"
    files = {path: _file(doc, "blob-w"), rel: blob}

    def patched(self, session, settings=None, client=None):
        from backend.repositories.disputes import DisputeRepository

        self.session = session
        self.settings = get_settings()
        self.client = FakeGitHubClient(files, commit="pushsha")
        self.repo = DisputeRepository(session)

    monkeypatch.setattr(GitHubDisputeSource, "__init__", patched)
    payload = {
        "after": "pushsha",
        "commits": [{"added": [path], "modified": [], "removed": []}],
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": f"delivery-{uuid.uuid4().hex}",
        "Content-Type": "application/json",
    }
    first = await client.post("/webhooks/github", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["imported"] == 1
    second = await client.post("/webhooks/github", content=body, headers=headers)
    assert second.json()["duplicate"] is True
    assert second.json()["imported"] == 0


@pytest.mark.asyncio
async def test_human_review_missing_delivery_and_low_confidence(client, api_headers, monkeypatch):
    async def fake_structured(schema, *_args, **_kwargs):
        if schema.__name__ == "IntakeResult":
            return _intake(risk_level="high")
        if schema.__name__ == "EvidenceResult":
            return EvidenceResult(scores=[], evidence_gaps=[], overall_evidence_strength="weak")
        if schema.__name__ == "StrategyResult":
            return StrategyResult(
                recommended_action="escalate_to_human",
                reasoning="Low confidence because delivery confirmation is not in the supplied GitHub artifacts.",
                key_arguments=["Missing delivery"],
                evidence_strength="weak",
                estimated_effort_hours=2,
                risk_of_escalation=0.8,
            )
        if schema.__name__ == "ResponseResult":
            return ResponseResult(rebuttal_letter="We contest using only supplied records. " * 6, evidence_summary="- invoice", word_count=80)
        if schema.__name__ == "FilingResult":
            return FilingResult(filing_approved=True, validation_notes="simulated", missing_requirements=[], filing_confidence=0.4)
        if schema.__name__ == "FeedbackResult":
            return FeedbackResult(lessons_learned=["n/a"], outcome_analysis="test")
        raise AssertionError(schema)

    for name in (
        "backend.agents.intake_agent.invoke_structured",
        "backend.agents.evidence_assembly_agent.invoke_structured",
        "backend.agents.strategy_agent.invoke_structured",
        "backend.agents.response_drafting_agent.invoke_structured",
        "backend.agents.filing_agent.invoke_structured",
        "backend.agents.feedback_loop_agent.invoke_structured",
    ):
        monkeypatch.setattr(name, fake_structured)

    created = await client.post(
        "/disputes",
        json={"dispute_amount": 9000, "reason_code": "product_not_received", "merchant_name": "Demo"},
        headers=api_headers,
    )
    session_id = created.json()["session_id"]
    started = await client.post(f"/disputes/{session_id}/start", headers=api_headers)
    assert started.status_code == 200
    assert started.json()["status"] == "awaiting_human_review"
    detail = await client.get(f"/disputes/{session_id}", headers=api_headers)
    gaps = detail.json()["case"]["evidence_gaps"]
    assert any("delivery" in gap.lower() for gap in gaps)


@pytest.mark.asyncio
async def test_import_then_analysis(client, api_headers, monkeypatch):
    async def fake_structured(schema, *_args, **_kwargs):
        if schema.__name__ == "IntakeResult":
            return _intake()
        if schema.__name__ == "EvidenceResult":
            return EvidenceResult(scores=[], evidence_gaps=[], overall_evidence_strength="strong")
        if schema.__name__ == "StrategyResult":
            return StrategyResult(
                recommended_action="contest",
                reasoning="Supplied GitHub delivery confirmation supports a contest.",
                key_arguments=["Delivery"],
                evidence_strength="strong",
                estimated_effort_hours=1,
                risk_of_escalation=0.1,
            )
        if schema.__name__ == "ResponseResult":
            return ResponseResult(
                rebuttal_letter="We contest this dispute based solely on the supplied GitHub artifacts. " * 4,
                evidence_summary="- delivery",
                word_count=90,
            )
        if schema.__name__ == "FilingResult":
            return FilingResult(filing_approved=True, validation_notes="ok", missing_requirements=[], filing_confidence=0.8)
        if schema.__name__ == "FeedbackResult":
            return FeedbackResult(lessons_learned=["ok"], outcome_analysis="ok")
        raise AssertionError(schema)

    for name in (
        "backend.agents.intake_agent.invoke_structured",
        "backend.agents.evidence_assembly_agent.invoke_structured",
        "backend.agents.strategy_agent.invoke_structured",
        "backend.agents.response_drafting_agent.invoke_structured",
        "backend.agents.filing_agent.invoke_structured",
    ):
        monkeypatch.setattr(name, fake_structured)

    monkeypatch.setattr(get_settings(), "github_owner", "o")
    monkeypatch.setattr(get_settings(), "github_repo", "r")
    doc, rel, blob = _dispute()
    path = f"disputes/input/{uuid.uuid4().hex}.json"
    files = {path: _file(doc, "blob-z"), rel: blob}

    def patched(self, session, settings=None, client=None):
        from backend.repositories.disputes import DisputeRepository

        self.session = session
        self.settings = get_settings()
        self.client = FakeGitHubClient(files)
        self.repo = DisputeRepository(session)

    monkeypatch.setattr(GitHubDisputeSource, "__init__", patched)
    synced = await client.post("/github/sync", headers=api_headers)
    assert synced.json()["imported"] == 1
    listed = await client.get("/disputes", headers=api_headers)
    github_rows = [row for row in listed.json() if row.get("source_type") == "github"]
    session_id = github_rows[0]["session_id"]
    started = await client.post(f"/disputes/{session_id}/start", headers=api_headers)
    assert started.status_code == 200
    detail = await client.get(f"/disputes/{session_id}", headers=api_headers)
    case = detail.json()["case"]
    assert case["source"]["source_type"] == "github"
    filing = case.get("filing") or {}
    if filing:
        assert filing.get("simulated") is True
