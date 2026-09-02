"""
This is your PROOF. It fires N concurrent requests carrying the SAME
idempotency_key at your running server, then reports how many actually
resulted in a NEW payment (should always be exactly 1) vs how many
were correctly recognized as duplicates.

Run this against the naive handler (if you temporarily wire it in) to
WATCH it fail with newCount > 1, then run it against the fixed handler
to watch it pass. That before/after comparison is your resume line and
your interview story.

Usage:
    python race_test.py --n 50 --url http://localhost:8000/payments
"""
import argparse
import asyncio
import httpx


async def fire_one(client: httpx.AsyncClient, url: str, payload: dict):
    try:
        resp = await client.post(url, json=payload, timeout=10)
        data = resp.json()
        return "duplicate" if data.get("duplicate") else "new"
    except Exception:
        return "error"


async def main(n: int, url: str):
    payload = {
        "idempotency_key": "race-test-key-001",  # SAME key every time, on purpose
        "user_id": "user-42",
        "amount_cents": 50000,
    }

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[fire_one(client, url, payload) for _ in range(n)])

    new_count = results.count("new")
    dup_count = results.count("duplicate")
    err_count = results.count("error")

    print(f"Fired {n} concurrent requests with the SAME idempotency key")
    print(f"  New payments created:      {new_count}  (should be exactly 1)")
    print(f"  Recognized as duplicate:   {dup_count}")
    print(f"  Errors:                    {err_count}")

    if new_count == 1:
        print("PASS: no double-charge occurred.")
    else:
        print("FAIL: double-charge occurred! new_count should be 1.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--url", type=str, default="http://localhost:8000/payments")
    args = parser.parse_args()
    asyncio.run(main(args.n, args.url))
