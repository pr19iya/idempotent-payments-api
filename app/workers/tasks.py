import uuid
from typing import Any

from app.db import get_connection
from app.services.provider_client import (
    PermanentProviderError,
    TransientProviderError,
    send_payment_to_provider,
    send_refund_to_provider,
)

from app.services.refund_service import (
    complete_successful_refund,
    get_refund,
    record_refund_retry,
    record_refund_transition,
    restore_payment_after_failed_refund,
)
from app.services.outbox_publisher import (
    publish_pending_outbox_events,
)




from app.workers.celery_app import celery_app
from app.services.reconciliation import reconcile_processing_payments

def get_payment(payment_id: str) -> dict[str, Any] | None:
    """Retrieve the latest payment information."""

    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                id,
                amount_cents,
                currency,
                status,
                retry_count
            FROM payments
            WHERE id = %s
            """,
            (payment_id,),
        ).fetchone()


def record_transition(
    payment_id: str,
    event_type: str,
    new_status: str,
    provider_payment_id: str | None = None,
    failure_code: str | None = None,
) -> None:
    """
    Update a payment's state and atomically record its ledger
    and outbox events.
    """

    with get_connection() as connection:
        payment = connection.execute(
            """
            SELECT status
            FROM payments
            WHERE id = %s
            FOR UPDATE
            """,
            (payment_id,),
        ).fetchone()

        if payment is None:
            raise ValueError(f"Payment {payment_id} does not exist")

        previous_status = payment["status"]

        valid_transitions = {
            "CREATED": {"PROCESSING"},
            "PROCESSING": {"SUCCEEDED", "FAILED"},
            "FAILED": {"PROCESSING"},
            "SUCCEEDED": {"REFUND_PENDING"},
            "REFUND_PENDING": {"REFUNDED", "SUCCEEDED"},
            "REFUNDED": set(),
        }

        if new_status not in valid_transitions.get(
            previous_status,
            set(),
        ):
            # Receiving the same final result again should not create
            # another ledger event.
            if previous_status == new_status:
                return

            raise ValueError(
                f"Invalid payment transition: "
                f"{previous_status} -> {new_status}"
            )

        connection.execute(
            """
            UPDATE payments
            SET
                status = %s,
                provider_payment_id =
                    COALESCE(%s, provider_payment_id),
                failure_code = %s,
                version = version + 1,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                new_status,
                provider_payment_id,
                failure_code,
                payment_id,
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
                '{}'::jsonb
            )
            """,
            (
                uuid.uuid4(),
                payment_id,
                event_type,
                previous_status,
                new_status,
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
                    'previous_status', %s::text,
                    'new_status', %s::text
                )
            )
            """,
            (
                uuid.uuid4(),
                payment_id,
                event_type,
                payment_id,
                previous_status,
                new_status,
            ),
        )


def record_awaiting_webhook(
    payment_id: str,
    provider_payment_id: str,
) -> None:
    """
    Store the provider ID and record that final confirmation
    will arrive asynchronously.
    """

    with get_connection() as connection:
        payment = connection.execute(
            """
            SELECT status
            FROM payments
            WHERE id = %s
            FOR UPDATE
            """,
            (payment_id,),
        ).fetchone()

        if payment is None:
            raise ValueError(f"Payment {payment_id} does not exist")

        if payment["status"] != "PROCESSING":
            return

        connection.execute(
            """
            UPDATE payments
            SET
                provider_payment_id = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (provider_payment_id, payment_id),
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
                'PAYMENT_AWAITING_WEBHOOK',
                'PROCESSING',
                'PROCESSING',
                '{}'::jsonb
            )
            """,
            (uuid.uuid4(), payment_id),
        )


def record_retry(payment_id: str, reason: str) -> None:
    """Increase the retry counter and add a retry ledger event."""

    safe_reason = reason[:200]

    with get_connection() as connection:
        payment = connection.execute(
            """
            SELECT status
            FROM payments
            WHERE id = %s
            FOR UPDATE
            """,
            (payment_id,),
        ).fetchone()

        if payment is None:
            raise ValueError(f"Payment {payment_id} does not exist")

        if payment["status"] != "PROCESSING":
            return

        connection.execute(
            """
            UPDATE payments
            SET
                retry_count = retry_count + 1,
                failure_code = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (safe_reason, payment_id),
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
            SELECT
                %s,
                id,
                'PAYMENT_RETRY_SCHEDULED',
                status,
                status,
                jsonb_build_object(
                    'reason', %s::text,
                    'retry_number', retry_count
                )
            FROM payments
            WHERE id = %s
            """,
            (
                uuid.uuid4(),
                safe_reason,
                payment_id,
            ),
        )


