# DisputeFlow Implementation Report

Date: 2026-09-15
Workspace: E:\\disputeflow
LLM provider: OpenRouter
Configured GitHub repository: https://github.com/IvayloP0709/dispute-automation

## Executive summary

DisputeFlow is a FastAPI, LangGraph, XGBoost, and React application for payment-dispute analysis. It classifies a dispute, evaluates supplied evidence, estimates win probability, drafts a rebuttal, validates a simulated filing package, pauses for human approval when required, and records a later outcome for feedback.

The application is deliberately conservative. It does not submit to Visa, Mastercard, Stripe, or another card network. It does not invent delivery proof, customer communication, authentication, refund, or transaction evidence. The bootstrap XGBoost model is trained on synthetic heuristic labels and is not a measure of real issuer accuracy.

The GitHub repository is currently fetched as a read-only reference by scripts/fetch_github_reference.py. It is an automation program, not a dispute-data repository. Its own pipeline obtains live records from Stripe and enriches them with PostHog, Resend, Gmail, invoices, terms, and pricing. Therefore, the current system does not pretend that repository source code is live case input.

## Architecture

Backend components:

- FastAPI routes in backend/api/routes.py.
- Pydantic request and domain models.
- SQLAlchemy persistence with SQLite for local development/tests and PostgreSQL for Docker/production.
- LangGraph state machine in backend/workflows/graph.py.
- Durable workflow orchestration and human-review resume logic in backend/services/workflow.py.
- Six main stages: intake, evidence assembly, strategy formulation, human review, response drafting, and filing.
- Feedback analysis after a real outcome is recorded.
- Hash-chained audit entries.
- XGBoost model prediction.
- OpenRouter structured JSON calls with retries and Pydantic validation.
- API-key authentication, rate limiting, and WebSocket authentication.
- Live and readiness health endpoints.

Frontend components:

- React and TypeScript dashboard.
- Vite development server.
- Nginx production container.
- Midnight Signal dark intelligence-dashboard design.
- Searchable case queue.
- Evidence, strategy, timeline, filing, diagnostics, and outcome panels.
- WebSocket progress updates.
- Human review controls.
- Raw-record disclosure for audit/debugging.

Runtime flow:

Browser → Vite or Nginx → authenticated API/WebSocket proxy → FastAPI → database, XGBoost, and OpenRouter.

Docker Compose keeps the backend internal to the Compose network. The frontend proxy injects the API key server-side so the key is not compiled into browser JavaScript.

## Backend execution flow

### Case creation

POST /disputes validates amount, currency, reason, phase, payment method, merchant/customer fields, authentication flags, typed artifacts, and optional idempotency key. It stores the case as open with pipeline status created.

Transaction facts and evidence are kept distinct. A shipping address is not delivery proof.

### Intake

The intake agent classifies reason, phase, risk, urgency, and summary using OpenRouter structured output. If the LLM is unavailable, a conservative local classification is stored and the error is added to the case.

### Evidence assembly

The evidence stage processes only facts and artifacts already supplied to the case. It does not silently retrieve data from GitHub, Stripe, PostHog, Resend, or Gmail.

Deterministic checks identify gaps. For product-not-received disputes, delivery evidence must contain confirmed delivery plus tracking, carrier/date, or signature details. False/negative 3DS is not positive authentication. A shipping address alone does not satisfy delivery proof.

LLM failure now creates a bounded diagnostic, preserves the supplied evidence inventory, sets an evidence-origin human-review gate, and stops before unsupported strategy execution.

### Strategy formulation

XGBoost generates a win probability and confidence. OpenRouter generates a structured recommendation. A deterministic policy guard then overrides unsafe recommendations.

Human review is forced when confidence is below 35%, evidence gaps remain, reason-critical evidence is missing, risk is high/critical, the phase is pre-arbitration/arbitration, or the amount exceeds the review threshold.

For example, a product-not-received case with 10% confidence and no delivery proof cannot remain contest. It is changed to escalate_to_human with reasoning that probability cannot replace missing factual proof.

### Human review

Every pause is persisted with timestamp, origin, stage, probability, risk, open state, and later decision. The dashboard can approve, re-analyze, or reject.

Approval resumes the same case. Evidence-origin approval continues to strategy. Strategy-origin approval continues to response drafting. Filing-origin approval can create a human override of the simulated filing.

### Response drafting

The response agent receives only supplied case facts, strategy arguments, and evidence. Prompts prohibit model metrics, synthetic-training language, invented tracking/signatures/communications, unsupported delivery claims, and unjustified wording such as proves.

Generated responses are validated. A response exposing model probability/confidence or unsupported delivery is rejected and routed to human review.

