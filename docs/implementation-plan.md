# RecoverAI Enterprise Tier — Implementation Plan

## Delivery Sequence

| Phase | Change | Affected files | Verification |
|---|---|---|---|
| 1 | Establish production configuration contracts for PostgreSQL, execution mode, queue mode, JWT, and EV costs. | `recover_ai/config.py`, `.env.example` | Settings validation tests; production startup rejects SQLite and insecure secrets. |
| 2 | Add the deterministic EV engine and make non-positive EV a hard dispatch bypass. | `recover_ai/expected_value.py`, `recover_ai/agent_engine.py` | EV zero, negative, fee threshold, and exact-decimal tests. |
| 3 | Add shadow-mode interception and an evaluation ledger event. | `recover_ai/agent_engine.py`, `recover_ai/database.py` | Provider adapters are not called in shadow mode. |
| 4 | Add JWT role parsing and tenant-aware authorization while retaining API-key compatibility for local development. | `recover_ai/auth.py`, `recover_ai/main.py` | Admin/operator/auditor authorization and cross-tenant rejection tests. |
| 5 | Add PostgreSQL schema with native monthly range partitions, RLS, indexes, and idempotency constraints. | `migrations/001_enterprise_postgres.sql` | SQL review and migration smoke test against PostgreSQL CI service. |
| 6 | Make distributed queue selection explicit and fail closed in production. | `recover_ai/queue_worker.py`, `recover_ai/main.py` | Redis outage health test; production does not use asyncio fallback. |
| 7 | Remove non-deterministic live outcome simulation and ensure all live decisions are recorded before dispatch. | `recover_ai/agent_engine.py`, `recover_ai/main.py` | Determinism tests and audit assertions. |
| 8 | Add operational documentation and CI checks for tests, lint, SAST, and migration review. | `README.md`, `.github/workflows/deploy.yml` | CI execution and artifact inspection. |

## Migration Notes

The PostgreSQL migration creates tenant-owned tables with `tenant_id`, an idempotency ledger, monthly range-partitioned transactions and audit logs, and Row Level Security policies based on `app.tenant_id`. The application must set the tenant context with `SET LOCAL app.tenant_id = ...` inside each transaction. Existing SQLite data is not migrated implicitly; an explicit export/import process is required to avoid silently carrying insecure assumptions into production.

## Rollout Strategy

The first deployment runs in `SHADOW` mode with provider credentials disabled and compares counterfactual decisions against current operations. The second deployment enables PostgreSQL and distributed queue enforcement while retaining shadow mode. The third deployment enables live dispatch for tenants that have approved thresholds and completed HITL validation. Rollback consists of setting `EXECUTION_MODE=SHADOW` and draining the queue; database migrations are additive.

## Verification Tasks

The verification suite must run unit tests, API authorization tests, tenant isolation tests, PostgreSQL migration smoke tests, queue dependency health tests, cryptographic audit tests, and a headless integration test against the containerized stack. Browser tests must verify that the deployed API health endpoint, tenant-scoped audit view, HITL view, and shadow-mode status are reachable without exposing another tenant's data.

## Definition of Done

The upgrade is complete when all requirements in `enterprise-requirements.md` are implemented or explicitly tracked as an infrastructure dependency, all automated tests pass, no production route contains a mock state initializer, no live route uses random success simulation, and the production configuration fails closed for missing PostgreSQL, distributed queue, JWT secret, and encryption keys.

## References

[1]: https://www.postgresql.org/docs/current/ddl-partitioning.html "PostgreSQL table partitioning"

[2]: https://www.postgresql.org/docs/current/ddl-rowsecurity.html "PostgreSQL Row Security Policies"

[3]: https://owasp.org/www-project-application-security-verification-standard/ "OWASP Application Security Verification Standard"
