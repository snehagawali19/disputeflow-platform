# DisputeFlow

Demo operations console for payment-dispute *analysis*. Six LangGraph nodes (not a separate supervisor agent) classify a case, score **supplied** facts, draft a rebuttal, and record an **internal simulated filing**. Win probability is an XGBoost model trained on **synthetic heuristic labels**, not issuer outcomes.

This is not a card-network integration, not a document-retrieval system, and not continuous learning.

## What it is / is not

| Claim | Reality |
|---|---|
| Multi-agent pipeline | Yes: intake → evidence → strategy → (HITL) → response → filing → awaiting outcome |
| LangGraph supervisor agent | No separate supervisor LLM. A `StateGraph` routes nodes. |
| Evidence collection | Scores GitHub-imported facts and artifacts only. No OCR, Stripe retrieval, or invented proof. |
| Win probability | Trained on 2000 synthetic rows labeled by a hand-written `win_score`. Manifest metrics are **simulator AUC**, not business accuracy. |
| Filing | Always simulated (`DF-SIM-...`). No Visa/Mastercard/Stripe submit. |
| Feedback / continuous learning | After `POST .../outcome`, an LLM writes lessons on the case. Real rows are stored for later retrain; auto-retrain runs only when enough **real** outcomes exist. |
| Operations dashboard | GitHub sync panel, case queue, stage stepper, human-review gates, JSON case dump. |

## Architecture

- **Intake** — classifies reason, phase, and risk
- **Evidence Assembly** — scores supplied processor/merchant facts and attached artifacts only
- **Strategy** — XGBoost (or heuristic) win % + contest / accept / escalate
- **Response Drafting** — rebuttal from supplied evidence
- **Filing** — package checks + **simulated** internal filing (`DF-SIM-...`)
- **Feedback** — runs only after a human records won / lost / withdrawn

Human review uses LangGraph `interrupt()`. Approve / reject / modify resume the same case. Filing success stops at `awaiting_outcome`.

## Requirements

- Python 3.11
- Node 20+
- A real `GROQ_API_KEY`, `OPENAI_API_KEY`, or `OPENROUTER_API_KEY` (`LLM_PROVIDER=openrouter`) for live LLM calls
- PostgreSQL for Docker/production; SQLite for local/tests
- A GitHub repository containing `disputes/input/*.json` and `disputes/artifacts/**`

## Quick start

1. Copy environment defaults:

   ```bash
   cp .env.example .env
   ```

   Set a real provider key. Change `SECRET_KEY` and `API_KEY` before any shared use.
   Set `GITHUB_OWNER`, `GITHUB_REPO`, and `GITHUB_TOKEN` (Contents: Read).

2. Create a virtualenv and install backend dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Generate **synthetic** training data and train the simulator model:

   ```bash
   python scripts/generate_training_data.py
   python scripts/retrain_model.py
   ```

   Retrain from recorded real outcomes only (refuses if too few rows):

   ```bash
   python scripts/retrain_model.py --real-only
   ```

4. Compare the model to the labeling heuristic (not real-world accuracy):

   ```bash
   python scripts/evaluate_model.py
   ```

5. Generate and validate the synthetic GitHub dispute corpus (20 files):

   ```bash
   python scripts/generate_demo_disputes.py
   python scripts/validate_github_cases.py
   ```

6. Start the API and dashboard:

   ```bash
   python -m backend.main
   cd frontend
   npm install
   npm start
   ```

7. Open http://localhost:3000. The local Vite proxy reads `API_KEY` from
   `.env`; the credential is not compiled into browser JavaScript. Use
   **Sync GitHub cases** — the local create form is disabled when
   `DISPUTE_SOURCE=github`.

   To process one GitHub case at a time, use **Fetch & analyze one**. It selects
   the next valid, unprocessed JSON case, fetches only that case and its
   referenced artifacts, and starts the normal pipeline. The button skips
   cases already running, awaiting review, awaiting outcome, or completed.

## GitHub dispute intake

Production input is a GitHub repository, not the dashboard form and not a
local fixture directory. JSON files under `disputes/input/` are versioned
**demo input**, not live Stripe, Visa, or Mastercard records. OpenRouter
analyzes only those supplied facts; it does not retrieve payment records.
Filing remains an internal `DF-SIM-...` simulation.

### Repository layout

```text
disputes/input/dispute-001.json
...
disputes/input/dispute-020.json
disputes/artifacts/<dispute_id>/invoice.json
```

