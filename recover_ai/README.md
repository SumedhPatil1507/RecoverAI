# RecoverAI: Agentic Payment Degradation & Abandonment Engine
 
> An autonomous, production-grade AI system that detects failed payments, classifies root causes, and executes intelligent recovery actions — all within a sub-50 ms webhook response window.

---

## System Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │           Razorpay / Simulator               │
                        │      payment.failed  webhook events          │
                        └────────────────┬────────────────────────────┘
                                         │ HTTPS POST + HMAC-SHA256
                                         ▼
                        ┌─────────────────────────────────────────────┐
                        │         FastAPI Ingestion Layer (main.py)    │
                        │  ① Verify HMAC signature  (security.py)     │
                        │  ② Validate Pydantic v2 schema (schemas.py) │
                        │  ③ ACK HTTP 200 in < 50 ms                  │
                        │  ④ Enqueue BackgroundTask ──────────────┐   │
                        └─────────────────────────────────────────│───┘
                                                                   │
                                         ┌─────────────────────────▼──┐
                                         │    Agent Engine             │
                                         │    (agent_engine.py)        │
                                         │                             │
                                         │  Primary:  OpenAI LLM       │
                                         │  ┌─────────────────────┐   │
                                         │  │ Classify root cause │   │
                                         │  │ Decide action       │   │
                                         │  └──────────┬──────────┘   │
                                         │             │ > 3s timeout  │
                                         │             ▼               │
                                         │  Fallback: Rule Engine      │
                                         │  ┌─────────────────────┐   │
                                         │  │ Deterministic maps  │   │
                                         │  │ (zero dependencies) │   │
                                         │  └──────────┬──────────┘   │
                                         └─────────────│───────────────┘
                                                       │
                                         ┌─────────────▼───────────────┐
                                         │   SQLite WAL Database        │
                                         │   (database.py)              │
                                         │   • transactions table       │
                                         │   • audit_logs table         │
                                         └─────────────────────────────┘
                                                       │
                                         ┌─────────────▼───────────────┐
                                         │   Streamlit Dashboard        │
                                         │   (app.py)                   │
                                         │   • Conversion Funnel        │
                                         │   • Revenue Time-Series      │
                                         │   • Root Cause Donut         │
                                         │   • Audit Trail Table        │
                                         └─────────────────────────────┘
```

---

## File Structure

```
recover_ai/
├── config.py           Pydantic BaseSettings – all env-driven configuration
├── security.py         HMAC-SHA256 webhook signature verification middleware
├── database.py         Thread-safe SQLite WAL connection pool + all queries
├── schemas.py          Pydantic v2 strict schemas (webhooks, agent I/O)
├── agent_engine.py     Async AI agent with deterministic rule-engine fallback
├── main.py             FastAPI webhook ingestion (<50 ms response SLA)
├── app.py              Streamlit Merchant Hub (Plotly WebGL dashboards)
├── data_simulator.py   HMAC-signed synthetic payment failure stream
├── requirements.txt    Pinned production dependencies
├── .env.example        Environment variable template
└── README.md           This file
```

---

## Quick Start

### 1. Install dependencies

```bash
cd recover_ai
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env if needed – defaults work for local development
```

**Key variables:**

| Variable | Default | Description |
|---|---|---|
| `RAZORPAY_WEBHOOK_SECRET` | `dev_webhook_secret_…` | Must match your Razorpay dashboard secret |
| `OPENAI_API_KEY` | _(empty)_ | Leave blank → rule engine is used (free, instant) |
| `DATABASE_PATH` | `recover_ai.db` | SQLite database file location |
| `SIMULATOR_INTERVAL_SECONDS` | `5.0` | How often the simulator fires an event |

### 3. Start the API server

```bash
uvicorn main:app --reload --port 8000
```

The API will be available at:
- Webhook endpoint: `http://127.0.0.1:8000/webhook/razorpay`
- Interactive API docs: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`

### 4. Start the simulator (new terminal)

```bash
python data_simulator.py
```

Sends a burst of 5 events immediately, then one every 5 seconds — each with a valid HMAC signature.

### 5. Launch the dashboard (new terminal)

```bash
streamlit run app.py
```

Opens at `http://localhost:8501` with live auto-refresh.

---

## Security Design

### HMAC-SHA256 Webhook Verification

Every inbound request to `POST /webhook/razorpay` is validated before any processing:

1. The raw request body is read once via a FastAPI dependency (`security.py`).
2. An HMAC-SHA256 digest is computed using the shared `RAZORPAY_WEBHOOK_SECRET`.
3. `hmac.compare_digest()` is used for **constant-time comparison** to prevent timing attacks.
4. Any request with a missing or mismatched `X-Razorpay-Signature` header is rejected with **HTTP 401** — no processing occurs.

### SQL Injection Prevention

100% of database queries use **parameterized statements** (SQLite `?` placeholders). No string interpolation is ever used to construct SQL.

### Input Validation

All inbound data is validated through **strict Pydantic v2 schemas** before being passed to any business logic. Unknown extra fields are either rejected or passed through safely depending on the model config.

---

## Concurrency Model

