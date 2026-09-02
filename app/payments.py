from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_pool

router = APIRouter()


class PaymentRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    amount_cents: int = Field(..., gt=0)


class PaymentResponse(BaseModel):
    id: int
    idempotency_key: str
    user_id: str
    amount_cents: int
    status: str
    duplicate: bool  # True if this request was a RETRY, not a new charge


# ============================================================
# THE NAIVE (BROKEN) VERSION -- read this first, don't run it.
# ============================================================
#
# def create_payment_naive(req: PaymentRequest):
#     pool = get_pool()
#     with pool.connection() as conn:
#         # Step 1: check if this key already exists
#         existing = conn.execute(
#             "SELECT id FROM payments WHERE idempotency_key = %s",
#             (req.idempotency_key,),
#         ).fetchone()
#         if existing:
#             return {"duplicate": True, ...}
#
#         # Step 2: insert the new payment
#         conn.execute(
#             "INSERT INTO payments (idempotency_key, user_id, amount_cents) "
#             "VALUES (%s, %s, %s)",
#             (req.idempotency_key, req.user_id, req.amount_cents),
#         )
#
#     # THE BUG: Step 1 and Step 2 are TWO separate round-trips to the
#     # database. If two requests carrying the SAME idempotency_key
#     # arrive close together, BOTH can run Step 1 and see "doesn't
#     # exist" before EITHER has finished Step 2. Both then proceed to
#     # insert -- resulting in a double charge (or an integrity error,
#     # if you're not handling that properly). This is the exact bug
#     # this whole project exists to fix.


# ============================================================
# THE FIXED VERSION -- one atomic database operation
# ============================================================
#
# Instead of "check, then insert" (two steps, with a gap between them
# where another request can sneak in), we do both in ONE atomic
# operation using Postgres's "INSERT ... ON CONFLICT DO NOTHING".
# Postgres guarantees no other query can interleave in the middle of
# this -- so the race condition is closed at the database level,
# without us needing to write manual locks.
@router.post("/payments", response_model=PaymentResponse, status_code=201)
def create_payment(req: PaymentRequest):
    pool = get_pool()
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO payments (idempotency_key, user_id, amount_cents)
            VALUES (%s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id, idempotency_key, user_id, amount_cents, status
            """,
            (req.idempotency_key, req.user_id, req.amount_cents),
        ).fetchone()

        if row is not None:
            # The insert actually happened -- this is a brand new payment.
            id_, key, user_id, amount, status = row
            return PaymentResponse(
                id=id_, idempotency_key=key, user_id=user_id,
                amount_cents=amount, status=status, duplicate=False,
            )

        # row is None means ON CONFLICT triggered: a payment with this
        # key already exists. This is a RETRY, not a new payment.
        # Fetch and return the ORIGINAL payment so the client gets a
        # consistent response instead of an error or a second charge.
        existing = conn.execute(
            """
            SELECT id, idempotency_key, user_id, amount_cents, status
            FROM payments WHERE idempotency_key = %s
            """,
            (req.idempotency_key,),
        ).fetchone()

        if existing is None:
            raise HTTPException(status_code=500, detail="internal error")

        id_, key, user_id, amount, status = existing
        return PaymentResponse(
            id=id_, idempotency_key=key, user_id=user_id,
            amount_cents=amount, status=status, duplicate=True,
        )
