import uuid
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status,
)
from psycopg.types.json import Jsonb

from app.core.security import authenticate_merchant
from app.db import get_connection
from app.schemas.refunds import (
    RefundCreateRequest,
    RefundEventsResponse,
    RefundResponse,
)
from app.workers.tasks import process_refund


router = APIRouter(tags=["Refunds"])


@router.post(
    "/v1/payments/{payment_id}/refunds",
    response_model=RefundResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_refund(
    payment_id: uuid.UUID,
    request: RefundCreateRequest,
    merchant_id: Annotated[
        str,
        Depends(authenticate_merchant)
    ],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
        ),
    ],
    x_test_scenario: Annotated[
        Literal[
            "success",
            "declined",
            "timeout",
            "server_error",
        ],
        Header(alias="X-Test-Scenario"),
    ] = "success",
) -> dict:
    with get_connection() as connection:
        existing_refund = connection.execute(
            """
            SELECT
                id,
                payment_id,
                merchant_id,
                idempotency_key,
                amount_cents,
                currency,
                status,
                provider_refund_id,
                failure_code,
                retry_count,
                created_at,
                updated_at
            FROM refunds
            WHERE merchant_id = %s
              AND idempotency_key = %s
            """,
            (merchant_id, idempotency_key),
        ).fetchone()

        if existing_refund is not None:
            return {
                **existing_refund,
                "duplicate": True,
            }

        payment = connection.execute(
            """
            SELECT
                id,
                merchant_id,
                amount_cents,
                currency,
                status,
                provider_payment_id
            FROM payments
            WHERE id = %s
              AND merchant_id = %s
            FOR UPDATE
            """,
            (payment_id, merchant_id),
        ).fetchone()

        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payment not found",
            )

        if payment["status"] != "SUCCEEDED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Only a succeeded payment can be refunded"
                ),
            )

        if payment["provider_payment_id"] is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment has no provider payment ID",
            )

        refund_totals = connection.execute(
            """
            SELECT
                COALESCE(
                    SUM(amount_cents)
                    FILTER (
                        WHERE status IN (
                            'CREATED',
                            'PROCESSING',
                            'SUCCEEDED'
                        )
                    ),
                    0
                ) AS reserved_amount
            FROM refunds
            WHERE payment_id = %s
            """,
            (payment_id,),
        ).fetchone()

        reserved_amount = refund_totals[
            "reserved_amount"
        ]
        refundable_amount = (
            payment["amount_cents"] - reserved_amount
        )

        if request.amount_cents > refundable_amount:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": (
                        "Refund amount exceeds refundable balance"
                    ),
                    "requested_amount_cents": (
                        request.amount_cents
                    ),
                    "refundable_amount_cents": (
                        refundable_amount
                    ),
                },
            )

        refund_id = uuid.uuid4()

        refund = connection.execute(
            """
            INSERT INTO refunds (
                id,
                payment_id,
                merchant_id,
                idempotency_key,
                amount_cents,
                currency,
                status
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'CREATED'
            )
            RETURNING
                id,
                payment_id,
                merchant_id,
                idempotency_key,
                amount_cents,
                currency,
                status,
                provider_refund_id,
                failure_code,
                retry_count,
                created_at,
                updated_at
            """,
            (
                refund_id,
                payment_id,
                merchant_id,
                idempotency_key,
                request.amount_cents,
                payment["currency"],
            ),
        ).fetchone()

        connection.execute(
            """
            UPDATE payments
            SET
                status = 'REFUND_PENDING',
                version = version + 1,
                updated_at = NOW()
            WHERE id = %s
            """,
            (payment_id,),
        )

        connection.execute(
            """
            INSERT INTO refund_events (
                id,
                refund_id,
                event_type,
                previous_status,
                new_status,
                metadata
            )
            VALUES (
                %s,
                %s,
                'REFUND_CREATED',
                NULL,
                'CREATED',
                %s
            )
            """,
            (
                uuid.uuid4(),
                refund_id,
                Jsonb(
                    {
                        "amount_cents": request.amount_cents,
                    }
                ),
            ),
        )

        connection.execute(
            """
            INSERT INTO payment_events (
                id,
                payment_id,
                event_type,
                previous_status,
                new_status,
                metadata
            )
            VALUES (
                %s,
                %s,
                'PAYMENT_REFUND_PENDING',
                'SUCCEEDED',
                'REFUND_PENDING',
                %s
            )
            """,
            (
                uuid.uuid4(),
                payment_id,
                Jsonb(
                    {
                        "refund_id": str(refund_id),
                        "amount_cents": request.amount_cents,
                    }
                ),
            ),
        )

        connection.execute(
            """
            INSERT INTO outbox_events (
                id,
                aggregate_id,
                event_type,
                payload
            )
            VALUES (
                %s,
                %s,
                'REFUND_CREATED',
                %s
            )
            """,
            (
                uuid.uuid4(),
                refund_id,
                Jsonb(
                    {
                        "refund_id": str(refund_id),
                        "payment_id": str(payment_id),
                        "amount_cents": request.amount_cents,
                    }
                ),
            ),
        )

    process_refund.delay(
        str(refund_id),
        x_test_scenario,
    )

    return {
        **refund,
        "duplicate": False,
    }


@router.get(
    "/v1/refunds/{refund_id}",
    response_model=RefundResponse,
)
def retrieve_refund(
    refund_id: uuid.UUID,
    merchant_id: Annotated[
        str,
        Depends(authenticate_merchant)
    ],
) -> dict:
    with get_connection() as connection:
        refund = connection.execute(
            """
            SELECT
                id,
                payment_id,
                merchant_id,
                idempotency_key,
                amount_cents,
                currency,
                status,
                provider_refund_id,
                failure_code,
                retry_count,
                created_at,
                updated_at
            FROM refunds
            WHERE id = %s
              AND merchant_id = %s
            """,
            (refund_id, merchant_id),
        ).fetchone()

    if refund is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Refund not found",
        )

    return {
        **refund,
        "duplicate": False,
    }


@router.get(
    "/v1/refunds/{refund_id}/events",
    response_model=RefundEventsResponse,
)
def retrieve_refund_events(
    refund_id: uuid.UUID,
    merchant_id: Annotated[
        str,
        Depends(authenticate_merchant)
    ],
) -> dict:
    with get_connection() as connection:
        refund = connection.execute(
            """
            SELECT id
            FROM refunds
            WHERE id = %s
              AND merchant_id = %s
            """,
            (refund_id, merchant_id),
        ).fetchone()

        if refund is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Refund not found",
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
            FROM refund_events
            WHERE refund_id = %s
            ORDER BY created_at ASC
            """,
            (refund_id,),
        ).fetchall()

    return {
        "refund_id": refund_id,
        "events": events,
    }