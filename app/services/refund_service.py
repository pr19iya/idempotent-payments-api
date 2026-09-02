import uuid
from typing import Any

from psycopg.types.json import Jsonb

from app.db import get_connection


def get_refund(refund_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        return connection.execute(
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
            """,
            (refund_id,),
        ).fetchone()


def record_refund_transition(
    refund_id: str,
    event_type: str,
    new_status: str,
    provider_refund_id: str | None = None,
    failure_code: str | None = None,
) -> None:
    with get_connection() as connection:
        refund = connection.execute(
            """
            SELECT
                id,
                payment_id,
                status
            FROM refunds
            WHERE id = %s
            FOR UPDATE
            """,
            (refund_id,),
        ).fetchone()

        if refund is None:
            raise ValueError(
                f"Refund {refund_id} does not exist"
            )

        previous_status = refund["status"]

        valid_transitions = {
            "CREATED": {"PROCESSING"},
            "PROCESSING": {"SUCCEEDED", "FAILED"},
            "SUCCEEDED": set(),
            "FAILED": set(),
        }

        if new_status not in valid_transitions.get(
            previous_status,
            set(),
        ):
            if previous_status == new_status:
                return

            raise ValueError(
                f"Invalid refund transition: "
                f"{previous_status} -> {new_status}"
            )

        connection.execute(
            """
            UPDATE refunds
            SET
                status = %s,
                provider_refund_id =
                    COALESCE(%s, provider_refund_id),
                failure_code = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                new_status,
                provider_refund_id,
                failure_code,
                refund_id,
            ),
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
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                uuid.uuid4(),
                refund_id,
                event_type,
                previous_status,
                new_status,
                Jsonb({}),
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
                refund_id,
                event_type,
                Jsonb(
                    {
                        "refund_id": str(refund_id),
                        "payment_id": str(
                            refund["payment_id"]
                        ),
                        "previous_status": previous_status,
                        "new_status": new_status,
                    }
                ),
            ),
        )


def record_refund_retry(
    refund_id: str,
    reason: str,
) -> None:
    safe_reason = reason[:200]

    with get_connection() as connection:
        refund = connection.execute(
            """
            UPDATE refunds
            SET
                retry_count = retry_count + 1,
                failure_code = %s,
                updated_at = NOW()
            WHERE id = %s
              AND status = 'PROCESSING'
            RETURNING retry_count
            """,
            (safe_reason, refund_id),
        ).fetchone()

        if refund is None:
            return

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
                'REFUND_RETRY_SCHEDULED',
                'PROCESSING',
                'PROCESSING',
                %s
            )
            """,
            (
                uuid.uuid4(),
                refund_id,
                Jsonb(
                    {
                        "reason": safe_reason,
                        "retry_number": refund[
                            "retry_count"
                        ],
                    }
                ),
            ),
        )

def complete_successful_refund(
    refund_id: str,
) -> None:
    """
    After a refund succeeds, mark the payment REFUNDED when its
    entire amount has been refunded. Otherwise return it to SUCCEEDED.
    """

    with get_connection() as connection:
        refund = connection.execute(
            """
            SELECT payment_id
            FROM refunds
            WHERE id = %s
            """,
            (refund_id,),
        ).fetchone()

        if refund is None:
            raise ValueError(
                f"Refund {refund_id} does not exist"
            )

        payment = connection.execute(
            """
            SELECT id, amount_cents, status
            FROM payments
            WHERE id = %s
            FOR UPDATE
            """,
            (refund["payment_id"],),
        ).fetchone()

        if payment is None:
            raise ValueError("Payment does not exist")

        # successful_total = connection.execute(
        #     """
        #     SELECT COALESCE(SUM(amount_cents), 0) AS total
        #     FROM refunds
        #     WHERE payment_id = %s
        #       AND status = 'SUCCEEDED'
        #     """,
        #     (payment["id"],),
        # ).fetchone()["total"]
        successful_total = int(
    connection.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS total
        FROM refunds
        WHERE payment_id = %s
          AND status = 'SUCCEEDED'
        """,
        (payment["id"],),
    ).fetchone()["total"]
)


        if successful_total >= payment["amount_cents"]:
            new_status = "REFUNDED"
            event_type = "PAYMENT_REFUNDED"
        else:
            new_status = "SUCCEEDED"
            event_type = "PAYMENT_PARTIALLY_REFUNDED"

        previous_status = payment["status"]

        connection.execute(
            """
            UPDATE payments
            SET
                status = %s,
                version = version + 1,
                updated_at = NOW()
            WHERE id = %s
            """,
            (new_status, payment["id"]),
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
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                uuid.uuid4(),
                payment["id"],
                event_type,
                previous_status,
                new_status,
                Jsonb(
                    {
                        "refund_id": str(refund_id),
                        "total_refunded_cents": successful_total,
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
                payment["id"],
                event_type,
                Jsonb(
                    {
                        "payment_id": str(payment["id"]),
                        "refund_id": str(refund_id),
                        "status": new_status,
                        "total_refunded_cents": successful_total,
                    }
                ),
            ),
        )


def restore_payment_after_failed_refund(
    refund_id: str,
) -> None:
    """Return a payment to SUCCEEDED after its refund fails."""

    with get_connection() as connection:
        refund = connection.execute(
            """
            SELECT payment_id
            FROM refunds
            WHERE id = %s
            """,
            (refund_id,),
        ).fetchone()

        if refund is None:
            return

        connection.execute(
            """
            UPDATE payments
            SET
                status = 'SUCCEEDED',
                version = version + 1,
                updated_at = NOW()
            WHERE id = %s
              AND status = 'REFUND_PENDING'
            """,
            (refund["payment_id"],),
        )