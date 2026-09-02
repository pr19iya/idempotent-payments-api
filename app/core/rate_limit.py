import hashlib
import os
import time

import redis
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://redis:6379/0",
)

RATE_LIMIT_REQUESTS = int(
    os.getenv("RATE_LIMIT_REQUESTS", "100")
)

RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)

        self.redis_client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    async def dispatch(
        self,
        request: Request,
        call_next,
    ) -> Response:
        path = request.url.path

        # Rate-limit merchant API endpoints only.
        if not path.startswith("/v1/"):
            return await call_next(request)

        # Provider webhooks use signature authentication and should
        # not share the merchant API rate limit.
        if path.startswith("/v1/webhooks/"):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")

        if not api_key:
            return await call_next(request)

        identity = hashlib.sha256(
            api_key.encode("utf-8")
        ).hexdigest()[:24]

        window = int(
            time.time() // RATE_LIMIT_WINDOW_SECONDS
        )

        redis_key = (
            f"rate-limit:{identity}:{window}"
        )

        try:
            pipeline = self.redis_client.pipeline()
            pipeline.incr(redis_key)
            pipeline.expire(
                redis_key,
                RATE_LIMIT_WINDOW_SECONDS + 1,
            )
            request_count, _ = pipeline.execute()

        except redis.RedisError:
            # Fail open if Redis is temporarily unavailable.
            return await call_next(request)

        remaining = max(
            RATE_LIMIT_REQUESTS - request_count,
            0,
        )

        reset_seconds = (
            RATE_LIMIT_WINDOW_SECONDS
            - int(time.time()) % RATE_LIMIT_WINDOW_SECONDS
        )

        if request_count > RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "limit": RATE_LIMIT_REQUESTS,
                    "window_seconds": (
                        RATE_LIMIT_WINDOW_SECONDS
                    ),
                    "retry_after_seconds": reset_seconds,
                },
                headers={
                    "Retry-After": str(reset_seconds),
                    "X-RateLimit-Limit": str(
                        RATE_LIMIT_REQUESTS
                    ),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(
                        reset_seconds
                    ),
                },
            )

        response = await call_next(request)

        response.headers["X-RateLimit-Limit"] = str(
            RATE_LIMIT_REQUESTS
        )
        response.headers[
            "X-RateLimit-Remaining"
        ] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(
            reset_seconds
        )

        return response