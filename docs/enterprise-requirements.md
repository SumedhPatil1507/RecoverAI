# RecoverAI Enterprise Tier — Requirements Specification

## Purpose

This specification defines the production controls required to evolve RecoverAI from a local prototype into a multi-tenant enterprise payment-recovery service. The requirements use the **Easy Approach to Requirements Syntax (EARS)**. Production behavior is fail-closed for security, financial, and persistence controls; local development remains available through explicit development configuration.

## Functional Requirements

| ID | EARS requirement |
|---|---|
| FR-01 | **When** the service runs with `ENVIRONMENT=production`, **the system shall** require `DATABASE_URL` using PostgreSQL and shall refuse to start when only a SQLite path is configured. |
| FR-02 | **When** the service creates a telemetry or audit table in PostgreSQL, **the system shall** use time-based range partitions on `created_at` or `timestamp` and shall provide an operational migration for future partitions. |
| FR-03 | **When** a webhook is accepted, **the system shall** assign a stable event identifier and idempotency key before publishing it to the distributed queue. |
| FR-04 | **When** production queue dependencies are unavailable, **the system shall** fail the health check and shall not silently route work to an in-process queue. |
| FR-05 | **When** a request contains a tenant identity, **the system shall** apply that identity to database session context and shall enforce PostgreSQL Row Level Security on tenant-owned tables. |
| FR-06 | **When** an authenticated principal calls a protected endpoint, **the system shall** authorize one of `enterprise_admin`, `operator`, or `auditor` roles. |
| FR-07 | **When** an operator attempts an automated external action, **the system shall** require an approved human-in-the-loop decision whenever a configured high-value, repeated-failure, high-discount, or ambiguous-score gate is met. |
| FR-08 | **When** the decision engine evaluates an action, **the system shall** calculate `EV = (probability × recoverable_amount) − (operational_fee + gateway_cost)` using integer paise or exact decimal arithmetic. |
| FR-09 | **If** expected value is less than or equal to zero, **the system shall** bypass dispatch, persist the decision reason, and publish an audit event. |
| FR-10 | **When** the primary model is unavailable or returns an invalid decision, **the system shall** use the deterministic rule engine and shall record the fallback source. |
| FR-11 | **When** `EXECUTION_MODE=SHADOW`, **the system shall** run feature processing, scoring, and decision logic but shall not call payment, messaging, or notification providers. |
| FR-12 | **When** an audit record is appended, **the system shall** include the previous record hash and a keyed SHA-256 signature over canonical record data. |
| FR-13 | **When** an audit record or its predecessor is modified, **the system shall** report the tampered record index during verification. |
| FR-14 | **When** sensitive PII, PHI, or provider credentials are persisted, **the system shall** use AES-256-GCM with authenticated encryption and a managed key supplied through the runtime secret store. |
| FR-15 | **When** API or worker telemetry is emitted, **the system shall** include OpenTelemetry spans and structured metrics for latency, queue health, decision outcomes, EV bypasses, and model drift. |

## Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-01 | The webhook acknowledgement path shall remain bounded and shall not perform model inference, provider calls, or database writes beyond the minimum idempotency operation. |
| NFR-02 | Tenant data shall not be accessible across tenant boundaries, including through administrative query parameters. |
| NFR-03 | Financial decisions shall be deterministic for identical inputs and configuration. Random simulation shall not determine live transaction status. |
| NFR-04 | Schema migrations shall be forward-compatible, repeatable, reviewable, and executable by CI or an operator without application code imports. |
| NFR-05 | Production secrets shall never have insecure defaults. Development defaults shall be clearly marked and rejected in production. |
| NFR-06 | The test suite shall cover EV zero and negative cases, cryptographic tamper detection, role authorization, tenant isolation, shadow dispatch suppression, and duplicate event handling. |

## Acceptance Criteria

A release is enterprise-ready only when the production startup check rejects SQLite-only configuration, PostgreSQL migration files pass syntax review, queue dependency loss is observable, a tenant-scoped query cannot cross tenant boundaries, a non-positive EV produces no external dispatch, shadow mode produces no external dispatch, and all security regression tests pass.

## Assumptions and Boundaries

The application continues to support SQLite only for explicitly configured local development and unit tests. PostgreSQL with PgBouncer is the production system of record. Redis/Celery remains the supported distributed execution transport. Provider credentials and encryption keys are injected through a secret manager or equivalent environment integration.

## References

[1]: https://www.gov.uk/government/publications/requirements-engineering/requirements-engineering "Requirements engineering guidance"

[2]: https://www.postgresql.org/docs/current/ddl-rowsecurity.html "PostgreSQL Row Security Policies"

[3]: https://opentelemetry.io/docs/concepts/observability-primer/ "OpenTelemetry observability primer"
