from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from pydantic import BaseModel, Field, field_validator
from fastapi import HTTPException
from app.db import get_connection
from app.core.security import authenticate_merchant
from app.services.payment_service import create_idempotent_payment
from typing import Literal
from app.workers.tasks import process_payment

router = APIRouter(prefix="/v1/payments", tags=["Payments"])


class PaymentRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    amount_cents: int = Field(gt=0, le=100_000_000)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class PaymentResponse(BaseModel):
    id: UUID
    merchant_id: str
    idempotency_key: str
    user_id: str
    amount_cents: int
    currency: str
    status: Literal[
        "CREATED",
        "PROCESSING",
        "SUCCEEDED",
        "FAILED",
        "REFUND_PENDING",
        "REFUNDED",
    ]
    provider_payment_id: str | None
    retry_count: int
    created_at: datetime
    duplicate: bool


@router.post(
    "",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_payment(
    request: PaymentRequest,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
    ),
    merchant_id: str = Depends(authenticate_merchant),
    x_test_scenario: Literal[
    "success",
    "declined",
    "timeout",
    "server_error",
    "delayed_success",
    "delayed_webhook",
    "duplicate_webhook"
    "lost_webhook"
] = Header(default="success", alias="X-Test-Scenario"),

) -> PaymentResponse:
    payment, duplicate = create_idempotent_payment(
        merchant_id=merchant_id,
        idempotency_key=idempotency_key,
        user_id=request.user_id,
        amount_cents=request.amount_cents,
        currency=request.currency,
    )
    if not duplicate:
        process_payment.delay(
        str(payment["id"]),
        x_test_scenario,
    )

    if duplicate:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    else:
        response.headers["Idempotent-Replayed"] = "false"

    return PaymentResponse(**payment)




@router.get("/{payment_id}")
def retrieve_payment(
    payment_id: UUID,
    merchant_id: str = Depends(authenticate_merchant),
) -> dict:
    with get_connection() as connection:
        payment = connection.execute(
            """
            SELECT
                id,
                merchant_id,
                idempotency_key,
                user_id,
                amount_cents,
                currency,
                status,
                provider_payment_id,
                failure_code,
                retry_count,
                version,
                created_at,
                updated_at
            FROM payments
            WHERE id = %s
              AND merchant_id = %s
            """,
            (payment_id, merchant_id),
        ).fetchone()

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    return payment


@router.get("/{payment_id}/events")
def retrieve_payment_events(
    payment_id: UUID,
    merchant_id: str = Depends(authenticate_merchant),
) -> dict:
    with get_connection() as connection:
        payment = connection.execute(
            """
            SELECT id
            FROM payments
            WHERE id = %s
              AND merchant_id = %s
            """,
            (payment_id, merchant_id),
        ).fetchone()

        if payment is None:
            raise HTTPException(
                status_code=404,
                detail="Payment not found",
            )

        events = connection.execute(
            """
            SELECT
                id,
                event_type,
                previous_status,
                new_status,
                metadata,
                created_at
            FROM payment_events
            WHERE payment_id = %s
            ORDER BY created_at ASC
            """,
            (payment_id,),
        ).fetchall()

    return {
        "payment_id": payment_id,
        "events": events,
    }