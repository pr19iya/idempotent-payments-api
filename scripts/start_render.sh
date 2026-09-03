#!/bin/sh

set -eu

APP_PORT="${PORT:-8000}"

export PROVIDER_URL="http://127.0.0.1:8001"
export PAYMENT_API_WEBHOOK_URL="http://127.0.0.1:${APP_PORT}/v1/webhooks/provider"

echo "Applying database migrations..."
python scripts/migrate.py

echo "Starting provider simulator..."
uvicorn provider_simulator.main:app \
    --host 127.0.0.1 \
    --port 8001 &

echo "Starting Celery worker..."
celery -A app.workers.celery_app.celery_app worker \
    --loglevel=info \
    --concurrency=1 &

echo "Starting Celery Beat..."
celery -A app.workers.celery_app.celery_app beat \
    --loglevel=info &

echo "Starting PayFlow API on port ${APP_PORT}..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${APP_PORT}"