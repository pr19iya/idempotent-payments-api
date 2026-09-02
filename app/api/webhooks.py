import hashlib
import hmac
import json
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from psycopg.types.json import Jsonb

from app.core.config import settings
from app.db import get_connection


router = APIRouter(
    prefix="/v1/webhooks",
    tags=["Webhooks"],
)


def verify_webhook_signature(
    raw_body: bytes,
    received_signature: str,
) -> bool:
    expected_signature = hmac.new(
        settings.webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected_signature,
        received_signature,
    )


@router.post("/provider")
async def receive_provider_webhook(
    request: Request,
    x_webhook_signature: str = Header(
        ...,
        alias="X-Webhook-Signature",
    ),
) -> dict[str, Any]:
    raw_body = await request.body()

    if not verify_webhook_signature(
        raw_body,
        x_webhook_signature,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc

    required_fields = {
        "event_id",
        "event_type",
        "payment_id",
        "provider_payment_id",
        "status",
    }

    if not required_fields.issubset(payload):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook payload is missing required fields",
        )

    if payload["status"] not in {"SUCCEEDED", "FAILED"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported payment status",
        )

    with get_connection() as connection:
        webhook_row = connection.execute(
            """
            INSERT INTO webhook_events (
                id,
                provider_event_id,
                event_type,
                payload,
                processed,
                processed_at
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                TRUE,
                NOW()
            )
            ON CONFLICT (provider_event_id)
            DO NOTHING
            RETURNING id
            """,
            (
                uuid.uuid4(),
                payload["event_id"],
                payload["event_type"],
                Jsonb(payload),
            ),
        ).fetchone()

        if webhook_row is None:
            return {
                "status": "duplicate_ignored",
                "event_id": payload["event_id"],
            }

        payment = connection.execute(
            """
            SELECT id, status
            FROM payments
            WHERE id = %s
            FOR UPDATE
            """,
            (payload["payment_id"],),
        ).fetchone()

        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Payment not found",
            )

        previous_status = payment["status"]
        new_status = payload["status"]

        if previous_status in {"SUCCEEDED", "REFUNDED"}:
            return {
                "status": "already_finalized",
                "event_id": payload["event_id"],
            }

        if previous_status != "PROCESSING":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot transition payment from "
                    f"{previous_status} to {new_status}"
                ),
            )

        event_type = (
            "PAYMENT_SUCCEEDED"
            if new_status == "SUCCEEDED"
            else "PAYMENT_FAILED"
        )

        connection.execute(
            """
            UPDATE payments
            SET
                status = %s,
                provider_payment_id = %s,
                failure_code = CASE
                    WHEN %s = 'FAILED'
                    THEN 'PROVIDER_WEBHOOK_FAILURE'
                    ELSE NULL
                END,
                version = version + 1,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                new_status,
                payload["provider_payment_id"],
                new_status,
                payload["payment_id"],
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
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                uuid.uuid4(),
                payload["payment_id"],
                event_type,
                previous_status,
                new_status,
                Jsonb(
                    {
                        "source": "provider_webhook",
                        "provider_event_id": payload["event_id"],
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
            VALUES (%s, %s, %s, %s)
            """,
            (
                uuid.uuid4(),
                payload["payment_id"],
                event_type,
                Jsonb(
                    {
                        "payment_id": payload["payment_id"],
                        "status": new_status,
                        "source": "provider_webhook",
                    }
                ),
            ),
        )

    return {
        "status": "processed",
        "event_id": payload["event_id"],
        "payment_status": new_status,
    }