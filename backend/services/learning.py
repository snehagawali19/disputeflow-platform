"""Persist real dispute outcomes and optionally retrain from those rows only."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import TrainingOutcome
from backend.models.dispute import DisputeCase
from backend.models.ml_predictor import FEATURE_COLUMNS, extract_features, _heuristic, predict_win_probability

MIN_REAL_ROWS = int(os.getenv("MIN_REAL_TRAINING_ROWS", "50"))
REAL_OUTCOMES_CSV = os.path.join(
    os.path.dirname(__file__), "..", "data", "real", "training_outcomes.csv"
)


def real_outcomes_csv_path() -> str:
    return os.path.abspath(REAL_OUTCOMES_CSV)


def outcome_is_win(outcome: str) -> bool | None:
    if outcome == "won":
        return True
    if outcome == "lost":
        return False
    return None


def prediction_log(case: DisputeCase) -> dict:
    features = extract_features(case)
    heuristic_prob, _ = _heuristic(features)
    predicted = case.strategy.win_probability if case.strategy else None
    if predicted is None:
        predicted, _ = predict_win_probability(case)
    return {
        "features": features,
        "predicted_win_probability": float(predicted),
        "heuristic_win_probability": float(heuristic_prob),
        "actual_outcome": case.actual_outcome,
        "note": "Predicted vs recorded outcome. Not a claim of issuer accuracy.",
    }


async def persist_training_outcome(session: AsyncSession, case: DisputeCase) -> TrainingOutcome | None:
    won = outcome_is_win(case.actual_outcome or "")
    if won is None:
        return None
    existing = await session.scalar(select(TrainingOutcome).where(TrainingOutcome.case_id == case.case_id))
    if existing:
        return existing
    payload = prediction_log(case)
    row = TrainingOutcome(
        case_id=case.case_id,
        features_json=json.dumps(payload),
        outcome_won=won,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    logger.info(
        "Recorded real training outcome case={} predicted={:.3f} heuristic={:.3f} actual={}",
        case.case_id,
        payload["predicted_win_probability"],
        payload["heuristic_win_probability"],
        case.actual_outcome,
    )
    return row


async def count_real_outcomes(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(TrainingOutcome)) or 0)


async def export_real_outcomes_csv(session: AsyncSession, path: str | None = None) -> str | None:
    rows = (await session.scalars(select(TrainingOutcome))).all()
    if not rows:
        return None
    dest = path or real_outcomes_csv_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FEATURE_COLUMNS + ["outcome_won"])
        writer.writeheader()
        for row in rows:
            payload = json.loads(row.features_json)
            features = payload.get("features") or payload
            writer.writerow({**{col: features.get(col, 0) for col in FEATURE_COLUMNS}, "outcome_won": int(row.outcome_won)})
    return dest


async def maybe_retrain_from_real_outcomes(session: AsyncSession) -> dict:
    count = await count_real_outcomes(session)
    if count < MIN_REAL_ROWS:
        logger.info("Skipping real-only retrain: {} rows < {}", count, MIN_REAL_ROWS)
        return {"retrained": False, "real_rows": count, "min_required": MIN_REAL_ROWS}
    csv_path = await export_real_outcomes_csv(session)
    if not csv_path:
        return {"retrained": False, "real_rows": count, "min_required": MIN_REAL_ROWS}
    if os.getenv("DISPUTEFLOW_AUTO_RETRAIN", "").lower() not in {"1", "true", "yes"}:
        logger.info("Real outcomes ready at {} ({} rows). Set DISPUTEFLOW_AUTO_RETRAIN=1 to train in-process.", csv_path, count)
        return {"retrained": False, "real_rows": count, "csv": csv_path, "min_required": MIN_REAL_ROWS}
    from backend.models.ml_predictor import train_model

    train_model(csv_path, max_evals=int(os.getenv("HYPEROPT_EVALS", "8")), dataset_kind="real_outcomes")
    return {"retrained": True, "real_rows": count, "csv": csv_path}
