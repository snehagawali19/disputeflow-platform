"""Retrain the XGBoost model. Default CSV is synthetic unless --real-only."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.ml_predictor import train_model
from backend.services.learning import MIN_REAL_ROWS, export_real_outcomes_csv


async def _real_csv() -> str:
    from backend.db.session import SessionLocal, init_db

    await init_db()
    async with SessionLocal() as session:
        path = await export_real_outcomes_csv(session)
    if not path:
        raise SystemExit("No real recorded outcomes in the database.")
    import pandas as pd

    rows = len(pd.read_csv(path))
    if rows < MIN_REAL_ROWS:
        raise SystemExit(
            f"Refusing real-only retrain: {rows} rows < MIN_REAL_TRAINING_ROWS={MIN_REAL_ROWS}. "
            "Synthetic AUC is not a substitute."
        )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train win-probability model. Default data is synthetic.")
    parser.add_argument("data_path", nargs="?", default=None)
    parser.add_argument("--real-only", action="store_true", help="Train only from recorded issuer outcomes")
    args = parser.parse_args()
    max_evals = int(os.getenv("HYPEROPT_EVALS", "8"))

    if args.real_only:
        path = asyncio.run(_real_csv())
        model = train_model(path, max_evals=max_evals, dataset_kind="real_outcomes")
    else:
        model = train_model(args.data_path, max_evals=max_evals)
    if model:
        print("Model retrained. Check model_manifest.json — metrics are not issuer accuracy unless dataset_kind=real_outcomes.")
    else:
        print("No training data available.")


if __name__ == "__main__":
    main()
