import os
import time
import uuid
from typing import Any

import httpx


BASE_URL = os.getenv(
    "TEST_API_URL",
    "http://localhost:8000",
)

API_KEY = os.getenv(
    "TEST_API_KEY",
    "merchant-development-key",
)

HEADERS = {
    "X-API-Key": API_KEY,
}


def wait_for_payment_status(
    payment_id: str,
    expected_status: str,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        response = httpx.get(
            f"{BASE_URL}/v1/payments/{payment_id}",
            headers=HEADERS,
            timeout=5,
        )
        response.raise_for_status()

        payment = response.json()

        if payment["status"] == expected_status:
            return payment

        time.sleep(1)

    raise AssertionError(
        f"Payment {payment_id} did not reach "
        f"{expected_status}"
    )


def wait_for_refund_status(
    refund_id: str,
    expected_status: str,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        response = httpx.get(
            f"{BASE_URL}/v1/refunds/{refund_id}",
            headers=HEADERS,
            timeout=5,
        )
        response.raise_for_status()

        refund = response.json()

        if refund["status"] == expected_status:
            return refund

        time.sleep(1)

    raise AssertionError(
        f"Refund {refund_id} did not reach "
        f"{expected_status}"
    )


def test_payment_and_partial_refund_flow() -> None:
    unique_value = uuid.uuid4().hex

    payment_response = httpx.post(
        f"{BASE_URL}/v1/payments",
        headers={
            **HEADERS,
            "Idempotency-Key": (
                f"e2e-payment-{unique_value}"
            ),
            "X-Test-Scenario": "success",
        },
        json={
            "user_id": f"e2e-user-{unique_value}",
            "amount_cents": 100000,
            "currency": "INR",
        },
        timeout=5,
    )

    assert payment_response.status_code in {
        200,
        201,
        202,
    }

    payment = payment_response.json()
    payment_id = payment["id"]

    final_payment = wait_for_payment_status(
        payment_id,
        "SUCCEEDED",
    )

    assert final_payment["amount_cents"] == 100000
    assert final_payment["currency"] == "INR"

    refund_key = f"e2e-refund-{unique_value}"

    refund_response = httpx.post(
        (
            f"{BASE_URL}/v1/payments/"
            f"{payment_id}/refunds"
        ),
        headers={
            **HEADERS,
            "Idempotency-Key": refund_key,
            "X-Test-Scenario": "success",
        },
        json={
            "amount_cents": 40000,
        },
        timeout=5,
    )

    assert refund_response.status_code == 202

    refund = refund_response.json()
    refund_id = refund["id"]

    completed_refund = wait_for_refund_status(
        refund_id,
        "SUCCEEDED",
    )

    assert completed_refund["amount_cents"] == 40000
    assert completed_refund[
        "provider_refund_id"
    ] is not None

    duplicate_response = httpx.post(
        (
            f"{BASE_URL}/v1/payments/"
            f"{payment_id}/refunds"
        ),
        headers={
            **HEADERS,
            "Idempotency-Key": refund_key,
            "X-Test-Scenario": "success",
        },
        json={
            "amount_cents": 40000,
        },
        timeout=5,
    )

    assert duplicate_response.status_code == 202

    duplicate = duplicate_response.json()

    assert duplicate["id"] == refund_id
    assert duplicate["duplicate"] is True

    payment_after_refund = wait_for_payment_status(
        payment_id,
        "SUCCEEDED",
    )

    assert payment_after_refund["status"] == "SUCCEEDED"


def create_test_payment(
        scenario: str,
    amount_cents: int = 100000,
) -> dict[str, Any]:
    unique_value = uuid.uuid4().hex

    response = httpx.post(
        f"{BASE_URL}/v1/payments",
        headers={
            **HEADERS,
            "Idempotency-Key": (
                f"test-payment-{unique_value}"
            ),
            "X-Test-Scenario": scenario,
        },
        json={
            "user_id": f"test-user-{unique_value}",
            "amount_cents": amount_cents,
            "currency": "INR",
        },
        timeout=5,
    )

    assert response.status_code in {
        200,
        201,
        202,
    }

    return response.json()


def get_payment_events(
    payment_id: str,
) -> list[dict[str, Any]]:
    response = httpx.get(
        f"{BASE_URL}/v1/payments/{payment_id}/events",
        headers=HEADERS,
        timeout=5,
    )

    response.raise_for_status()

    return response.json()["events"]


def test_declined_payment() -> None:
    payment = create_test_payment("declined")

    final_payment = wait_for_payment_status(
        payment["id"],
        "FAILED",
    )

    assert final_payment["failure_code"] == (
        "PROVIDER_DECLINED"
    )

    events = get_payment_events(payment["id"])
    event_types = [
        event["event_type"]
        for event in events
    ]

    assert event_types == [
        "PAYMENT_CREATED",
        "PAYMENT_PROCESSING",
        "PAYMENT_FAILED",
    ]


def test_duplicate_webhook_is_processed_once() -> None:
    payment = create_test_payment(
        "duplicate_webhook"
    )

    wait_for_payment_status(
        payment["id"],
        "SUCCEEDED",
        timeout_seconds=25,
    )

    # Allow the provider time to send the duplicate event.
    time.sleep(3)

    events = get_payment_events(payment["id"])
    succeeded_events = [
        event
        for event in events
        if event["event_type"] == "PAYMENT_SUCCEEDED"
    ]

    assert len(succeeded_events) == 1


def test_lost_webhook_is_reconciled() -> None:
    payment = create_test_payment("lost_webhook")

    final_payment = wait_for_payment_status(
        payment["id"],
        "SUCCEEDED",
        timeout_seconds=50,
    )

    assert final_payment["status"] == "SUCCEEDED"

    events = get_payment_events(payment["id"])
    event_types = [
        event["event_type"]
        for event in events
    ]

    assert "PAYMENT_AWAITING_WEBHOOK" in event_types
    assert (
        "PAYMENT_RECONCILED_SUCCEEDED"
        in event_types
    )


def test_full_refund_marks_payment_refunded() -> None:
    payment = create_test_payment(
        "success",
        amount_cents=50000,
    )

    wait_for_payment_status(
        payment["id"],
        "SUCCEEDED",
    )

    unique_value = uuid.uuid4().hex

    response = httpx.post(
        (
            f"{BASE_URL}/v1/payments/"
            f"{payment['id']}/refunds"
        ),
        headers={
            **HEADERS,
            "Idempotency-Key": (
                f"full-refund-{unique_value}"
            ),
            "X-Test-Scenario": "success",
        },
        json={
            "amount_cents": 50000,
        },
        timeout=5,
    )

    assert response.status_code == 202

    refund = response.json()

    wait_for_refund_status(
        refund["id"],
        "SUCCEEDED",
    )

    final_payment = wait_for_payment_status  (
        payment["id"],
        "REFUNDED",
    )

    assert final_payment["status"] == "REFUNDED"


def test_refund_cannot_exceed_payment_amount() -> None:
    payment = create_test_payment(
        "success",
        amount_cents=50000,
    )

    wait_for_payment_status(
        payment["id"],
        "SUCCEEDED",
    )

    response = httpx.post(
        (
            f"{BASE_URL}/v1/payments/"
            f"{payment['id']}/refunds"
        ),
        headers={
            **HEADERS,
            "Idempotency-Key": (
                f"excess-refund-{uuid.uuid4().hex}"
            ),
            "X-Test-Scenario": "success",
        },
        json={
            "amount_cents": 50001,
        },
        timeout=5,
    )

    assert response.status_code == 422,response.text
    assert "exceeds refundable balance" in str(
        response.json()["detail"]
    ).lower() 