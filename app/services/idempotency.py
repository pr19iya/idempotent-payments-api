import hashlib
import json
import uuid
from contextlib import contextmanager
from typing import Generator

import redis

from app.core.config import settings


redis_client = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


def generate_request_hash(
    user_id: str,
    amount_cents: int,
    currency: str,
) -> str:
    canonical_request = json.dumps(
        {
            "user_id": user_id,
            "amount_cents": amount_cents,
            "currency": currency.upper(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()


def build_lock_key(merchant_id: str, idempotency_key: str) -> str:
    return f"payment-lock:{merchant_id}:{idempotency_key}"


@contextmanager
def acquire_payment_lock(
    merchant_id: str,
    idempotency_key: str,
) -> Generator[bool, None, None]:
    lock_key = build_lock_key(merchant_id, idempotency_key)
    ownership_token = uuid.uuid4().hex

    try:
        acquired = bool(
            redis_client.set(
                lock_key,
                ownership_token,
                nx=True,
                ex=settings.idempotency_lock_seconds,
            )
        )
    except redis.RedisError:
        # PostgreSQL's unique constraint remains the final safety layer.
        acquired = True

    try:
        yield acquired
    finally:
        if acquired:
            try:
                redis_client.eval(
                    RELEASE_LOCK_SCRIPT,
                    1,
                    lock_key,
                    ownership_token,
                )
            except redis.RedisError:
                pass


def redis_is_ready() -> bool:
    try:
        return bool(redis_client.ping())
    except redis.RedisError:
        return False