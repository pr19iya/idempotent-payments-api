import uuid
from typing import Any

from fastapi import HTTPException, status

from app.db import get_connection
from app.services.idempotency import (
    acquire_payment_lock,
    generate_request_hash,
)


def serialize_payment(row: dict[str, Any], duplicate: bool) -> dict[str, Any]:
    return {
        "id": row["id"],
        "merchant_id": row["merchant_id"],
        "idempotency_key": row["idempotency_key"],
        "user_id": row["user_id"],
        "amount_cents": row["amount_cents"],
        "currency": row["currency"],
        "status": row["status"],
        "provider_payment_id": row["provider_payment_id"],
        "retry_count": row["retry_count"],
        "created_at": row["created_at"],
        "duplicate": duplicate,
    }


def get_existing_payment(
    merchant_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                id,
                merchant_id,
                idempotency_key,
                request_hash,
                user_id,
                amount_cents,
                currency,
                status,
                provider_payment_id,
                retry_count,
                created_at
            FROM payments
            WHERE merchant_id = %s
              AND idempotency_key = %s
            """,
            (merchant_id, idempotency_key),
        ).fetchone()


def create_idempotent_payment(
    merchant_id: str,
    idempotency_key: str,
    user_id: str,
    amount_cents: int,
    currency: str,
) -> tuple[dict[str, Any], bool]:
    request_hash = generate_request_hash(
        user_id=user_id,
        amount_cents=amount_cents,
        currency=currency,
    )

    with acquire_payment_lock(merchant_id, idempotency_key) as lock_acquired:
        if not lock_acquired:
            existing = get_existing_payment(
                merchant_id=merchant_id,
                idempotency_key=idempotency_key,
            )

            if existing is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Another request with this idempotency key is processing",
                )

            if existing["request_hash"] != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Idempotency key was already used "
                        "with a different request"
                    ),
                )

            return serialize_payment(existing, duplicate=True), True

        payment_id = uuid.uuid4()
        event_id = uuid.uuid4()

        with get_connection() as connection:
            inserted = connection.execute(
                """
                INSERT INTO payments (
                    id,
                    merchant_id,
                    idempotency_key,
                    request_hash,
                    user_id,
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
                    %s,
                    'CREATED'
                )
                ON CONFLICT (merchant_id, idempotency_key)
                DO NOTHING
                RETURNING
                    id,
                    merchant_id,
                    idempotency_key,
                    request_hash,
                    user_id,
                    amount_cents,
                    currency,
                    status,
                    provider_payment_id,
                    retry_count,
                    created_at
                """,
                (
                    payment_id,
                    merchant_id,
                    idempotency_key,
                    request_hash,
                    user_id,
                    amount_cents,
                    currency.upper(),
                ),
            ).fetchone()

            if inserted is not None:
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
                        'PAYMENT_CREATED',
                        NULL,
                        'CREATED',
                        '{}'::jsonb
                    )
                    """,
                    (event_id, payment_id),
                )

                return serialize_payment(inserted, duplicate=False), False

            existing = connection.execute(
                """
                SELECT
                    id,
                    merchant_id,
                    idempotency_key,
                    request_hash,
                    user_id,
                    amount_cents,
                    currency,
                    status,
                    provider_payment_id,
                    retry_count,
                    created_at
                FROM payments
                WHERE merchant_id = %s
                  AND idempotency_key = %s
                """,
                (merchant_id, idempotency_key),
            ).fetchone()

            if existing is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Unable to retrieve the existing payment",
                )

            if existing["request_hash"] != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Idempotency key was already used "
                        "with a different request"
                    ),
                )

            return serialize_payment(existing, duplicate=True), True