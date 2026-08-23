# RecoverAI Enterprise
## Agentic Payment Degradation & Revenue Recovery Engine
### Razorpay AI Buildathon — Track 03

---

## What It Does

RecoverAI is a production-grade autonomous system that:

1. **Ingests** Razorpay `payment.failed` webhooks in under 30 ms
2. **Scores** every failed transaction with a LightGBM ML model (recoverability 0.0 → 1.0)
3. **Classifies** root causes via OpenAI LLM, falling back to a deterministic rule engine on timeout
4. **Executes** targeted recovery actions (retry, reminder, EMI offer, alternate UPI)
5. **Audits** every decision in a SHA-256 hash-chain ledger (tamper-detectable)
6. **Visualises** everything in a live Streamlit dashboard with Plotly WebGL charts

---

## Architecture

```
Razorpay / Simulator
        │  HTTPS POST + HMAC-SHA256
        ▼
┌─────────────────────────────────────┐
│   FastAPI Gateway  (main.py)        │  < 30 ms ACK SLA
│   ① HMAC verify   (security.py)    │
│   ② Schema check  (schemas.py)     │
│   ③ PII redaction (security.py)    │
│   ④ Queue enqueue (queue_worker.py)│
└──────────────┬──────────────────────┘
               │  asyncio.Queue
               ▼
┌─────────────────────────────────────┐
│   Agent Pipeline (agent_engine.py) │
│                                     │
│   Stage 1: ML Scoring              │  LightGBM + sklearn calibration
│            score < 0.15 → SKIP     │  (saves LLM API budget)
│                                     │
│   Stage 2: LLM Classification      │  OpenAI gpt-4o-mini (3 s timeout)
│            ↓ timeout/fail          │
│   Stage 3: Rule Engine Fallback    │  Zero-dependency, instant
│                                     │
│   Guardrails:                       │
│   • Discount cap ≤ 15%             │
│   • Max 2 recovery attempts        │
│   • PII never sent to LLM         │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│   SQLite WAL Database (database.py)│
│   • transactions table             │  INTEGER paise (no float errors)
│   • audit_logs table               │  SHA-256 hash chain
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│   Streamlit Dashboard  (app.py)    │
│   • KPI cards (risk / recovered /  │
│     rate / ML score / ledger)      │
│   • ML-Augmented Funnel chart      │
│   • Dual-trace Revenue Time-Series │
│   • Root Cause Donut chart         │
│   • ML Score Histogram             │
│   • Ledger Verification button     │
│   • Audit Trail + JSON Inspector   │
└─────────────────────────────────────┘
```

---

## File Structure

```
RecoverAI/                          ← git repo root
├── recover_ai/                     ← application package
│   ├── __init__.py
│   ├── _path.py                    ← sys.path resolver (makes flat imports work everywhere)
│   ├── config.py                   ← pydantic-settings, Streamlit Cloud secret injection
│   ├── security.py                 ← HMAC-SHA256 verify + recursive PII/PAN redaction
│   ├── database.py                 ← SQLite WAL, parameterized SQL, SHA-256 hash-chain
│   ├── schemas.py                  ← Pydantic v2 strict models (paise integers)
│   ├── ml_scorer.py                ← LightGBM + calibration, auto-train, persist/reload
│   ├── agent_engine.py             ← Async orchestrator: ML → LLM → rule fallback
│   ├── queue_worker.py             ← asyncio.Queue pool, graceful shutdown
│   ├── main.py                     ← FastAPI ingestion gateway + stats API
│   ├── app.py                      ← Streamlit dashboard (all charts + ledger verify)
│   ├── data_simulator.py           ← HMAC-signed synthetic + live event generator
│   └── requirements.txt            ← pinned package versions
├── streamlit_app.py                ← Streamlit Cloud entry point (repo root)
├── requirements.txt                ← root-level copy for cloud platforms
├── Dockerfile                      ← multi-stage, non-root, libgomp1 for LightGBM
├── docker-compose.yml              ← api + dashboard + simulator (dev profile)
├── Procfile                        ← Render / Railway / Heroku
├── render.yaml                     ← Render Blueprint (auto-deploys both services)
├── railway.toml                    ← Railway config-as-code
├── .env.example                    ← all env vars documented
├── .streamlit/
│   ├── config.toml                 ← dark theme, headless=true, CORS fix
│   └── secrets.toml.example        ← Streamlit Cloud secrets template
├── .github/workflows/deploy.yml   ← CI: syntax → import → docker build → push
├── aws/apprunner.yaml              ← AWS App Runner service config
├── azure/webapp.bicep              ← Azure App Service Bicep IaC
└── DEPLOYMENT.md                   ← step-by-step guide for every platform
```

---

## Quick Start (Local)

### 1 — Install

```bash
git clone https://github.com/YOUR_USERNAME/RecoverAI.git
cd RecoverAI
pip install -r requirements.txt
```

### 2 — Configure

```bash
cp .env.example .env
# Edit .env if needed — defaults work for local dev
```

### 3 — Start API server (Terminal 1)

```bash
uvicorn recover_ai.main:app --reload --port 8000
```

First run trains the LightGBM model (~25 s), then persists it to `recover_ai_lgbm.pkl`.

### 4 — Start dashboard (Terminal 2)

```bash
streamlit run streamlit_app.py
# Opens at http://localhost:8501
```

### 5 — Start simulator (Terminal 3)

