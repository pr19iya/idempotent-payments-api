from fastapi import APIRouter, HTTPException, Response, status

from app.db import database_is_ready
from app.services.idempotency import redis_is_ready

import os
from typing import Any

import httpx
import redis
from fastapi import APIRouter, Response, status

from app.core.config import settings
from app.db import get_connection
router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live")
def liveness() -> dict:
    return {
        "status": "alive",
        "service": "payments-api",
    }


@router.get("/ready")
def readiness() -> dict:
    checks = {
        "database": database_is_ready(),
        "redis": redis_is_ready(),
    }

    if not all(checks.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not_ready",
                "checks": checks,
            },
        )

    return {
        "status": "ready",
        "checks": checks,
    }

@router.get("/health/live")
def liveness() -> dict[str, str]:
    """
    Confirms that the API process is running.

    This check does not contact external dependencies.
    """

    return {
        "status": "alive",
        "service": "payment-api",
    }


@router.get("/health/ready")
def readiness(
    response: Response,
) -> dict[str, Any]:
    """
    Confirms that PostgreSQL, Redis and the provider simulator
    are reachable.
    """

    checks: dict[str, str] = {
        "database": "unknown",
        "redis": "unknown",
        "provider": "unknown",
    }

    try:
        with get_connection() as connection:
            connection.execute(
                "SELECT 1"
            ).fetchone()

        checks["database"] = "healthy"
    except Exception:
        checks["database"] = "unhealthy"

    redis_url = os.getenv(
        "REDIS_URL",
        "redis://redis:6379/0",
    )

    try:
        redis_client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        redis_client.ping()
        checks["redis"] = "healthy"
    except redis.RedisError:
        checks["redis"] = "unhealthy"

    try:
        provider_response = httpx.get(
            f"{settings.provider_url}/health",
            timeout=2.0,
        )

        if provider_response.status_code == 200:
            checks["provider"] = "healthy"
        else:
            checks["provider"] = "unhealthy"

    except httpx.HTTPError:
        checks["provider"] = "unhealthy"

    is_ready = all(
        value == "healthy"
        for value in checks.values()
    )

    if not is_ready:
        response.status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
        )

    return {
        "status": "ready" if is_ready else "not_ready",
        "checks": checks,
    }