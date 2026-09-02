from typing import Any

import httpx

from app.core.config import settings


class TransientProviderError(Exception):
    """Temporary failure that can safely be retried."""


class PermanentProviderError(Exception):
    """Permanent failure that should not be retried."""


def send_payment_to_provider(
    payment_id: str,
    amount_cents: int,
    currency: str,
    scenario: str = "success",
) -> dict[str, Any]:
    try:
        response = httpx.post(
            f"{settings.provider_url}/provider/payments",
            headers={
                "X-Test-Scenario": scenario,
                "X-Provider-API-Key": settings.provider_api_key,
            },
            json={
                "payment_id": payment_id,
                "amount_cents": amount_cents,
                "currency": currency,
            },
            timeout=3.0,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise TransientProviderError(
            "Provider request timed out or encountered a network error"
        ) from exc

    if response.status_code >= 500:
        raise TransientProviderError(
            f"Provider temporarily unavailable: {response.status_code}"
        )

    if response.status_code >= 400:
        raise PermanentProviderError(
            f"Provider rejected request: {response.text}"
        )

    return response.json()


def get_provider_payment(
    provider_payment_id: str,
) -> dict[str, Any]:
    try:
        response = httpx.get(
            (
                f"{settings.provider_url}/provider/payments/"
                f"{provider_payment_id}"
            ),
            headers={
                "X-Provider-API-Key": settings.provider_api_key,
            },
            timeout=3.0,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise TransientProviderError(
            "Unable to retrieve provider payment"
        ) from exc

    if response.status_code >= 500:
        raise TransientProviderError(
            "Provider temporarily unavailable"
        )

    if response.status_code == 404:
        raise PermanentProviderError(
            "Provider payment was not found"
        )

    if response.status_code >= 400:
        raise PermanentProviderError(
            f"Provider lookup failed: {response.text}"
        )

    return response.json()

def send_refund_to_provider(
    refund_id: str,
    provider_payment_id: str,
    amount_cents: int,
    currency: str,
    scenario: str = "success",
) -> dict[str, Any]:
    try:
        response = httpx.post(
            f"{settings.provider_url}/provider/refunds",
            json={
                "refund_id": refund_id,
                "provider_payment_id": provider_payment_id,
                "amount_cents": amount_cents,
                "currency": currency,
            },
            headers={
                "X-Provider-API-Key": settings.provider_api_key,
                "X-Test-Scenario": scenario,
            },
            timeout=5.0,
        )
    except (
        httpx.TimeoutException,
        httpx.NetworkError,
    ) as exc:
        raise TransientProviderError(
            "Unable to reach provider refund service"
        ) from exc

    if response.status_code >= 500:
        raise TransientProviderError(
            "Provider refund service is temporarily unavailable"
        )

    if response.status_code >= 400:
        raise PermanentProviderError(
            f"Provider refund failed: {response.text}"
        )

    return response.json()
