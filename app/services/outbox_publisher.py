import json
import os
from typing import Any

import redis

from app.db import get_connection


REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://redis:6379/0",
)

OUTBOX_STREAM = os.getenv(
    "OUTBOX_STREAM",
    "payment-events",
)


def publish_pending_outbox_events(
    batch_size: int = 100,
) -> dict[str, Any]:
    published = 0
    failed = 0

    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )

    with get_connection() as connection:
        events = connection.execute(
            """
            SELECT
                id,
                aggregate_id,
                event_type,
                payload,
                attempts,
                created_at
            FROM outbox_events
            WHERE status = 'PENDING'
              AND published_at IS NULL
              AND available_at <= NOW()
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT %s
            """,
            (batch_size,),
        ).fetchall()

        for event in events:
            try:
                redis_client.xadd(
                    OUTBOX_STREAM,
                    {
                        "event_id": str(event["id"]),
                        "aggregate_id": str(
                            event["aggregate_id"]
                        ),
                        "event_type": event["event_type"],
                        "payload": json.dumps(
                            event["payload"],
                            default=str,
                            separators=(",", ":"),
                        ),
                        "created_at": (
                            event["created_at"].isoformat()
                        ),
                    },
                )

                connection.execute(
                    """
                    UPDATE outbox_events
                    SET
                        status = 'PUBLISHED',
                        attempts = attempts + 1,
                        published_at = NOW()
                    WHERE id = %s
                    """,
                    (event["id"],),
                )

                published += 1

            except redis.RedisError:
                connection.execute(
                    """
                    UPDATE outbox_events
                    SET
                        attempts = attempts + 1,
                        available_at =
                            NOW()
                            + (
                                LEAST(
                                    POWER(
                                        2,
                                        attempts + 1
                                    ),
                                    300
                                ) * INTERVAL '1 second'
                            )
                    WHERE id = %s
                    """,
                    (event["id"],),
                )

                failed += 1

    return {
        "selected": len(events),
        "published": published,
        "failed": failed,
        "stream": OUTBOX_STREAM,
    }