@celery_app.task(
    bind=True,
    name="process_payment",
    max_retries=3,
)
def process_payment(
    self,
    payment_id: str,
    scenario: str = "success",
) -> dict[str, Any]:
    """
    Process one payment through the simulated provider.

    Temporary provider failures are retried with exponential
    backoff. Permanent failures immediately finalize the payment.
    """

    payment = get_payment(payment_id)

    if payment is None:
        return {
            "payment_id": payment_id,
            "result": "not_found",
        }

    if payment["status"] in {"SUCCEEDED", "REFUNDED"}:
        return {
            "payment_id": payment_id,
            "result": "already_completed",
        }

    if payment["status"] == "FAILED":
        return {
            "payment_id": payment_id,
            "result": "already_failed",
        }

    if payment["status"] == "CREATED":
        record_transition(
            payment_id=payment_id,
            event_type="PAYMENT_PROCESSING",
            new_status="PROCESSING",
        )

    try:
        provider_result = send_payment_to_provider(
            payment_id=payment_id,
            amount_cents=payment["amount_cents"],
            currency=payment["currency"],
            scenario=scenario,
        )

        provider_status = provider_result["status"]
        provider_payment_id = provider_result[
            "provider_payment_id"
        ]

        if provider_status == "SUCCEEDED":
            record_transition(
                payment_id=payment_id,
                event_type="PAYMENT_SUCCEEDED",
                new_status="SUCCEEDED",
                provider_payment_id=provider_payment_id,
            )

            return {
                "payment_id": payment_id,
                "result": "succeeded",
            }

        if provider_status == "PROCESSING":
            record_awaiting_webhook(
                payment_id=payment_id,
                provider_payment_id=provider_payment_id,
            )

            return {
                "payment_id": payment_id,
                "result": "awaiting_webhook",
            }

        record_transition(
            payment_id=payment_id,
            event_type="PAYMENT_FAILED",
            new_status="FAILED",
            provider_payment_id=provider_payment_id,
            failure_code="PROVIDER_DECLINED",
        )

        return {
            "payment_id": payment_id,
            "result": "failed",
        }

    except TransientProviderError as exc:
        record_retry(payment_id, str(exc))

        if self.request.retries >= self.max_retries:
            record_transition(
                payment_id=payment_id,
                event_type="PAYMENT_FAILED",
                new_status="FAILED",
                failure_code="RETRIES_EXHAUSTED",
            )

            return {
                "payment_id": payment_id,
                "result": "retries_exhausted",
            }

        countdown = min(
            2 ** (self.request.retries + 1),
            30,
        )

        raise self.retry(
            exc=exc,
            countdown=countdown,
        )

    except PermanentProviderError as exc:
        record_transition(
            payment_id=payment_id,
            event_type="PAYMENT_FAILED",
            new_status="FAILED",
            failure_code=str(exc)[:200],
        )

        return {
            "payment_id": payment_id,
            "result": "permanent_failure",
        }


@celery_app.task(name="reconcile_payments")
def reconcile_payments() -> dict:
    return reconcile_processing_payments()



@celery_app.task(
    bind=True,
    name="process_refund",
    max_retries=3,
)
def process_refund(
    self,
    refund_id: str,
    scenario: str = "success",
) -> dict[str, Any]:
    refund = get_refund(refund_id)

    if refund is None:
        return {
            "refund_id": refund_id,
            "result": "not_found",
        }

    if refund["status"] == "SUCCEEDED":
        return {
            "refund_id": refund_id,
            "result": "already_succeeded",
        }

    if refund["status"] == "FAILED":
        return {
            "refund_id": refund_id,
            "result": "already_failed",
        }

    if refund["status"] == "CREATED":
        record_refund_transition(
            refund_id=refund_id,
            event_type="REFUND_PROCESSING",
            new_status="PROCESSING",
        )

    try:
        with get_connection() as connection:
            payment = connection.execute(
                """
                SELECT provider_payment_id
                FROM payments
                WHERE id = %s
                """,
                (refund["payment_id"],),
            ).fetchone()

        if (
            payment is None
            or payment["provider_payment_id"] is None
        ):
            raise PermanentProviderError(
                "Payment has no provider payment ID"
            )

        provider_result = send_refund_to_provider(
            refund_id=refund_id,
            provider_payment_id=payment[
                "provider_payment_id"
            ],
            amount_cents=refund["amount_cents"],
            currency=refund["currency"],
            scenario=scenario,
        )

        provider_status = provider_result["status"]
        provider_refund_id = provider_result[
            "provider_refund_id"
        ]

        if provider_status == "SUCCEEDED":
            record_refund_transition(
                refund_id=refund_id,
                event_type="REFUND_SUCCEEDED",
                new_status="SUCCEEDED",
                provider_refund_id=provider_refund_id,
            )

            complete_successful_refund(refund_id)

            return {
                "refund_id": refund_id,
                "result": "succeeded",
            }

        record_refund_transition(
            refund_id=refund_id,
            event_type="REFUND_FAILED",
            new_status="FAILED",
            provider_refund_id=provider_refund_id,
            failure_code="PROVIDER_REFUND_DECLINED",
        )

        restore_payment_after_failed_refund(refund_id)

        return {
            "refund_id": refund_id,
            "result": "failed",
        }

    except TransientProviderError as exc:
        record_refund_retry(refund_id, str(exc))

        if self.request.retries >= self.max_retries:
            record_refund_transition(
                refund_id=refund_id,
                event_type="REFUND_FAILED",
                new_status="FAILED",
                failure_code="REFUND_RETRIES_EXHAUSTED",
            )

            restore_payment_after_failed_refund(refund_id)

            return {
                "refund_id": refund_id,
                "result": "retries_exhausted",
            }

        countdown = min(
            2 ** (self.request.retries + 1),
            30,
        )

        raise self.retry(
            exc=exc,
            countdown=countdown,
        )

    except PermanentProviderError as exc:
        record_refund_transition(
            refund_id=refund_id,
            event_type="REFUND_FAILED",
            new_status="FAILED",
            failure_code=str(exc)[:200],
        )

        restore_payment_after_failed_refund(refund_id)

        return {
            "refund_id": refund_id,
            "result": "permanent_failure",
        }


@celery_app.task(name="publish_outbox_events")
def publish_outbox_events() -> dict:
    return publish_pending_outbox_events()

    