from fastapi import APIRouter, Response

from app.db import get_connection


router = APIRouter(
    prefix="/metrics",
    tags=["Metrics"],
)

PROMETHEUS_CONTENT_TYPE = (
    "text/plain; version=0.0.4; charset=utf-8"
)


@router.get(
    "/business",
    include_in_schema=False,
)
def business_metrics() -> Response:
    lines: list[str] = []

    with get_connection() as connection:
        payment_counts = connection.execute(
            """
            SELECT status, COUNT(*) AS total
            FROM payments
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()

        refund_counts = connection.execute(
            """
            SELECT status, COUNT(*) AS total
            FROM refunds
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()

        payment_retry_total = connection.execute(
            """
            SELECT COALESCE(SUM(retry_count), 0) AS total
            FROM payments
            """
        ).fetchone()["total"]

        refund_retry_total = connection.execute(
            """
            SELECT COALESCE(SUM(retry_count), 0) AS total
            FROM refunds
            """
        ).fetchone()["total"]

        successful_refund_amount = connection.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS total
            FROM refunds
            WHERE status = 'SUCCEEDED'
            """
        ).fetchone()["total"]

        outbox_counts = connection.execute(
            """
            SELECT status, COUNT(*) AS total
            FROM outbox_events
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()

    lines.extend(
        [
            "# HELP payment_gateway_payments_total "
            "Payments grouped by current status.",
            "# TYPE payment_gateway_payments_total gauge",
        ]
    )

    for row in payment_counts:
        lines.append(
            "payment_gateway_payments_total"
            f'{{status="{row["status"]}"}} '
            f'{int(row["total"])}'
        )

    lines.extend(
        [
            "# HELP payment_gateway_refunds_total "
            "Refunds grouped by current status.",
            "# TYPE payment_gateway_refunds_total gauge",
        ]
    )

    for row in refund_counts:
        lines.append(
            "payment_gateway_refunds_total"
            f'{{status="{row["status"]}"}} '
            f'{int(row["total"])}'
        )

    lines.extend(
        [
            "# HELP payment_gateway_payment_retries_total "
            "Total payment retry attempts.",
            "# TYPE payment_gateway_payment_retries_total gauge",
            (
                "payment_gateway_payment_retries_total "
                f"{int(payment_retry_total)}"
            ),
            "# HELP payment_gateway_refund_retries_total "
            "Total refund retry attempts.",
            "# TYPE payment_gateway_refund_retries_total gauge",
            (
                "payment_gateway_refund_retries_total "
                f"{int(refund_retry_total)}"
            ),
            "# HELP payment_gateway_refunded_amount_cents_total "
            "Total successfully refunded amount in cents.",
            "# TYPE "
            "payment_gateway_refunded_amount_cents_total gauge",
            (
                "payment_gateway_refunded_amount_cents_total "
                f"{int(successful_refund_amount)}"
            ),
            "# HELP payment_gateway_outbox_events_total "
            "Outbox events grouped by status.",
            "# TYPE payment_gateway_outbox_events_total gauge",
        ]
    )

    for row in outbox_counts:
        lines.append(
            "payment_gateway_outbox_events_total"
            f'{{status="{row["status"]}"}} '
            f'{int(row["total"])}'
        )

    return Response(
        content="\n".join(lines) + "\n",
        media_type=PROMETHEUS_CONTENT_TYPE,
    )