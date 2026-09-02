from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RefundCreateRequest(BaseModel):
    amount_cents: int = Field(
        gt=0,
        description="Amount to refund in the smallest currency unit",
    )


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    payment_id: UUID
    merchant_id: str
    idempotency_key: str
    amount_cents: int
    
    currency: str
    status: str
    provider_refund_id: str | None = None
    failure_code: str | None = None
    retry_count:int
    duplicate: bool = False
    created_at: datetime
    updated_at: datetime


class RefundEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    previous_status: str | None
    new_status: str
    metadata: dict[str, Any]
    created_at: datetime


class RefundEventsResponse(BaseModel):
    refund_id: UUID
    events: list[RefundEventResponse]