### Filing

The filing agent checks reason-specific evidence and response requirements. It records an internal simulated FilingRecord with a DF-SIM identifier only when validation succeeds.

Successful simulation ends at awaiting_outcome. A rejected package creates a filing-origin human-review gate. Human override remains explicitly simulated and never represents an external processor confirmation.

### Outcome and feedback

POST /disputes/{session_id}/outcome accepts won, lost, or withdrawn. The feedback agent extracts lessons. Real outcomes are stored for later training only after the configured minimum real-row threshold. The system never fabricates an outcome to force completion.

## Frontend behavior

The header shows branding, system status, refresh, and new-case controls.

The hero area shows the operations command center, controlled execution, filing simulation, and active human-review gates.

The metric strip shows open cases, review queue, selected probability, and evidence-record count.

The current create composer accepts amount, reason, merchant, customer email, phase, payment rail, optional delivery text, AVS, CVV, and 3DS. Delivery text becomes a typed artifact only when explicitly supplied. This form remains because no live case-data connector or GitHub case manifest has yet been selected.

The case queue supports search and selection. The analysis workspace shows case exposure, truth bar, simulation status, model/data disclosures, resolution timeline, evidence gaps, strategy, telemetry, filing, diagnostics, and outcome actions.

Legacy database records containing oversized traces or unsafe model-metric drafts are compacted or withheld in the normal UI while remaining available in the raw audit record.

WebSocket snapshot replay is processed as one batch. It no longer refreshes the same case once for every historical event.

## OpenRouter integration

The shared LLM helper:

1. Creates the OpenRouter-compatible client.
2. Uses JSON mode.
3. Includes the Pydantic schema in the prompt.
4. Rejects markdown, duplicate fields, and trailing text.
5. Retries on failure.
6. Validates JSON with Pydantic.
7. Stores only a bounded single-line diagnostic after failure.

A live OpenRouter structured-output smoke test succeeded.

## XGBoost

Training commands:

    python scripts/generate_training_data.py
    python scripts/retrain_model.py

The bootstrap dataset contains synthetic heuristic labels. It is for workflow testing, not real-world accuracy.

Features include amount, evidence count, relevance, reason, payment method, AVS, CVV, 3DS, risk, verified delivery, and positive authentication.

The Docker image trains and embeds the artifact. Production rejects heuristic fallback. Real outcome rows can be used for retraining only after the minimum configured threshold is reached.

## GitHub integration

scripts/fetch_github_reference.py:

- reads GITHUB_REPO_URL and GITHUB_TOKEN;
- never prints or stores the token;
- validates the GitHub URL;
- downloads through the GitHub API;
- safely extracts archives and blocks path traversal;
- stages extraction temporarily;
- protects an existing reference unless --refresh is supplied.

The configured repository documents a pipeline that finds open Stripe disputes, fetches customer/payment history, downloads invoices, queries PostHog and Resend, searches Gmail, obtains terms/pricing, drafts a rebuttal, waits for approval, and optionally submits to Stripe.

The repository does not contain a committed dispute case-data manifest. The local environment currently has GitHub and OpenRouter credentials, but the Stripe, PostHog, Resend, and Gmail connector credentials are missing or placeholders.

Consequently, a truthful live execution of that repository pipeline cannot complete using only GitHub and OpenRouter credentials. OpenRouter provides reasoning, not transaction data. The application must either receive real connector credentials or consume a versioned JSON case manifest committed to the GitHub repository.

The application intentionally does not execute mutable downloaded remote code on every run because that would introduce a supply-chain and remote-code-execution risk.

## API routes

Unauthenticated:

- GET /
- GET /health/live
- GET /health/ready

Authenticated:

- POST /disputes
- GET /disputes
- GET /disputes/{session_id}
- POST /disputes/{session_id}/start
- POST /disputes/{session_id}/decide
- POST /disputes/{session_id}/outcome
- GET /disputes/{session_id}/trace

WebSocket:

- WS /ws/{session_id}
- Direct connections may use an API-key query parameter.
- Vite/Nginx proxy connections use a server-side X-API-Key header.

## Security and reliability changes

- Weak production secrets are rejected.
- Production heuristic fallback is rejected.
- API-key comparison is constant-time.
- Request rate limiting and request-size limits are enabled.
- GitHub archive extraction is path-traversal safe.
- GitHub tokens are not placed in URLs or logs.
- API keys are not compiled into frontend JavaScript.
- Backend is not directly published by Docker Compose.
- LLM errors and new evidence/trace fields are bounded.
- Deterministic evidence gates prevent unsupported claims.
- Human review is mandatory for uncertainty and missing evidence.
- Filing remains labeled as simulation.
- Audit entries use hash chaining.
- External filing is never triggered by an exception.