```bash
# Continuous stream (one event every 5 s)
python recover_ai/data_simulator.py

# Quick seed burst (30 events immediately)
python recover_ai/data_simulator.py --burst 30

# Fixed count
python recover_ai/data_simulator.py --count 50 --interval 2

# Live integration test (single real-format event)
python recover_ai/data_simulator.py --live
```

### 6 — Run queue worker standalone (optional)

```bash
python recover_ai/queue_worker.py
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhook/razorpay` | Ingest HMAC-signed payment failure events |
| `GET` | `/health` | Liveness: DB, queue depth, ledger integrity |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/api/stats/funnel` | ML-augmented conversion funnel |
| `GET` | `/api/stats/root-causes` | Failure category breakdown |
| `GET` | `/api/stats/timeseries` | Per-minute revenue time-series |
| `GET` | `/api/stats/summary` | KPI summary (risk, recovered, rate, avg ML score) |
| `GET` | `/api/audit/logs` | Last 200 audit log entries |
| `GET` | `/api/audit/verify` | Run full hash-chain integrity check |
| `GET` | `/api/transactions` | Last 200 transaction records |

---

## Security Features

| Feature | Implementation |
|---------|----------------|
| Webhook auth | HMAC-SHA256 + `hmac.compare_digest` (constant-time) |
| PII masking | Recursive field + regex scanner before any DB write or LLM call |
| SQL injection | 100% parameterized queries (zero string interpolation) |
| Financial precision | All amounts stored as **INTEGER PAISE** (no floats) |
| LLM guardrails | Discount > 15% rejected and zeroed; refund terms blocked |
| Tamper detection | SHA-256 hash chain: each audit record links to previous digest |
| Non-root container | Docker uses UID 1001 |

---

## ML Pipeline

- **Model:** LightGBM (`LGBMClassifier`) + sklearn `CalibratedClassifierCV` (isotonic)
- **Fallback:** `LogisticRegression` pipeline if LightGBM is unavailable
- **Features:** `amount_rupees`, `error_code_category`, `hour_of_day`, `retry_count`
- **Training:** 5,000 synthetic samples on first run; model persisted to `recover_ai_lgbm.pkl`
- **Output:** `recoverability_score` ∈ [0.00, 1.00]
- **Low-priority guard:** Score < 0.15 → `LOW_PRIORITY_SKIP` (no LLM call, no customer contact)

---

## Agent Recovery Matrix

| Root Cause | Attempt 1 | Attempt 2 |
|------------|-----------|-----------|
| GATEWAY_DOWN | RETRY_PAYMENT | OFFER_ALTERNATE_UPI |
| NETWORK_TIMEOUT | RETRY_PAYMENT | OFFER_ALTERNATE_UPI |
| USER_CANCELLED | SEND_REMINDER | OFFER_EMI |
| INSUFFICIENT_FUNDS | OFFER_EMI | NOTIFY_SUPPORT |
| INVALID_DETAILS | SEND_REMINDER | NOTIFY_SUPPORT |
| BANK_DECLINE | OFFER_ALTERNATE_UPI | NOTIFY_SUPPORT |
| UNKNOWN | NOTIFY_SUPPORT | NO_ACTION |

After 2 attempts → `EXPIRED`.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RAZORPAY_WEBHOOK_SECRET` | `dev_secret_…` | **Required** — HMAC signing secret |
| `OPENAI_API_KEY` | _(empty)_ | Leave blank → rule engine only |
| `DATABASE_PATH` | `recover_ai_enterprise.db` | SQLite file path |
| `ML_MODEL_PATH` | `recover_ai_lgbm.pkl` | Persisted LightGBM model |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |
| `QUEUE_WORKERS` | `4` | Concurrent async workers |
| `MAX_RECOVERY_ATTEMPTS` | `2` | Hard cap per transaction |
| `ML_LOW_PRIORITY_THRESHOLD` | `0.15` | Skip score cutoff |
| `MAX_DISCOUNT_PCT` | `15.0` | LLM guardrail cap |
| `SIMULATOR_INTERVAL_SECONDS` | `5.0` | Time between simulator events |

---

## Deployment

See **[DEPLOYMENT.md](../DEPLOYMENT.md)** for full step-by-step guides for:
- Streamlit Cloud (dashboard)
- Render (API + dashboard, free tier, Blueprint)
- Railway
- AWS App Runner + ECR
- Azure App Service + Bicep
- Docker / self-hosted VPS

### Fastest zero-cost demo (15 min)

1. Push to GitHub
2. Render → New Blueprint → connects `render.yaml` → deploys API automatically
3. Streamlit Cloud → New App → file: `streamlit_app.py` → paste secrets
4. Set `WEBHOOK_BASE_URL` in Streamlit secrets to your Render API URL

---

## Running via Docker

```bash
cp .env.example .env   # fill in RAZORPAY_WEBHOOK_SECRET

# Production (API + dashboard)
docker compose up --build -d

# Development (includes simulator)
docker compose --profile dev up --build -d

# Logs
docker compose logs -f api
docker compose logs -f dashboard
```

Services: API on `:8000`, Dashboard on `:8501`.

---

## Compliance Notes

- PII is redacted **before** any database write and never sent to LLMs
- The audit ledger is append-only; no `UPDATE` or `DELETE` on `audit_logs`
- Hash chain verification can be triggered from the dashboard UI or via `GET /api/audit/verify`
- All webhook secrets and API keys must be stored in environment secrets managers in production (AWS Secrets Manager, Azure Key Vault, etc.)

---

*RecoverAI Enterprise v2.0.0 · Razorpay AI Buildathon Track 03*
