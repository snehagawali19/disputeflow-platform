FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Bake the bootstrap XGBoost artifact into the image. Production must not
# depend on a mutable host volume for its model.
RUN python scripts/generate_training_data.py \
    && HYPEROPT_EVALS=8 python scripts/retrain_model.py

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["python", "-m", "uvicorn", "backend.api.routes:app", "--host", "0.0.0.0", "--port", "8000"]