## Errors encountered and resolutions

### Malformed OpenRouter output

The pasted cases contained huge malformed JSON/structured-output errors. The old behavior preserved too much raw model output and continued with fallback scoring.

Resolved with JSON mode, explicit schemas, retries, Pydantic validation, bounded diagnostics, and evidence-origin human review.

### Static form facts looked like collected evidence

The original form produced transaction-based evidence, making cases appear populated without delivery, communications, invoice, or customer-history records.

Resolved by labeling supplied facts correctly, requiring explicit artifacts, adding deterministic gaps, and refusing to invent external evidence.

### Contradictory strategy

A case had approximately 10% confidence but recommended contest despite missing delivery evidence.

Resolved with the deterministic strategy policy guard and explicit human-review reasoning.

### Missing persisted approval

A workflow could stop after strategy without a persisted open approval record.

Resolved by saving the approval gate when a node requests human review.

### API key compiled into frontend

The initial Docker/Vite setup could expose the shared key in browser JavaScript or build a mismatched frontend key.

Resolved with Vite and Nginx server-side proxy injection.

### WebSocket refresh storm

Historical snapshot replay caused one case-detail request per event.

Resolved by processing snapshots as a single batch.

### Legacy oversized records

Old database rows may still contain huge traces or unsafe drafts.

Resolved with frontend compaction and legacy-draft withholding. Raw records remain for audit.

### Frontend test mismatch

The test expected Filing simulated on the initial screen, but it was initially shown only after selecting a case.

Resolved by placing the simulation disclosure in the hero trust note.

### Visual screenshot duplication

A full-page stitched screenshot appeared to duplicate the empty analysis panel. The live DOM contained one panel; the duplication was a screenshot-stitching artifact.

### Windows port conflicts

Port 8000 was already in use, causing socket error 10048. Vite also selected alternate ports when stale servers existed.

Resolved by stopping stale processes and using the actual printed server port.

### Health route mismatch

GET /health returned 404. The valid routes are /health/live and /health/ready.

### Docker BuildKit filesystem issue

The local Docker engine previously failed during image build because of a read-only/permission issue in BuildKit. Docker Compose configuration was validated, but a healthy Docker engine is still needed for a complete image-build verification.

### Pytest temporary-directory permissions

Pytest initially completed tests but returned an error while cleaning Windows temporary directories.

Resolved by rerunning with approved elevated execution. Temporary verification directories were removed.

## Verification results

Latest recorded verification:

- Backend: 22 passed, 1 skipped.
- Frontend: 2 passed.
- Frontend production build: successful.
- OpenRouter live structured-output smoke test: successful.
- Docker Compose configuration: valid.
- API proxy request: HTTP 200.
- Direct backend request without API key: HTTP 401.
- WebSocket through proxy: connected.
- Desktop layout: verified at 1440 pixels.
- Mobile layout: verified at 390 pixels.
- Horizontal overflow: none.
- Browser console errors: none.

Two non-blocking third-party deprecation warnings remain: Hyperopt imports deprecated pkg_resources, and Starlette references a deprecated AnyIO BlockingPortal alias.

## Running locally

Copy .env.example to .env and set LLM_PROVIDER=openrouter, OPENROUTER_API_KEY, unique SECRET_KEY, unique API_KEY, GITHUB_REPO_URL, and GITHUB_TOKEN.

Backend:

    cd E:\disputeflow
    .\\.venv\\Scripts\\python.exe -m backend.main

Frontend in another terminal:

    cd E:\disputeflow\\frontend
    npm install
    npm run start

Open http://localhost:3000.

Docker:

    cd E:\disputeflow
    docker compose build
    docker compose up -d

## Production readiness

Ready:

- application structure;
- API and WebSocket authentication;
- database persistence;
- OpenRouter integration;
- XGBoost artifact build;
- human-review routing;
- deterministic evidence policy;
- simulated filing flow;
- responsive dashboard;
- proxy authentication;
- core tests.

Still required before live financial operations:

- Stripe live connector;
- optional PostHog, Resend, and Gmail connectors;
- a committed GitHub case-data manifest or approved live data contract;
- real issuer-labeled training data;
- external filing adapter;
- TLS, secret management, backups, monitoring, and operational access controls;
- successful Docker image build on a healthy Docker engine.

The next valid implementation choice is either real connector mode with a restricted Stripe key and optional evidence-source credentials, or GitHub-only JSON mode with a versioned dispute manifest in the repository.

