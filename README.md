<div align="center">

# 🏦 RecoverAI Enterprise
### Agentic Payment Degradation & Revenue Recovery Engine

[![Razorpay AI Buildathon](https://img.shields.io/badge/Razorpay-AI%20Buildathon%202024-0D2463?style=for-the-badge&logo=razorpay&logoColor=white)](https://razorpay.com)
[![Track 03](https://img.shields.io/badge/Track-03%20Payment%20Intelligence-3ECF8E?style=for-the-badge)](https://razorpay.com)
[![Streamlit App](https://img.shields.io/badge/Streamlit-Live%20Dashboard-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)](https://recoverai-enterprise.streamlit.app)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LightGBM](https://img.shields.io/badge/LightGBM-ML%20Scoring-blue?style=for-the-badge)](https://lightgbm.readthedocs.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

**RecoverAI autonomously detects failed payments, scores them with a LightGBM ML model, classifies root causes via an AI agent, executes targeted recovery actions, and logs every decision in a tamper-proof cryptographic audit ledger — all in under 30 ms per webhook.** The webhook event model follows Razorpay's documented `payment.failed` event, which is triggered when a payment fails [1]. The latency figure is an application target measured by this project, not a Razorpay service-level guarantee.

[🚀 Live Demo](https://recoverai-enterprise.streamlit.app) · [📖 API Docs](https://recoverai-api.onrender.com/docs) · [📋 Deployment Guide](DEPLOYMENT.md)

</div>

---

## 📸 Dashboard Preview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  🏦 RecoverAI Enterprise — Intelligence Hub                              │
│  Updated: 10:48:18  ·  Razorpay AI Buildathon · Track 03               │
├─────────────┬──────────────┬──────────────┬────────────┬────────────────┤
│ 💰 At Risk  │ ✅ Recovered │ 📈 Rate      │ 🤖 ML Score│ 🔐 Ledger     │
│ ₹3,98,832   │ ₹2,11,410   │ 52.0%        │ 0.423      │ 🔒 VERIFIED   │
├─────────────┴──────────────┴──────────────┴────────────┴────────────────┤
│ 🔽 ML-Augmented Funnel     │ 🍩 Root Cause Breakdown                    │
│  Ingested     → 50         │  USER_CANCELLED  ████████  34%             │
│  ML Scored    → 50         │  NETWORK_TIMEOUT █████     22%             │
│  Agent Eval   → 50         │  INSUFF_FUNDS    ████      18%             │
│  Action Trig  → 50         │  GATEWAY_DOWN    ███       14%             │
│  Recovered    → 26         │  BANK_DECLINE    ██         8%             │
├────────────────────────────┴────────────────────────────────────────────┤
│ 📊 Revenue at Risk vs Recovered  [WebGL Time-Series with Range Slider]  │
│  ₹15K ▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░                         │
│  ₹10K  ░░▓▓░░▓▓▓░░░░░░▓▓░░░░░░░░░░░░░░░░░░░░ ← At Risk               │
│   ₹5K   ░░░░░░░▓░░░▓░░░░░░▓▓▓░░▓░░░░░░░░░░░░ ← Recovered             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## ✨ Key Features

| Feature | Implementation |
|---|---|
| ⚡ **Sub-30ms Webhook ACK** | FastAPI + `asyncio.Queue` — response before any DB/AI work; FastAPI documents database-backed applications and SQLite as a single-file option with Python support [4]. |
| 🤖 **ML Recoverability Scoring** | LightGBM + calibrated probabilities (0.00→1.00); score < 0.15 → skip. LightGBM is the model library used by this project [10]. |
| 🧠 **AI Recovery Agent** | OpenAI GPT-4o-mini with 3s hard timeout → instant rule-engine fallback |
| 🔒 **Tamper-Proof Audit Ledger** | SHA-256 hash chain — every decision cryptographically linked. SHA-256 is provided by Python's standard `hashlib` library [6], while SQLite WAL behavior is documented by SQLite [8]. |
| 🛡️ **Webhook and Data Protections** | HMAC-SHA256 webhook auth, recursive PII/PAN masking, parameterized SQL. Python documents the `hmac` module for keyed message authentication [7], and Razorpay documents webhook validation and testing [2]. “PCI-DSS Ready” is an implementation goal, not a compliance certification. |
| 💰 **Financial Precision** | All amounts as **INTEGER PAISE** — zero floating-point errors within the integer representation |
| 📊 **Live Plotly Dashboard** | Funnel, time-series, donut, histogram, dataframes, and controls use Streamlit and Plotly APIs [3] [9] |
| 🔄 **Resilient by Design** | LLM timeout → rule engine, exponential backoff retries via `tenacity` |

---

## 🏗️ System Architecture

```
                        ┌─────────────────────────────────┐
                        │    Razorpay Webhook / Simulator  │
                        └──────────────┬──────────────────┘
                                       │  HTTPS POST + HMAC-SHA256
                                       ▼
              ┌────────────────────────────────────────────────┐
              │         FastAPI Ingestion Gateway               │  < 30ms ACK
              │   ① HMAC-SHA256 signature verify               │
              │   ② Pydantic v2 strict schema validation       │
              │   ③ Recursive PII/PAN redaction                │
              │   ④ asyncio.Queue enqueue (non-blocking)       │
              └────────────────────┬───────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │     Async Worker Pool (×4)       │
                    │                                  │
                    │  ┌─ Stage 1: ML Scoring ───────┐ │
                    │  │  LightGBM + Calibration      │ │
                    │  │  score < 0.15 → SKIP         │ │
                    │  └────────────┬────────────────┘ │
                    │               │                  │
                    │  ┌─ Stage 2: LLM Agent ────────┐ │
                    │  │  OpenAI GPT-4o-mini          │ │
                    │  │  3s hard timeout             │ │
                    │  │  Guardrail: discount ≤ 15%   │ │
                    │  └────────────┬────────────────┘ │
                    │               │ timeout / fail   │
                    │  ┌─ Stage 3: Rule Engine ──────┐ │
                    │  │  Zero-dependency fallback    │ │
                    │  │  Deterministic action matrix │ │
                    │  └────────────┬────────────────┘ │
                    └───────────────┼──────────────────┘
                                    │
              ┌─────────────────────▼─────────────────────────┐
              │          SQLite WAL Database                    │
              │  transactions  (INTEGER paise, status, score)  │
              │  audit_logs    (SHA-256 hash chain — immutable)│
              └─────────────────────┬─────────────────────────┘
                                    │
              ┌─────────────────────▼─────────────────────────┐
              │       Streamlit Enterprise Dashboard           │
              │  KPI Cards · Funnel · Time-Series · Donut      │
              │  ML Score Histogram · Ledger Verification      │
              └────────────────────────────────────────────────┘
```

---

## 📁 Repository Structure

```
RecoverAI/
│
├── streamlit_app.py          ← 🎯 Streamlit Cloud entry point (REPO ROOT)
├── requirements.txt          ← Pinned production dependencies
├── packages.txt              ← Linux apt packages (libgomp1 for LightGBM)
├── runtime.txt               ← Python 3.11 pin for Streamlit Cloud
├── Procfile                  ← Render / Railway process declaration
├── render.yaml               ← Render Blueprint (auto-deploys both services)
├── railway.toml              ← Railway config-as-code
├── docker-compose.yml        ← API + Dashboard + optional Simulator
├── Dockerfile                ← Multi-stage build, non-root user, libgomp1
├── .env.example              ← All environment variables documented
│
├── recover_ai/               ← Application package
│   ├── __init__.py
│   ├── _path.py              ← sys.path resolver (flat imports everywhere)
│   ├── config.py             ← pydantic-settings + Streamlit Cloud injection
│   ├── security.py           ← HMAC-SHA256 verify + recursive PII redaction
│   ├── database.py           ← SQLite WAL, parameterized SQL, SHA-256 ledger
│   ├── schemas.py            ← Pydantic v2 strict models (integer paise)
│   ├── ml_scorer.py          ← LightGBM pipeline, auto-train, persist/reload
│   ├── agent_engine.py       ← Async orchestrator: ML → LLM → rule fallback
│   ├── queue_worker.py       ← asyncio.Queue pool, graceful shutdown
│   ├── main.py               ← FastAPI gateway + /api/stats/* endpoints
│   ├── app.py                ← Streamlit dashboard (all charts + ledger UI)
│   ├── data_simulator.py     ← HMAC-signed synthetic + live event generator
│   └── requirements.txt      ← Package mirror (for recover_ai-only deploys)
│
├── .streamlit/
│   ├── config.toml           ← Dark theme, headless=true, CORS config
│   └── secrets.toml.example  ← Template for Streamlit Cloud Secrets UI
│
├── .github/workflows/
│   └── deploy.yml            ← CI: syntax check → import test → Docker build
│
├── aws/
│   └── apprunner.yaml        ← AWS App Runner service config
│
└── azure/
    └── webapp.bicep          ← Azure App Service Infrastructure-as-Code
```

---

## 🚀 Quick Start (Local)

### Prerequisites
- Python 3.11+
- Git

### 1 — Clone & Install

```bash
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI
pip install -r requirements.txt
```

### 2 — Configure

```bash
cp .env.example .env
# Defaults work for local dev — no changes needed to start
```

### 3 — Start the API (Terminal 1)

```bash
uvicorn recover_ai.main:app --reload --port 8000
```

> First run trains LightGBM on ~5,000 synthetic samples (~25s), then persists the model to `recover_ai_lgbm.pkl`. Subsequent starts load in milliseconds.

### 4 — Start the Dashboard (Terminal 2)

```bash
streamlit run streamlit_app.py
# → Opens at http://localhost:8501
```

The dashboard now starts with **Live transaction feed enabled**. Every refresh creates one Razorpay-shaped failed payment, sends it through the existing ML → root-cause → recovery pipeline, and refreshes the KPI cards, failure breakdown, funnel, and time-series. Use the sidebar toggle to pause the feed or change the refresh interval. This makes the dashboard self-contained on Streamlit Cloud; it no longer depends on a separately running API or simulator just to populate the charts.

### 5 — Optional external webhook simulator (Terminal 3)

The external simulator remains available for testing the FastAPI webhook endpoint and HMAC verification:

```bash
# Burst-seed 50 events immediately (best for API demo)
python recover_ai/data_simulator.py --burst 50

# Continuous stream (one event every 5 seconds)
python recover_ai/data_simulator.py

# Exactly N events
python recover_ai/data_simulator.py --count 100 --interval 2

# Single live-format integration test
python recover_ai/data_simulator.py --live
```

To use only real webhook traffic, turn **Live transaction feed** off in the sidebar and run the API plus simulator separately.

---

## 🌐 Streamlit Cloud Deployment

### Fill in the Deploy form exactly as follows:

| Field | Value |
|---|---|
| **Repository** | `SumedhPatil1507/RecoverAI` |
| **Branch** | `main` |
| **Main file path** | `streamlit_app.py` |
| **App URL** | `recoverai-enterprise` |

### Advanced Settings → Secrets

Paste this block into the Secrets text box:

```toml
RAZORPAY_WEBHOOK_SECRET   = "dev_secret_replace_in_production"
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

> `OPENAI_API_KEY` can be left blank — the deterministic rule engine handles classification for free. The dashboard's built-in live feed also works without an OpenAI key and uses synthetic Razorpay-shaped test events; connect a production webhook endpoint before treating the displayed data as operational payment data.

---

## 🐳 Docker Deployment

```bash
cp .env.example .env          # set RAZORPAY_WEBHOOK_SECRET

# Production (API + Dashboard)
docker compose up --build -d

# Development (adds live simulator)
docker compose --profile dev up --build -d

# View logs
docker compose logs -f api
docker compose logs -f dashboard
```

| Service | Port | URL |
|---|---|---|
| FastAPI API | `8000` | `http://localhost:8000/docs` |
| Streamlit Dashboard | `8501` | `http://localhost:8501` |

---

## 🔌 API Reference

### Webhook Ingestion

```http
POST /webhook/razorpay
Content-Type: application/json
X-Razorpay-Signature: <hmac-sha256-hex>
```

Response (< 30ms):
```json
{
  "status": "ok",
  "message": "Payment pay_abc123 queued for recovery analysis.",
  "payment_id": "pay_abc123"
}
```

### Stats Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness: DB status, queue depth, ledger integrity |
| `GET /docs` | Interactive Swagger UI |
| `GET /api/stats/funnel` | ML-augmented 5-stage conversion funnel |
| `GET /api/stats/root-causes` | Failure category breakdown |
| `GET /api/stats/timeseries` | Per-minute revenue time-series |
| `GET /api/stats/summary` | KPIs: risk, recovered, recovery rate, avg ML score |
| `GET /api/audit/logs` | Last 200 immutable audit log entries |
| `GET /api/audit/verify` | Run full SHA-256 hash-chain integrity verification |
| `GET /api/transactions` | Last 200 transaction records |

---

## 🛡️ Security Design

### HMAC-SHA256 Webhook Authentication
Every request to `POST /webhook/razorpay` is verified using constant-time comparison (`hmac.compare_digest`) before any processing. Requests with missing or invalid `X-Razorpay-Signature` headers are rejected with HTTP 401. The keyed-authentication primitive and constant-time comparison are provided by Python's `hmac` module [7], while Razorpay's validation guidance is documented in [2].

### Recursive PII / PAN Masking
Before any database write or LLM call, all sensitive fields are masked:

```python
# Input
{"email": "john.doe@example.com", "card_number": "4111111111111111"}

# After redact_pii()
{"email": "j****.***@example.com", "card_number": "**** **** **** 1111"}
```

Fields covered: `card_number`, `email`, `phone`, `contact`, `cvv`, `vpa`, `name`, `address` + regex-based value-level detection.

### SHA-256 Hash-Chain Audit Ledger
Every AI decision writes an immutable record:

```
log_id=1  prev=GENESIS         → hash=a3f9...
log_id=2  prev=a3f9...         → hash=7bc2...
log_id=3  prev=7bc2...         → hash=e441...
```

Any modification to a past record breaks the chain and is detected by `GET /api/audit/verify`. The implementation uses SHA-256 from Python's standard `hashlib` library [6].

### SQL Injection Prevention
100% parameterized queries throughout `database.py` — zero string interpolation in SQL.

### Financial Precision
All monetary values stored as `INTEGER PAISE` (₹500 = `50000`). No floating-point arithmetic on revenue figures.

---

## 🤖 ML Pipeline

```
Input Features:
  amount_rupees      → float  (₹500 – ₹15,000)
  error_code_category → int   (0=GATEWAY_DOWN … 6=UNKNOWN)
  hour_of_day        → int   (0–23)
  retry_count        → int   (0–2)

Model:
  LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=31)
  + CalibratedClassifierCV(method="isotonic", cv=3)

Output:
  recoverability_score ∈ [0.00, 1.00]
  
  score < 0.15  →  LOW_PRIORITY_SKIP  (no LLM call, no customer contact)
  score ≥ 0.15  →  Agent evaluation proceeds
```

Training: 5,000 synthetic samples on first run (~25s), then auto-loaded from `recover_ai_lgbm.pkl`. The model implementation uses LightGBM [10] and scikit-learn utilities [11]; the sample count and timing are project-specific measurements.

---

## 📋 Recovery Action Matrix

| Root Cause | Attempt 1 | Attempt 2 |
|---|---|---|
| `GATEWAY_DOWN` | `RETRY_PAYMENT` | `OFFER_ALTERNATE_UPI` |
| `NETWORK_TIMEOUT` | `RETRY_PAYMENT` | `OFFER_ALTERNATE_UPI` |
| `USER_CANCELLED` | `SEND_REMINDER` | `OFFER_EMI` |
| `INSUFFICIENT_FUNDS` | `OFFER_EMI` | `NOTIFY_SUPPORT` |
| `INVALID_DETAILS` | `SEND_REMINDER` | `NOTIFY_SUPPORT` |
| `BANK_DECLINE` | `OFFER_ALTERNATE_UPI` | `NOTIFY_SUPPORT` |
| `UNKNOWN` | `NOTIFY_SUPPORT` | `NO_ACTION` |

After 2 attempts → status: `EXPIRED`.

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `RAZORPAY_WEBHOOK_SECRET` | `dev_secret_…` | **Required** — HMAC signing secret |
| `OPENAI_API_KEY` | _(empty)_ | Optional — leave blank for rule engine only |
| `DATABASE_PATH` | `recover_ai_enterprise.db` | SQLite file location |
| `ML_MODEL_PATH` | `recover_ai_lgbm.pkl` | LightGBM model pickle |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |
| `QUEUE_WORKERS` | `4` | Concurrent async agent workers |
| `MAX_RECOVERY_ATTEMPTS` | `2` | Hard cap per transaction |
| `ML_LOW_PRIORITY_THRESHOLD` | `0.15` | Score below which transaction is skipped |
| `MAX_DISCOUNT_PCT` | `15.0` | LLM guardrail cap (discount > 15% rejected) |
| `SIMULATOR_INTERVAL_SECONDS` | `5.0` | Seconds between simulator events |
| `DASHBOARD_REFRESH_SECONDS` | `5` | Auto-refresh interval |
| `COPILOT_MODEL` | `gpt-4o-mini` | Merchant Copilot model |
| `SLACK_WEBHOOK_URL` | _(empty)_ | Optional anomaly-alert webhook |
| `SMTP_HOST` / `ALERT_EMAIL_TO` | _(empty)_ | Optional email anomaly alerts |
| `DATABASE_URL` | _(empty)_ | Reserved for PostgreSQL deployment configuration |

---

## 🧩 AI Agent Modules

RecoverAI now exposes five integrated modules through FastAPI and the Streamlit dashboard. The Merchant Copilot uses local retrieval over transaction and audit records, generates read-only SQL, validates it against an allowlist, and explains the returned financial metrics. When `OPENAI_API_KEY` is configured, it can use the configured Copilot model; without a key, deterministic query templates keep the dashboard functional. LangGraph makes the retrieval → query → validation workflow inspectable [12].

The Explainable AI module provides per-transaction additive explanations, feature-importance views, SHAP waterfall and beeswarm plots when SHAP is available, and downloadable PDF reports. SHAP's documentation describes waterfall plots for individual predictions and beeswarm plots as dense summaries of feature impact [13] [14]. The Recovery Optimization Agent recommends a retry window, retry count, payment method, expected success probability, and expected recovered revenue using contextual Thompson Sampling over method and timing arms. The bandit calculations are implemented in this repository; the model outputs are estimates for decision support, not guarantees. Its simulation reports recovery rate, recovered revenue, and average regret.

The Experimentation Agent supports online strategy allocation with multi-armed-bandit sampling. It tracks recovered revenue, recovery rate, customer friction, time to recovery, revenue lift against a baseline, and 95% confidence intervals. The Revenue Anomaly Detection Agent combines Isolation Forest when enough observations exist with rolling statistical thresholding; Prophet is optional and is used when installed for forecasting [15] [16]. It estimates affected value and can send Slack or SMTP email alerts when credentials are configured.

### Streamlit modules

| Tab | Purpose |
|---|---|
| **Merchant Copilot** | Streaming chat, suggested questions, RAG evidence, validated SQL, and financial explanations. |
| **Explainable AI** | SHAP waterfall, SHAP beeswarm, feature importance, per-transaction action explanations, and PDF export. |
| **Recovery Optimizer** | Event-level recommendation and contextual-bandit simulation. |
| **Experiment Agent** | Strategy outcome recording, bandit report, lift, confidence intervals, friction, and time to recovery. |
| **Anomaly Agent** | Real-time scan, anomaly windows, merchant impact, forecast, and Slack/email alert controls. |

### AI API routes

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/copilot/query` | RAG-backed natural-language analytics with validated SQL. |
| `POST` | `/api/copilot/stream` | Server-Sent Events stream for Copilot responses. |
| `GET` | `/api/explain/{payment_id}` | Per-transaction explanation and recommended-action rationale. |
| `GET` | `/api/explain/{payment_id}/pdf` | Download an explanation PDF. |
| `POST` | `/api/recovery/optimize` | Return optimal retry strategy and expected economics. |
| `POST` | `/api/recovery/simulate` | Evaluate optimization performance over simulated rounds. |
| `GET` | `/api/experiments/report` | Return strategy metrics and confidence intervals. |
| `POST` | `/api/experiments/outcome` | Record an online experiment outcome. |
| `POST` | `/api/anomalies/scan` | Scan supplied or stored transactions for anomalies. |

### Production data and alert configuration

For a local demo, SQLite remains the default because it is easy to run as a single file. For multi-instance production, use the existing database boundary as the migration point for a PostgreSQL adapter, run migrations before deployment, and keep webhook/API workers stateless. Configure `SLACK_WEBHOOK_URL` for Slack alerts, or configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TLS`, `ALERT_EMAIL_FROM`, and `ALERT_EMAIL_TO` for email delivery. Secrets must be supplied through the hosting platform's secret manager rather than committed to Git.

---

## 🌍 Deployment Options

| Platform | API | Dashboard | Free Tier | Guide |
|---|---|---|---|---|
| **Streamlit Cloud** | ✗ | ✓ | ✓ | [See above](#-streamlit-cloud-deployment) |
| **Render** | ✓ | ✓ | ✓ (sleeps) | [DEPLOYMENT.md](DEPLOYMENT.md#step-3--render) |
| **Railway** | ✓ | ✓ | ✓ ($5 credit) | [DEPLOYMENT.md](DEPLOYMENT.md#step-4--railway) |
| **AWS App Runner** | ✓ | Via EC2 | ✗ | [DEPLOYMENT.md](DEPLOYMENT.md#step-5--aws-app-runner) |
| **Azure App Service** | ✓ | ✓ | ✗ (F1 free) | [DEPLOYMENT.md](DEPLOYMENT.md#step-6--azure-app-service) |
| **Docker (any VPS)** | ✓ | ✓ | ✓ | [See above](#-docker-deployment) |

---

## 📦 Dependencies

```
fastapi==0.115.5          Web framework
uvicorn[standard]==0.32.1 ASGI server
pydantic==2.9.2           Data validation
pydantic-settings==2.6.1  Settings management
httpx==0.27.2             Async HTTP client (LLM calls)
streamlit==1.39.0         Dashboard framework
streamlit-autorefresh     Auto-refresh component
plotly==5.24.1            WebGL charts
pandas==2.2.3             Data manipulation
scikit-learn==1.5.2       ML calibration wrapper
lightgbm==4.5.0           Gradient boosting classifier
numpy==1.26.4             Numerical computing
tenacity==9.0.0           Retry / exponential backoff
openai>=1.50.0            Merchant Copilot and LLM integration
langgraph>=0.2.0         Inspectable agent orchestration
shap>=0.45.0             SHAP explanations and plots
matplotlib>=3.8.0        Explainability rendering
reportlab>=4.0.0         PDF explanation export
psycopg[binary]>=3.2.0   Optional PostgreSQL driver
```

---

## 🗂️ CI/CD

GitHub Actions pipeline on every push to `main`:

```
push → main
  ├── [quality]  Syntax check all 10 Python modules
  ├── [quality]  Import smoke test (config, schemas, DB, ML scorer)
  ├── [docker]   Multi-stage Docker build (cache-optimised)
  └── [push]     Push image to Docker Hub (main branch only)
```

---

## 🧪 Testing the Webhook Manually

```bash
# Linux / macOS
BODY='{"entity":"event","event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_test01","order_id":"order_test01","amount":500000,"currency":"INR","error_code":"GATEWAY_ERROR","error_description":"Bank timeout"}}}}'
SECRET="dev_secret_replace_in_production"
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')

curl -X POST http://127.0.0.1:8000/webhook/razorpay \
  -H "Content-Type: application/json" \
  -H "X-Razorpay-Signature: $SIG" \
  -d "$BODY"
```

Expected response:
```json
{"status":"ok","message":"Payment pay_test01 queued for recovery analysis.","payment_id":"pay_test01"}
```

---

## ✅ Production Checklist

- [ ] Set a strong random `RAZORPAY_WEBHOOK_SECRET` (not the dev default)
- [ ] Store `OPENAI_API_KEY` in a secrets manager (AWS Secrets Manager / Azure Key Vault)
- [ ] Replace SQLite with PostgreSQL for multi-instance / multi-region deployments
- [ ] Enable TLS termination (nginx / AWS ALB / Azure Application Gateway)
- [ ] Add rate limiting to the webhook endpoint (e.g., `slowapi`)
- [ ] Configure Razorpay dashboard to send webhooks only to your HTTPS endpoint
- [ ] Set up log aggregation (CloudWatch / Azure Monitor / Datadog)
- [ ] Schedule periodic `GET /api/audit/verify` checks for ledger integrity monitoring
- [ ] Set `ENVIRONMENT=production` to enable production-mode guards

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built for the Razorpay AI Buildathon 2024 — Track 03: Payment Intelligence**

*FastAPI · SQLite WAL · LightGBM · SHA-256 Ledger · Streamlit · Plotly WebGL · OpenAI*

⭐ Star this repo if it helped you · [Report Issues](https://github.com/SumedhPatil1507/RecoverAI/issues)

</div>


---

## References

The following sources support the README's externally verifiable statements about payment webhooks, framework capabilities, visualization, cryptographic primitives, and database behavior. Project-specific thresholds, timings, recovery outcomes, and architecture decisions are defined by this repository's code and are not vendor guarantees.

| Ref. | Source | README-supported claim |
|---|---|---|
| [1] | [Razorpay — Payments Webhook Events](https://razorpay.com/docs/webhooks/payments/?preferred-country=US) | `payment.failed` is a payment webhook event triggered when a payment fails; webhook payloads are snapshots of the entity at event time. |
| [2] | [Razorpay — Validate and Test Webhooks](https://razorpay.com/docs/webhooks/validate-test/?preferred-country=US) | Webhook validation and testing guidance, including signature-validation considerations. |
| [3] | [Streamlit — API Reference](https://docs.streamlit.io/develop/api-reference) | Streamlit APIs for data applications, charts, dataframes, toggles, sliders, and application controls. |
| [4] | [FastAPI — SQL (Relational) Databases](https://fastapi.tiangolo.com/tutorial/sql-databases/) | FastAPI can work with database libraries; the guide demonstrates SQLite as a single-file database with integrated Python support. |
| [5] | [streamlit-autorefresh — GitHub](https://github.com/kmcgrady/streamlit-autorefresh) | The third-party component used by this project to periodically rerun the Streamlit script from a frontend timer. |
| [6] | [Python — `hashlib` documentation](https://docs.python.org/3/library/hashlib.html) | Standard-library hash algorithms, including SHA-256. |
| [7] | [Python — `hmac` documentation](https://docs.python.org/3/library/hmac.html) | Standard-library keyed-hash message authentication functionality. |
| [8] | [SQLite — Write-Ahead Logging](https://sqlite.org/wal.html) | SQLite WAL journaling behavior and concurrency characteristics. |
| [9] | [Plotly — Python Graphing Library](https://plotly.com/python/) | Plotly's Python charting and interactive visualization capabilities. |
| [10] | [LightGBM documentation](https://lightgbm.readthedocs.io/en/latest/) | LightGBM model library used by the recoverability scorer. |
| [11] | [scikit-learn documentation](https://scikit-learn.org/stable/) | Machine-learning utilities used alongside the scoring pipeline where applicable. |
| [12] | [LangGraph — StateGraph reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph) | LangGraph `StateGraph` nodes communicate through shared state and compile into an executable graph. |
| [13] | [SHAP — Waterfall plot documentation](https://shap.readthedocs.io/en/latest/example_notebooks/api_examples/plots/waterfall.html) | Waterfall plots explain a single prediction using an additive explanation object. |
| [14] | [SHAP — Beeswarm plot documentation](https://shap.readthedocs.io/en/latest/example_notebooks/api_examples/plots/beeswarm.html) | Beeswarm plots summarize how features affect model outputs across a dataset. |
| [15] | [scikit-learn — IsolationForest API](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html) | Isolation Forest is the anomaly-detection estimator used by the anomaly service when enough observations exist. |
| [16] | [Prophet documentation](https://facebook.github.io/prophet/) | Optional time-series forecasting implementation used by the anomaly service when installed. |

*RecoverAI Enterprise v2.0.0 · Razorpay AI Buildathon Track 03*


## Enterprise updates and Streamlit deployment

The root `streamlit_app.py` is the Streamlit Cloud entry point. Run it locally with `streamlit run streamlit_app.py`; run the API with `uvicorn recover_ai.main:app --reload`. The dashboard works without provider credentials because Razorpay links and WhatsApp dispatch use mock fallbacks.

Transactions above **₹50,000** or with a proposed discount above **10%** enter `PENDING_APPROVAL`. Merchants can approve, modify, or reject items in the **Agent Overrides & HITL Queue** panel. Audit integrity is available at `/api/audit/verify`.

| Configuration | Purpose |
|---|---|
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Enable real Razorpay payment links. |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_ID` | Enable WhatsApp Business dispatch. |
| `RAZORPAY_WEBHOOK_SECRET` | Verify incoming webhook HMAC signatures. |
| `AUDIT_HMAC_KEY` | Sign audit entries independently. |
| `TENANT_API_KEYS` | Optional comma-separated `merchant_id:api_key` tenant credentials. |
| `USE_CELERY`, `REDIS_URL` | Route jobs to the optional distributed Celery/Redis queue. |

For Streamlit Community Cloud, set the repository branch to `main` and the main file path to `streamlit_app.py`. For distributed deployment, set `USE_CELERY=1`, provide `REDIS_URL`, and run `celery -A recover_ai.queue_worker.celery_app worker --loglevel=INFO`. Without Celery/Redis, the built-in asyncio worker remains the local fallback.

Validate the repository with `python -m pytest -q`. The enterprise tests cover HMAC authentication, PII masking, HITL transitions, audit hash-chain verification, and async mock integrations.

### References

[1]: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app "Streamlit Community Cloud deployment documentation"
[2]: https://razorpay.com/docs/api/payments/payment-links/ "Razorpay Payment Links API"
[3]: https://developers.facebook.com/docs/whatsapp/cloud-api/overview "WhatsApp Cloud API overview"
[4]: https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html "Celery Redis broker documentation"


### Interactive visualization standard

All plots rendered by the Streamlit dashboard use `st.plotly_chart` and Plotly traces. Static Streamlit chart primitives and `st.pyplot` are not used in the visible dashboard. Charts support hover tooltips, zoom and pan, legend toggling, and the Plotly modebar for interactive exploration.


### Streamlit alert configuration

The anomaly-alert panel now reads credentials from Streamlit Cloud Secrets first (and environment variables as a fallback), so hosted deployments do not require a local `.env` file. In Streamlit Cloud, open **Manage app → Settings → Secrets**, paste the following TOML with real values, save it, and reboot the app:

```toml
SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/T.../B.../..."
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TLS = "true"
SMTP_USER = "alerts@example.com"
SMTP_PASSWORD = "provider-app-password"
ALERT_EMAIL_FROM = "alerts@example.com"
ALERT_EMAIL_TO = "recipient@example.com"
```

The **Alert channel configuration** expander shows whether each channel is detected without revealing secret values. Slack uses the incoming webhook URL. Email uses authenticated SMTP when `SMTP_USER` is present; for Gmail or other providers, use an app password rather than a normal account password. Delivery cannot be completed with placeholder values, so the dashboard reports provider failures separately from missing configuration.
