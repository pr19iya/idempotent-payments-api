CREATE TABLE IF NOT EXISTS refunds (
    id UUID PRIMARY KEY,
    payment_id UUID NOT NULL
        REFERENCES payments(id)
        ON DELETE CASCADE,
    merchant_id TEXT NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    amount_cents BIGINT NOT NULL
        CHECK (amount_cents > 0),
    currency VARCHAR(3) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'CREATED',
    provider_refund_id VARCHAR(255),
    failure_code VARCHAR(255),
    retry_count INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT refunds_status_check CHECK (
        status IN (
            'CREATED',
            'PROCESSING',
            'SUCCEEDED',
            'FAILED'
        )
    ),

    CONSTRAINT refunds_merchant_idempotency_unique
        UNIQUE (merchant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_refunds_payment_id
    ON refunds(payment_id);

CREATE INDEX IF NOT EXISTS idx_refunds_status
    ON refunds(status);

CREATE TABLE IF NOT EXISTS refund_events (
    id UUID PRIMARY KEY,
    refund_id UUID NOT NULL
        REFERENCES refunds(id)
        ON DELETE CASCADE,
    event_type VARCHAR(100) NOT NULL,
    previous_status VARCHAR(30),
    new_status VARCHAR(30) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refund_events_refund_id
    ON refund_events(refund_id);

CREATE INDEX IF NOT EXISTS idx_refund_events_created_at
    ON refund_events(created_at);