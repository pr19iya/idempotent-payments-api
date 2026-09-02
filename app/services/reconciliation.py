import uuid

from app.db import get_connection
from app.services.provider_client import (
    PermanentProviderError,
    TransientProviderError,
    get_provider_payment,
)


def reconcile_processing_payments() -> dict:
    checked = 0
    corrected = 0
    errors = 0

    with get_connection() as connection:
        payments = connection.execute(
            """
            SELECT
                id,
                status,
                provider_payment_id
            FROM payments
            WHERE status = 'PROCESSING'
              AND provider_payment_id IS NOT NULL
              AND updated_at < NOW() - INTERVAL '10 seconds'
            ORDER BY updated_at ASC
            LIMIT 100
            """
        ).fetchall()

    for payment in payments:
        checked += 1

        try:
            provider_payment = get_provider_payment(
                payment["provider_payment_id"]
            )
        except (
            TransientProviderError,
            PermanentProviderError,
        ):
            errors += 1
            continue

        provider_status = provider_payment["status"]

        if provider_status not in {"SUCCEEDED", "FAILED"}:
            continue

        with get_connection() as connection:
            current = connection.execute(
                """
                SELECT status
                FROM payments
                WHERE id = %s
                FOR UPDATE
                """,
                (payment["id"],),
            ).fetchone()

            if current is None or current["status"] != "PROCESSING":
                continue

            event_type = (
                "PAYMENT_RECONCILED_SUCCEEDED"
                if provider_status == "SUCCEEDED"
                else "PAYMENT_RECONCILED_FAILED"
            )

            connection.execute(
                """
                UPDATE payments
                SET
                    status = %s,
                    failure_code = CASE
                        WHEN %s = 'FAILED'
                        THEN 'RECONCILED_PROVIDER_FAILURE'
                        ELSE NULL
                    END,
                    version = version + 1,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    provider_status,
                    provider_status,
                    payment["id"],
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
                    'PROCESSING',
                    %s,
                    jsonb_build_object(
                        'source', 'reconciliation'
                    )
                )
                """,
                (
                    uuid.uuid4(),
                    payment["id"],
                    event_type,
                    provider_status,
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
                    %s,
                    jsonb_build_object(
                        'payment_id', %s::text,
                        'status', %s::text,
                        'source', 'reconciliation'
                    )
                )
                """,
                (
                    uuid.uuid4(),
                    payment["id"],
                    event_type,
                    payment["id"],
                    provider_status,
                ),
            )

            corrected += 1

    return {
        "checked": checked,
        "corrected": corrected,
        "errors": errors,
    }