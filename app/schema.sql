-- This table stores every payment we process.
--
-- idempotency_key has a UNIQUE constraint: Postgres will refuse to
-- insert two rows with the same key. That alone is NOT enough to stop
-- double-charges under concurrency though -- two requests can both
-- pass a "does this exist?" check before either finishes inserting.
-- We fix that properly in app/payments.py using an atomic insert.

CREATE TABLE IF NOT EXISTS payments (
    id              BIGSERIAL PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id         TEXT NOT NULL,
    amount_cents    BIGINT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'completed',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
