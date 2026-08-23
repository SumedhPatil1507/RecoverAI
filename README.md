<div align="center">

# 🏦 RecoverAI Enterprise
### Agentic Payment Degradation & Revenue Recovery Engine

**Razorpay AI Buildathon — Track 03**

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.39-FF4B4B?style=flat&logo=streamlit&logoColor=white)](https://streamlit.io)
[![LightGBM](https://img.shields.io/badge/LightGBM-4.5-2E86AB?style=flat)](https://lightgbm.readthedocs.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

*An autonomous, production-grade AI system that detects failed payments, scores recoverability with ML, classifies root causes via LLM, and executes targeted recovery actions — all within a sub-30 ms webhook response window.*

[**Live Dashboard →**](https://recoverai-enterprise.streamlit.app) &nbsp;|&nbsp; [**API Docs →**](https://recoverai-api.onrender.com/docs) &nbsp;|&nbsp; [**Deployment Guide →**](DEPLOYMENT.md)

</div>

---

## 📸 Dashboard Preview

```
┌──────────────────────────────────────────────────────────────────┐
│  🏦 RecoverAI Enterprise — Intelligence Hub                      │
├────────────┬────────────┬────────────┬────────────┬─────────────┤
│ 💰 At Risk │ ✅Recovered│ 📈 Rate    │ 🤖 ML Score│ 🔒 Ledger  │
│ ₹3,98,832  │ ₹2,11,410  │  52.0%     │   0.423    │ VERIFIED   │
├────────────┴────────────┴────────────┴────────────┴─────────────┤
│  Recovery Funnel    │         Root Cause Donut                   │
│  Ingested  50       │  USER_CANCELLED  34%                       │
│  ML Scored 50       │  NETWORK_TIMEOUT 22%                       │
│  Evaluated 50       │  INSUFF_FUNDS    18%                       │
│  Triggered 50       │  GATEWAY_DOWN    14%                       │
│  Recovered 26 ✅    │  BANK_DECLINE     8%                       │
├─────────────────────────────────────────────────────────────────┤
│  Revenue at Risk vs Recovered — Live Time-Series (WebGL)         │
│  [range slider + zoom + 5m / 30m / 1h / All selectors]          │
├─────────────────────────────────────────────────────────────────┤
│  🔐 Ledger: [Verify Integrity] → 100% IMMUTABLE & VERIFIED       │
│  📜 Audit Trail — SHA-256 hash chain — expandable JSON rows      │
└─────────────────────────────────────────────────────────────────┘
```

---

## ✨ What It Does

| Stage | Component | Detail |
|---|---|---|
| **1. Ingest** | FastAPI webhook gateway | HMAC-SHA256 verified, PII redacted, HTTP 200 in **< 30 ms** |
| **2. Score** | LightGBM ML model | Recoverability score 0.00 – 1.00; score < 0.15 → skip (saves LLM budget) |
| **3. Classify** | OpenAI gpt-4o-mini | Root cause classification with 3s hard timeout |
| **4. Fallback** | Deterministic rule engine | Instant, zero-dependency — fires when LLM times out |
| **5. Recover** | Action dispatcher | RETRY, REMINDER, EMI, ALT-UPI, SUPPORT — max 2 attempts per txn |
| **6. Audit** | SHA-256 hash-chain ledger | Every decision linked cryptographically; tamper-detectable |
| **7. Visualise** | Streamlit + Plotly WebGL | Live dashboard with funnel, time-series, donut, histogram |

---

## 🏗 Architecture

```
Razorpay Webhook / Simulator
         │  HTTPS POST + X-Razorpay-Signature
         ▼
┌──────────────────────────────────────────────┐
│  FastAPI Gateway          main.py            │
│  ① HMAC-SHA256 verify    security.py        │  < 30 ms ACK
│  ② Pydantic v2 validate  schemas.py         │
│  ③ PII redaction         security.py        │
│  ④ asyncio.Queue enqueue queue_worker.py    │
└─────────────────────┬────────────────────────┘
                      │  Non-blocking queue
                      ▼
┌──────────────────────────────────────────────┐
│  Agent Pipeline       agent_engine.py        │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │  Stage 1: LightGBM ML Scorer         │   │
│  │  features: amount, error_code,       │   │
│  │           hour_of_day, retry_count   │   │
│  │  score < 0.15 → LOW_PRIORITY_SKIP    │   │
│  └───────────────────┬──────────────────┘   │
│                      ▼                       │
│  ┌──────────────────────────────────────┐   │
│  │  Stage 2: LLM Agent (3s timeout)     │   │
│  │  OpenAI gpt-4o-mini                  │   │
│  │  Guardrails: discount ≤ 15%,         │   │
│  │             no refund terms          │   │
│  └───────────────────┬──────────────────┘   │
│           timeout ↙  │ ↘ success            │
│  ┌──────────────┐    │                       │
│  │ Rule Engine  │    │                       │
│  │ (instant)    │    │                       │
│  └──────────────┘    │                       │
└─────────────────────┬┴───────────────────────┘
                      ▼
┌──────────────────────────────────────────────┐
│  SQLite WAL Database      database.py        │
│  • transactions  (INTEGER paise — no floats) │
│  • audit_logs    (SHA-256 hash chain)        │
└─────────────────────┬────────────────────────┘
                      ▼
┌──────────────────────────────────────────────┐
│  Streamlit Dashboard      app.py             │
│  Plotly WebGL charts — auto-refresh 5s       │
└──────────────────────────────────────────────┘
```

---

## 📁 Repository Structure

```
RecoverAI/
│
├── streamlit_app.py          ← Streamlit Cloud entry point (REPO ROOT)
├── requirements.txt          ← pinned deps for all cloud platforms
├── packages.txt              ← apt: libgomp1 (LightGBM on Linux)
├── runtime.txt               ← python-3.11
├── Dockerfile                ← multi-stage, non-root (UID 1001)
├── docker-compose.yml        ← api + dashboard + simulator (dev profile)
├── Procfile                  ← Render / Railway / Heroku
├── render.yaml               ← Render Blueprint (auto-deploys API + dashboard)
├── railway.toml              ← Railway config-as-code
├── .env.example              ← all environment variables documented
│
├── .streamlit/
│   ├── config.toml           ← dark theme, headless=true, CORS fix
│   └── secrets.toml.example  ← Streamlit Cloud secrets template
│
├── .github/
│   └── workflows/deploy.yml  ← CI: syntax check → import test → docker build → push
│
├── aws/
│   └── apprunner.yaml        ← AWS App Runner service definition
│
├── azure/
│   └── webapp.bicep          ← Azure App Service Bicep IaC
│
└── recover_ai/               ← Application package
    ├── __init__.py
    ├── _path.py              ← sys.path resolver (flat imports work everywhere)
    ├── config.py             ← pydantic-settings + Streamlit Cloud secret injection
    ├── security.py           ← HMAC-SHA256 verify + recursive PII/PAN redaction
    ├── database.py           ← SQLite WAL, thread-safe pool, SHA-256 hash-chain ledger
    ├── schemas.py            ← Pydantic v2 models (all amounts in INTEGER paise)
    ├── ml_scorer.py          ← LightGBM + CalibratedClassifierCV, auto-train & persist
    ├── agent_engine.py       ← Async orchestrator: ML → LLM → rule fallback
    ├── queue_worker.py       ← asyncio.Queue pool, graceful shutdown
    ├── main.py               ← FastAPI gateway + /api/stats/* endpoints
    ├── app.py                ← Streamlit dashboard (all charts + ledger verify)
    ├── data_simulator.py     ← HMAC-signed synthetic + live event generator
    └── requirements.txt      ← package-level pinned deps
```

---

## 🚀 Quick Start (Local)

### Prerequisites

- Python 3.11+
- Git

### 1. Clone & Install

```bash
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Defaults work for local dev — no changes needed
```

### 3. Start the API (Terminal 1)

```bash
uvicorn recover_ai.main:app --reload --port 8000
```

> First run trains the LightGBM model (~25 s) and saves it to `recover_ai_lgbm.pkl`.  
> All subsequent starts load the pre-trained model instantly.

### 4. Start the Dashboard (Terminal 2)

```bash
streamlit run streamlit_app.py
# → http://localhost:8501
```

### 5. Send Events (Terminal 3)

```bash
# Continuous stream — one event every 5 seconds
python recover_ai/data_simulator.py

# Instant burst — seeds the dashboard immediately
python recover_ai/data_simulator.py --burst 50

# Fixed count
python recover_ai/data_simulator.py --count 100 --interval 2

# Single live-integration test event
python recover_ai/data_simulator.py --live
```

### 6. Verify the Ledger

Open `http://localhost:8501` → click **"Verify Ledger Integrity"** → see the green badge.

Or via API:

```bash
curl http://localhost:8000/api/audit/verify
# {"ok": true, "message": "100% IMMUTABLE & VERIFIED — 100 records validated."}
```

---

## 🌐 Deploying to Streamlit Cloud

**Fill in the Streamlit Cloud form:**

| Field | Value |
|---|---|
| Repository | `SumedhPatil1507/RecoverAI` |
| Branch | `main` |
| **Main file path** | **`streamlit_app.py`** |
| App URL | `recoverai-enterprise` |

**Advanced settings → Secrets** — paste:

```toml
RAZORPAY_WEBHOOK_SECRET   = "your_webhook_secret"
OPENAI_API_KEY            = ""
DATABASE_PATH             = "recover_ai_enterprise.db"
ML_MODEL_PATH             = "recover_ai_lgbm.pkl"
ENVIRONMENT               = "production"
MAX_RECOVERY_ATTEMPTS     = "2"
ML_LOW_PRIORITY_THRESHOLD = "0.15"
MAX_DISCOUNT_PCT          = "15.0"
QUEUE_WORKERS             = "4"
WEBHOOK_BASE_URL          = "https://your-api.onrender.com"
DASHBOARD_REFRESH_SECONDS = "5"
```

> `OPENAI_API_KEY` can be left blank — the deterministic rule engine handles everything at zero cost.

Click **Deploy** → live at `https://recoverai-enterprise.streamlit.app`

---

## 🔧 All Deployment Platforms

| Platform | What deploys | Free | Config file |
|---|---|---|---|
| **Streamlit Cloud** | Dashboard | ✅ | `streamlit_app.py` + secrets UI |
| **Render** | API + Dashboard | ✅ (sleeps) | `render.yaml` (Blueprint) |
| **Railway** | API | ✅ ($5 credit) | `railway.toml` |
| **AWS App Runner** | API (Docker) | ❌ | `aws/apprunner.yaml` |
| **Azure App Service** | API + Dashboard | ❌ (F1 free) | `azure/webapp.bicep` |
| **Docker / VPS** | Full stack | ✅ | `docker-compose.yml` |

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for step-by-step instructions for every platform.

### Docker (fastest full-stack)

```bash
cp .env.example .env          # set RAZORPAY_WEBHOOK_SECRET

# Production: API + dashboard
docker compose up --build -d

# Development: adds the simulator
docker compose --profile dev up --build -d
```

- API → `http://localhost:8000`
- Dashboard → `http://localhost:8501`

---

## 🔌 API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/webhook/razorpay` | Ingest HMAC-signed `payment.failed` event |
| `GET` | `/health` | Liveness: DB ✓, queue depth, ledger integrity |
| `GET` | `/docs` | Swagger UI (interactive) |
| `GET` | `/api/stats/funnel` | ML-augmented conversion funnel counts |
| `GET` | `/api/stats/root-causes` | Failure category breakdown |
| `GET` | `/api/stats/timeseries` | Per-minute revenue time-series |
| `GET` | `/api/stats/summary` | KPI summary (risk, recovered, rate, ML score) |
| `GET` | `/api/audit/logs` | Last 200 audit log entries |
| `GET` | `/api/audit/verify` | Run full SHA-256 hash-chain integrity check |
| `GET` | `/api/transactions` | Last 200 transaction records |

### Test the Webhook Manually

```bash
# Compute a valid signature
BODY='{"entity":"event","event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_test01","order_id":"order_test01","amount":500000,"currency":"INR","error_code":"GATEWAY_ERROR","error_description":"Bank timeout"}}}}'
SECRET="dev_secret_replace_in_production"

# Linux / macOS
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')
curl -X POST http://localhost:8000/webhook/razorpay \
  -H "Content-Type: application/json" \
  -H "X-Razorpay-Signature: $SIG" \
  -d "$BODY"
```

Expected response:
```json
{
  "status": "ok",
  "message": "Payment pay_test01 queued for recovery analysis.",
  "payment_id": "pay_test01"
}
```

---

## 🔒 Security Design

### HMAC-SHA256 Webhook Verification

Every request to `POST /webhook/razorpay` goes through:

```
raw_body  →  HMAC-SHA256(secret)  →  hmac.compare_digest()  →  HTTP 401 or proceed
```

`hmac.compare_digest` is used throughout — **constant-time comparison** to prevent timing attacks.

### PII Redaction

Before any DB write or LLM call, `redact_pii()` recursively walks the payload:

| Raw value | After redaction |
|---|---|
| `john.doe@gmail.com` | `j**n.d**@gmail.com` |
| `4111 1111 1111 1111` | `**** **** **** 1111` |
| `+919876543210` | `******3210` |

### SQL Injection Prevention

100% parameterized queries — zero string interpolation in SQL. All values passed as positional `?` parameters.

### Financial Precision

All amounts stored as **INTEGER PAISE** (`amount_paise INT`). No `FLOAT` columns for money. Conversion to `Decimal` happens only at the display layer.

### LLM Guardrails

```python
if discount_pct > 15.0:
    # Rejected — zeroed and flagged in audit log
    discount_pct = 0.0
    reasoning += " [GUARDRAIL: proposed discount rejected]"
```

### SHA-256 Hash-Chain Audit Ledger

```
log_id=1  previous_hash=GENESIS      current_hash=sha256(GENESIS + data_1)
log_id=2  previous_hash=hash_1       current_hash=sha256(hash_1  + data_2)
log_id=3  previous_hash=hash_2       current_hash=sha256(hash_2  + data_3)
...
```

Any modification to any row breaks the chain — detected instantly by `verify_audit_integrity()`.

---

## 🤖 ML Pipeline

| Property | Value |
|---|---|
| Model | `LightGBMClassifier` + `CalibratedClassifierCV(method="isotonic")` |
| Fallback | `LogisticRegression` pipeline (if LightGBM unavailable) |
| Training data | 5,000 synthetic samples (generated on first run) |
| Features | `amount_rupees`, `error_code_category`, `hour_of_day`, `retry_count` |
| Output | `recoverability_score` ∈ [0.00, 1.00] |
| Persistence | Auto-saved to `recover_ai_lgbm.pkl`; loaded on subsequent starts |
| Low-priority guard | Score < `0.15` → `LOW_PRIORITY_SKIP` (no LLM call, no customer contact) |

### Recovery decision matrix (rule engine)

| Root Cause | Attempt 1 | Attempt 2 |
|---|---|---|
| `GATEWAY_DOWN` | `RETRY_PAYMENT` | `OFFER_ALTERNATE_UPI` |
| `NETWORK_TIMEOUT` | `RETRY_PAYMENT` | `OFFER_ALTERNATE_UPI` |
| `USER_CANCELLED` | `SEND_REMINDER` | `OFFER_EMI` |
| `INSUFFICIENT_FUNDS` | `OFFER_EMI` | `NOTIFY_SUPPORT` |
| `INVALID_DETAILS` | `SEND_REMINDER` | `NOTIFY_SUPPORT` |
| `BANK_DECLINE` | `OFFER_ALTERNATE_UPI` | `NOTIFY_SUPPORT` |
| `UNKNOWN` | `NOTIFY_SUPPORT` | `NO_ACTION` |

After 2 attempts → `EXPIRED`

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `RAZORPAY_WEBHOOK_SECRET` | `dev_secret_…` | **Required** — HMAC signing secret |
| `OPENAI_API_KEY` | *(empty)* | Leave blank → rule engine only (free) |
| `LLM_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `LLM_TIMEOUT_SECONDS` | `3.0` | Hard timeout before rule engine fires |
| `DATABASE_PATH` | `recover_ai_enterprise.db` | SQLite file path |
| `ML_MODEL_PATH` | `recover_ai_lgbm.pkl` | Persisted LightGBM model path |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |
| `QUEUE_WORKERS` | `4` | Concurrent async processing workers |
| `MAX_RECOVERY_ATTEMPTS` | `2` | Hard cap per transaction |
| `ML_LOW_PRIORITY_THRESHOLD` | `0.15` | Skip score cutoff |
| `MAX_DISCOUNT_PCT` | `15.0` | LLM guardrail cap |
| `SIMULATOR_INTERVAL_SECONDS` | `5.0` | Seconds between simulator events |
| `WEBHOOK_BASE_URL` | `http://127.0.0.1:8000` | Used by simulator |
| `DASHBOARD_REFRESH_SECONDS` | `5` | Auto-refresh interval |

---

## 🧪 CI/CD Pipeline

Every push to `main` triggers:

```
Push to main
    │
    ▼
Syntax check (py_compile all 10 modules)
    │
    ▼
Import smoke test (config, schemas, database, security, ml_scorer)
    │
    ▼
Docker build (multi-stage, validates Dockerfile + deps)
    │
    ▼
Push to Docker Hub (main branch only, on success)
```

Configure these GitHub Actions secrets for the push step:

| Secret | Value |
|---|---|
| `RAZORPAY_WEBHOOK_SECRET` | your webhook secret |
| `OPENAI_API_KEY` | your OpenAI key (optional) |
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |

---

## 📋 Production Checklist

- [ ] Set a strong random `RAZORPAY_WEBHOOK_SECRET` (use `openssl rand -hex 32`)
- [ ] Store all secrets in a secrets manager (AWS Secrets Manager, Azure Key Vault)
- [ ] Replace SQLite with PostgreSQL for multi-instance / persistent deployments
- [ ] Add rate limiting (`slowapi`) to the webhook endpoint
- [ ] Deploy API behind TLS termination (nginx / AWS ALB / Cloudflare)
- [ ] Set `ENVIRONMENT=production` in all prod deployments
- [ ] Set up log aggregation (CloudWatch, Datadog, Grafana Loki)
- [ ] Configure Razorpay to send webhooks only to your HTTPS endpoint
- [ ] Enable Render/Railway health-check alerts for the `/health` endpoint

---

## 📦 Tech Stack

| Layer | Technology |
|---|---|
| **API Framework** | FastAPI 0.115 + Uvicorn |
| **Validation** | Pydantic v2 (strict mode) |
| **ML** | LightGBM 4.5 + scikit-learn CalibratedClassifierCV |
| **LLM** | OpenAI gpt-4o-mini (optional) |
| **Database** | SQLite with WAL mode + thread-local connection pool |
| **Async Queue** | `asyncio.Queue` with worker pool |
| **Dashboard** | Streamlit 1.39 + Plotly 5.24 (WebGL) |
| **HTTP Client** | httpx (async, for LLM calls + simulator) |
| **Resilience** | tenacity (exponential backoff retries) |
| **Containerisation** | Docker multi-stage (python:3.11-slim) |
| **CI/CD** | GitHub Actions |

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

<div align="center">

**RecoverAI Enterprise v2.0.0**

Built for the Razorpay AI Buildathon · Track 03

*FastAPI · SQLite WAL · LightGBM · SHA-256 Ledger · Streamlit · Plotly WebGL*

</div>
