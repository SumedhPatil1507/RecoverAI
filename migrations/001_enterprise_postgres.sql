-- RecoverAI Enterprise PostgreSQL baseline.
-- Run with a migration runner as a privileged schema owner.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_idempotency (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    idempotency_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS transactions (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    payment_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    amount_paise BIGINT NOT NULL CHECK (amount_paise > 0),
    currency CHAR(3) NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL DEFAULT 'FAILED',
    failure_code TEXT,
    failure_reason TEXT,
    failure_category TEXT,
    email_encrypted BYTEA,
    recoverability_score NUMERIC(7,6) NOT NULL DEFAULT 0 CHECK (recoverability_score BETWEEN 0 AND 1),
    recovery_attempts INTEGER NOT NULL DEFAULT 0 CHECK (recovery_attempts >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, payment_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS transactions_2026_09 PARTITION OF transactions
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE TABLE IF NOT EXISTS audit_logs (
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    log_id BIGINT GENERATED ALWAYS AS IDENTITY,
    transaction_id TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    decision_rationale TEXT NOT NULL,
    source TEXT NOT NULL,
    recoverability_score NUMERIC(7,6) NOT NULL DEFAULT 0,
    previous_hash TEXT NOT NULL,
    current_hash TEXT NOT NULL,
    hmac_signature TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, log_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS audit_logs_2026_09 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE INDEX IF NOT EXISTS idx_transactions_tenant_status ON transactions (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_logs (tenant_id, created_at);

ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_idempotency ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS transactions_tenant_isolation ON transactions;
CREATE POLICY transactions_tenant_isolation ON transactions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS audit_tenant_isolation ON audit_logs;
CREATE POLICY audit_tenant_isolation ON audit_logs
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS idempotency_tenant_isolation ON event_idempotency;
CREATE POLICY idempotency_tenant_isolation ON event_idempotency
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- Operators must create the next monthly partitions before the first day of the month.
-- Example: CREATE TABLE transactions_2026_10 PARTITION OF transactions FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
