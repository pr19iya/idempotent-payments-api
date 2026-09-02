FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system appgroup \
    && adduser \
    --system \
    --ingroup appgroup \
    --home /home/appuser \
    appuser

COPY requirements.txt .

RUN python -m pip install --upgrade pip \
    && python -m pip install \
    --no-cache-dir \
    -r requirements.txt

COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8000 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]