```
HTTP Request Thread          Background Thread Pool
       │                              │
       ├── verify_signature()         │
       ├── parse_schema()             │
       ├── BackgroundTasks.add_task() │
       └── return HTTP 200 ──────────┤
                                      ├── upsert_transaction()   [thread-local SQLite conn]
                                      ├── llm_classify() ───────► OpenAI API (3s timeout)
                                      │          └── timeout ──► rule_classify() (instant)
                                      ├── update_transaction()
                                      └── append_audit_log()
```

- The FastAPI response is sent **before** any database write or AI call — guaranteeing the <50 ms SLA.
- SQLite uses **WAL mode** (`PRAGMA journal_mode=WAL`) so background writes never block concurrent reads.
- Each OS thread gets its own SQLite connection via `threading.local()`, eliminating lock contention.

---

## Agent Engine Logic

### Classification

| Failure Code Pattern | Root Cause |
|---|---|
| `GATEWAY_ERROR`, `SERVER_ERROR`, `TIMEOUT`, `NETWORK_ERROR` | Bank Downtime |
| `PAYMENT_CANCELLED`, `USER_CANCELLED`, `BAD_REQUEST_ERROR`, `INVALID_CARD` | Customer Drop-off |
| `INSUFFICIENT_FUNDS`, `LOW_BALANCE` | Insufficient Funds |
| _(no match)_ | Unknown |

### Recovery Actions

| Root Cause | Attempt 1 | Attempt 2 |
|---|---|---|
| Bank Downtime | RETRY_PAYMENT | OFFER_ALTERNATE_UPI |
| Customer Drop-off | SEND_REMINDER_EMAIL | OFFER_EMI |
| Insufficient Funds | OFFER_EMI | NOTIFY_SUPPORT |
| Unknown | NOTIFY_SUPPORT | NOTIFY_SUPPORT |

### Business Rules

- **Maximum 2 recovery attempts** per transaction — status becomes `EXPIRED` thereafter.
- **LLM timeout hard cap: 3 seconds** — if exceeded, the rule engine fires instantly.
- All decisions are written to an **immutable `audit_logs` table** for compliance.

### Transaction Lifecycle

```
FAILED → RECOVERING → RECOVERED
                   ↘
              EXPIRED (max attempts or explicit no-action)
```

---

## Dashboard Features

| Chart | Library | Description |
|---|---|---|
| Conversion Funnel | Plotly Funnel | Ingested → Classified → Action Triggered → Recovered |
| Revenue Time-Series | Plotly Scatter + Fill | Revenue at Risk vs. Recovered with range slider & zoom |
| Root Cause Donut | Plotly Pie (hole=0.55) | Breakdown of failure classifications |
| Status Bar Chart | Plotly Bar | Live count per transaction status |
| KPI Cards | HTML/CSS | Total at Risk, Recovered, Recovery Rate %, Net Loss |
| Audit Trail | st.dataframe | Expandable compliance log with full AI reasoning |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/razorpay` | Ingest signed payment failure events |
| `GET` | `/health` | Liveness probe |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/api/stats/funnel` | Conversion funnel counts |
| `GET` | `/api/stats/root-causes` | Root cause breakdown |
| `GET` | `/api/stats/timeseries` | Revenue time-series |
| `GET` | `/api/stats/summary` | KPI summary metrics |
| `GET` | `/api/audit-logs` | Last 200 audit log entries |
| `GET` | `/api/transactions` | Last 200 transaction records |

---

## Testing the Webhook Manually

```bash
# Compute a valid signature first
BODY='{"entity":"event","event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_test001","order_id":"order_test001","amount":50000,"currency":"INR","error_code":"GATEWAY_ERROR","error_description":"Bank timeout"}}}}'
SECRET="dev_webhook_secret_change_in_production"

# On Linux/macOS:
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')

curl -X POST http://127.0.0.1:8000/webhook/razorpay \
  -H "Content-Type: application/json" \
  -H "X-Razorpay-Signature: $SIG" \
  -d "$BODY"
```

Expected response:
```json
{"status": "ok", "message": "Payment pay_test001 queued for recovery analysis."}
```

---

## Production Checklist

- [ ] Set a strong, random `RAZORPAY_WEBHOOK_SECRET` in your environment.
- [ ] Store `OPENAI_API_KEY` in a secrets manager (AWS Secrets Manager, Vault, etc.).
- [ ] Replace SQLite with PostgreSQL for multi-instance deployments.
- [ ] Add rate limiting (e.g., `slowapi`) to the webhook endpoint.
- [ ] Deploy behind TLS termination (nginx / AWS ALB).
- [ ] Set up log aggregation (CloudWatch, Datadog, etc.).
- [ ] Configure Razorpay to send webhooks only to your endpoint with HTTPS.

---

## Built With

- **FastAPI** – async web framework with dependency injection
- **Pydantic v2** – strict schema validation
- **SQLite WAL** – embedded database with concurrent-read-safe writes
- **OpenAI GPT-4o-mini** – LLM classification (optional)
- **Streamlit + Plotly** – interactive real-time dashboards
- **httpx** – async HTTP client for LLM calls and simulator
- **Python 3.11+** – modern async/await throughout
