from contextlib import asynccontextmanager
from app.api.webhooks import router as webhooks_router
from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.payments import router as payments_router
from app.db import close_pool
from app.api.refunds import router as refunds_router
from app.core.middleware import RequestContextMiddleware
from app.core.rate_limit import RateLimitMiddleware
from app.api.metrics import router as metrics_router
from app.core.metrics import setup_metrics
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    close_pool()


app = FastAPI(
    title="PayFlow - Resilient Payments API",
    description=(
        "Payment processing API demonstrating idempotency, "
        "distributed locking and failure-safe processing."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(payments_router)
app.include_router(webhooks_router)

app.include_router(refunds_router)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware)
app.include_router(metrics_router)

setup_metrics(app)


@app.get("/", tags=["Root"])
def root() -> dict:
    return {
        "service": "PayFlow",
        "version": "2.0.0",
        "documentation": "/docs",
    }