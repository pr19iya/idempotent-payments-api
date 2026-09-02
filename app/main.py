from fastapi import FastAPI

from app.payments import router as payments_router
from app.db import close_pool

app = FastAPI(title="Idempotent Payments API")

app.include_router(payments_router)


@app.on_event("shutdown")
def shutdown():
    close_pool()


@app.get("/health")
def health():
    return {"status": "ok"}
