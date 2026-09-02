import asyncio
import hashlib
import hmac
import json
import os
import uuid
from typing import Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(
    title="Payment Provider Simulator",
    version="3.0.0",
)

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    "development-webhook-secret",
)

WEBHOOK_URL = os.getenv(
    "PAYMENT_API_WEBHOOK_URL",
    "http://api:8000/v1/webhooks/provider",
)

# In-memory provider storage is sufficient for this simulator.
# Restarting the provider container clears these records.
PROVIDER_PAYMENTS: dict[str, dict] = {}
PROVIDER_REFUNDS: dict[str, dict] = {}



class ProviderPaymentRequest(BaseModel):
    payment_id: str
    amount_cents: int = Field(gt=0)
    currency: str = Field(
        default="INR",
        min_length=3,
        max_length=3,
    )


class ProviderPaymentResponse(BaseModel):
    provider_payment_id: str
    payment_id: str
    status: str
    message: str

    


Scenario = Literal[
    "success",
    "declined",
    "timeout",
    "server_error",
    "delayed_success",
    "delayed_webhook",
    "duplicate_webhook",
    "lost_webhook",
]

class ProviderRefundRequest(BaseModel):
    refund_id: str
    provider_payment_id: str
    amount_cents: int = Field(gt=0)
    currency: str = Field(
        default="INR",
        min_length=3,
        max_length=3,
    )


class ProviderRefundResponse(BaseModel):
    provider_refund_id: str
    refund_id: str
    provider_payment_id: str
    status: str
    message: str


def create_signature(raw_body: bytes) -> str:
    """Create an HMAC-SHA256 signature for a webhook body."""

    return hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()


async def deliver_webhook(
    payment_id: str,
    provider_payment_id: str,
    duplicate: bool = False,
) -> None:
    """
    Deliver a signed payment-success webhook after a delay.

    When duplicate=True, the exact same webhook event is sent twice
    to prove that the payment API handles duplicate webhooks safely.
    """

    await asyncio.sleep(5)

    payload = {
        "event_id": f"event_{uuid.uuid4().hex}",
        "event_type": "payment.succeeded",
        "payment_id": payment_id,
        "provider_payment_id": provider_payment_id,
        "status": "SUCCEEDED",
    }

    raw_body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    signature = create_signature(raw_body)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                WEBHOOK_URL,
                content=raw_body,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()

            if duplicate:
                await asyncio.sleep(1)

                duplicate_response = await client.post(
                    WEBHOOK_URL,
                    content=raw_body,
                    headers=headers,
                    timeout=10,
                )
                duplicate_response.raise_for_status()

    except httpx.HTTPError as exc:
        # The simulator logs the problem without crashing its API.
        print(f"Webhook delivery failed: {exc}")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "provider-simulator",
    }


@app.post(
    "/provider/payments",
    response_model=ProviderPaymentResponse,
)
async def create_provider_payment(
    request: ProviderPaymentRequest,
    background_tasks: BackgroundTasks,
    x_test_scenario: Scenario = Header(default="success"),
) -> ProviderPaymentResponse:
    provider_payment_id = f"provider_{uuid.uuid4().hex}"

    PROVIDER_PAYMENTS[provider_payment_id] = {
        "provider_payment_id": provider_payment_id,
        "payment_id": request.payment_id,
        "amount_cents": request.amount_cents,
        "currency": request.currency.upper(),
        "status": "PROCESSING",
    }

    if x_test_scenario == "timeout":
        await asyncio.sleep(15)

    if x_test_scenario == "server_error":
        raise HTTPException(
            status_code=503,
            detail="Simulated provider service failure",
        )

    if x_test_scenario == "declined":
        PROVIDER_PAYMENTS[provider_payment_id]["status"] = "FAILED"

        return ProviderPaymentResponse(
            provider_payment_id=provider_payment_id,
            payment_id=request.payment_id,
            status="FAILED",
            message="Payment declined by simulated provider",
        )

    if x_test_scenario == "delayed_success":
        await asyncio.sleep(5)

    if x_test_scenario == "lost_webhook":
        # Provider succeeded, but no webhook will be delivered.
        # Reconciliation must discover and repair this mismatch.
        PROVIDER_PAYMENTS[provider_payment_id]["status"] = "SUCCEEDED"

        return ProviderPaymentResponse(
            provider_payment_id=provider_payment_id,
            payment_id=request.payment_id,
            status="PROCESSING",
            message="Payment succeeded but webhook was lost",
        )

    if x_test_scenario in {
        "delayed_webhook",
        "duplicate_webhook",
    }:
        PROVIDER_PAYMENTS[provider_payment_id]["status"] = "SUCCEEDED"

        background_tasks.add_task(
            deliver_webhook,
            request.payment_id,
            provider_payment_id,
            x_test_scenario == "duplicate_webhook",
        )

        return ProviderPaymentResponse(
            provider_payment_id=provider_payment_id,
            payment_id=request.payment_id,
            status="PROCESSING",
            message="Final result will be delivered by webhook",
        )

    PROVIDER_PAYMENTS[provider_payment_id]["status"] = "SUCCEEDED"

    return ProviderPaymentResponse(
        provider_payment_id=provider_payment_id,
        payment_id=request.payment_id,
        status="SUCCEEDED",
        message="Payment completed successfully",
    )


