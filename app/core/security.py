import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def authenticate_merchant(
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> str:
    if not hmac.compare_digest(x_api_key, settings.merchant_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid merchant API key",
        )

    # Later this can come from a merchants database table.
    return "demo-merchant"