Each dispute JSON lists artifacts with `repository_path` and a SHA-256 of
the file bytes. Paths must stay inside `disputes/artifacts/`. The importer
never executes repository files.

Expected JSON fields include `dispute_id`, processor identifiers, amounts,
`reason_code`, `dispute_phase`, payment method, verification (AVS/CVV/3DS),
network context, order, optional shipping/communications/refunds/usage, and
`artifacts[]`.

### Token and webhook

Use a fine-grained or classic PAT with **Contents: Read** on the dispute
repo. Set `GITHUB_TOKEN` in `.env` only — never in frontend code.

| Variable | Purpose |
|---|---|
| `DISPUTE_SOURCE=github` | Production source (use `local` only for explicit development) |
| `GITHUB_OWNER` / `GITHUB_REPO` | Repository to list and fetch |
| `GITHUB_REF` | Branch or commit-ish (default `main`) |
| `GITHUB_INPUT_PATH` | Directory of JSON cases (default `disputes/input`) |
| `GITHUB_TOKEN` | Server-side read token |
| `GITHUB_WEBHOOK_SECRET` | HMAC secret for `POST /webhooks/github` |

Webhook setup (GitHub → Settings → Webhooks):

- Payload URL: `https://<your-host>/webhooks/github`
- Content type: `application/json`
- Secret: same as `GITHUB_WEBHOOK_SECRET`
- Events: **Just the push event**

The handler verifies `X-Hub-Signature-256`, accepts only `push`, ignores
pushes that do not touch `disputes/input/**` or `disputes/artifacts/**`,
and is idempotent on `X-GitHub-Delivery`. If the secret is unset, the
webhook returns a safe skip message; use authenticated `POST /github/sync`.

### Manual sync

```bash
curl -X POST http://localhost:8000/github/sync -H "X-API-Key: $API_KEY"
```

Response shape: `{ "imported", "updated", "unchanged", "rejected", "commit_sha" }`.

### Production warnings

- Do not put `GITHUB_TOKEN` or `OPENROUTER_API_KEY` in the browser bundle.
- `POST /disputes` is disabled unless `APP_ENV=test` or `DISPUTE_SOURCE=local`.
- Missing evidence is recorded as gaps; models must not invent tracking, 3DS, or refunds.
- Low-confidence and incomplete cases still require human review.

## Docker production deployment

Set unique production values for `SECRET_KEY` and `API_KEY` in `.env`, and
put the credential in the variable matching `LLM_PROVIDER`. Then run:

```bash
docker compose build
docker compose up -d
```

The backend image builds and embeds the bootstrap XGBoost artifact. Production
rejects heuristic-model fallback; use `python scripts/retrain_model.py
--real-only` only after the minimum real-outcome threshold is reached.
Nginx injects `API_KEY` only while proxying same-origin API and WebSocket
traffic, and the backend is not published directly by Docker Compose.

## API

All routes except `/`, `/health/live`, `/health/ready`, and `POST /webhooks/github`
require `X-API-Key`.

- `POST /github/sync` — import or update cases from GitHub (idempotent)
- `POST /github/fetch-one-and-start` — import one unprocessed case and start its pipeline
- `GET /github/status` — repository, last commit, webhook configuration
- `POST /webhooks/github` — signed GitHub `push` ingestion
- `GET /disputes` — list GitHub-imported (and any allowed local) cases
- `GET /disputes/{id}` — case detail including source metadata
- `POST /disputes/{id}/start` — run the pipeline
- `POST /disputes/{id}/decide` — human approve / reject / modify
- `POST /disputes/{id}/outcome` — record won / lost / withdrawn (once)
- `GET /disputes/{id}/trace` — agent trace and audit chain (includes commit/blob SHA)
- `WS /ws/{id}?api_key=...` — progress stream with snapshot replay
- `POST /disputes` — tests or `DISPUTE_SOURCE=local` only

## Tests

```bash
python scripts/validate_github_cases.py
python scripts/generate_demo_disputes.py
python -m pytest -q
cd frontend && npm test && npm run build
```

## Docker

```bash
docker compose up --build
```

Filing remains an internal simulation and is labeled as such.

## Security assumptions

- One backend replica is supported with in-process WebSocket fan-out
- External card-network filing is intentionally simulated
- Evidence agents never invent delivery, 3DS, or customer-communication proof
- A shipping address is **not** delivery proof
- Production rejects default/weak `SECRET_KEY` and `API_KEY` values
- GitHub tokens and OpenRouter keys are never logged