@app.get("/provider/payments/{provider_payment_id}")
def retrieve_provider_payment(
    provider_payment_id: str,
) -> dict:
    payment = PROVIDER_PAYMENTS.get(provider_payment_id)

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Provider payment not found",
        )

    return payment


@app.post(
    "/provider/refunds",
    response_model=ProviderRefundResponse,
)
async def create_provider_refund(
    request: ProviderRefundRequest,
    x_test_scenario: Literal[
        "success",
        "declined",
        "timeout",
        "server_error",
    ] = Header(default="success"),
) -> ProviderRefundResponse:
    payment = PROVIDER_PAYMENTS.get(
        request.provider_payment_id
    )

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Provider payment not found",
        )

    if payment["status"] != "SUCCEEDED":
        raise HTTPException(
            status_code=409,
            detail="Only successful payments can be refunded",
        )

    if request.currency.upper() != payment["currency"]:
        raise HTTPException(
            status_code=422,
            detail="Refund currency does not match payment currency",
        )

    already_refunded = sum(
        refund["amount_cents"]
        for refund in PROVIDER_REFUNDS.values()
        if (
            refund["provider_payment_id"]
            == request.provider_payment_id
            and refund["status"] == "SUCCEEDED"
        )
    )

    refundable_amount = (
        payment["amount_cents"] - already_refunded
    )

    if request.amount_cents > refundable_amount:
        raise HTTPException(
            status_code=422,
            detail="Refund amount exceeds refundable balance",
        )

    provider_refund_id = f"refund_{uuid.uuid4().hex}"

    PROVIDER_REFUNDS[provider_refund_id] = {
        "provider_refund_id": provider_refund_id,
        "refund_id": request.refund_id,
        "provider_payment_id": request.provider_payment_id,
        "amount_cents": request.amount_cents,
        "currency": request.currency.upper(),
        "status": "PROCESSING",
    }

    if x_test_scenario == "timeout":
        await asyncio.sleep(15)

    if x_test_scenario == "server_error":
        raise HTTPException(
            status_code=503,
            detail="Simulated refund provider failure",
        )

    if x_test_scenario == "declined":
        PROVIDER_REFUNDS[provider_refund_id][
            "status"
        ] = "FAILED"

        return ProviderRefundResponse(
            provider_refund_id=provider_refund_id,
            refund_id=request.refund_id,
            provider_payment_id=request.provider_payment_id,
            status="FAILED",
            message="Refund declined by simulated provider",
        )

    PROVIDER_REFUNDS[provider_refund_id][
        "status"
    ] = "SUCCEEDED"

    return ProviderRefundResponse(
        provider_refund_id=provider_refund_id,
        refund_id=request.refund_id,
        provider_payment_id=request.provider_payment_id,
        status="SUCCEEDED",
        message="Refund completed successfully",
    )


@app.get("/provider/refunds/{provider_refund_id}")
def retrieve_provider_refund(
    provider_refund_id: str,
) -> dict:
    refund = PROVIDER_REFUNDS.get(provider_refund_id)

    if refund is None:
        raise HTTPException(
            status_code=404,
            detail="Provider refund not found",
        )

    return refund
