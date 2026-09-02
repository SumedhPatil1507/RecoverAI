# 🏦 RecoverAI Enterprise

> **Agentic Payment Recovery Platform** — Razorpay AI Buildathon · Track 03

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://recoverai-enterprise.streamlit.app)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-59%20passed-brightgreen.svg)](#testing)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

RecoverAI autonomously recovers failed Razorpay payments using a multi-agent pipeline: LightGBM ML scoring → KS drift detection → LLM root-cause classification → A/B-tested recovery strategies → Razorpay Payment Links → WhatsApp/SMS dispatch → HITL approval queue → cryptographic HMAC audit ledger.

---

## ✨ Features

| Tab | What it does |
|-----|-------------|
| 📊 **Intelligence Hub** | Live KPIs, ML-augmented recovery funnel, time-series, SHA-256 + HMAC audit ledger |
| 🔗 **Payment Links** | Create / bulk-generate Razorpay Payment Links with circuit breaker + smart routing |
| 📨 **Dispatch** | WhatsApp Business API / Twilio SMS / SMTP email with per-channel circuit breakers |
| 👤 **HITL Approvals** | Human-in-the-Loop queue for amounts > ₹50k or ambiguous ML scores, with ROI calculator |
| 🧪 **A/B Testing** | Live experiment engine with z-score significance, net revenue lift, and margin analysis |
| 💥 **Chaos Simulator** | Inject payload corruption, latency spikes, bad signatures — with latency histogram |
| 🏢 **Merchants** | Multi-tenant isolation with per-merchant dashboards and plan tiers |

---

## 🚀 Live Demo

**[recoverai-enterprise.streamlit.app](https://recoverai-enterprise.streamlit.app)**

On first load the app auto-seeds 60 synthetic transactions so every chart is populated immediately. Click **🌱 Seed Demo Data** in the sidebar to refresh at any time.

---

## 🏗 Architecture

```
Razorpay Webhook  ──HMAC──▶  POST /webhook/razorpay (< 15ms 202 ACK)
                                      │
                              asyncio.Queue / Celery+Redis
                                      │
                     ┌────────────────▼──────────────────────┐
                     │   agent_engine.py (9-node pipeline)    │
                     │                                        │
                     │  Ingest → ML Score (KS drift) →        │
                     │  A/B Route → LLM/Rules →               │
                     │  Discount Guardrail (15% cap) →        │
                     │  HITL Gate (> ₹50k / ambiguous) →      │
                     │  Razorpay Payment Link →               │
                     │  WhatsApp/SMS/Email Dispatch →         │
                     │  SHA-256 + HMAC Audit Log              │
                     └───────────────────────────────────────┘
                                      │
                     ┌────────────────▼──────────────────────┐
                     │   SQLite  (WAL · /tmp/ on Cloud)       │
                     │   transactions · audit_logs ·          │
                     │   hitl_queue · ab_experiment           │
                     └───────────────────────────────────────┘
                                      │
                     ┌────────────────▼──────────────────────┐
                     │   streamlit_app.py (7-tab dashboard)   │
                     └───────────────────────────────────────┘
```

---

## ⚡ Quick Start (local)

```bash
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
streamlit run streamlit_app.py
```

To also run the FastAPI backend and send synthetic events:
```bash
# Terminal 1 — FastAPI
uvicorn recover_ai.main:app --reload --port 8000

# Terminal 2 — send 20 synthetic webhook events
python recover_ai/data_simulator.py --burst 20

# Terminal 3 — chaos stress test (500 concurrent)
python recover_ai/data_simulator.py --chaos 500
```

---

## ☁️ Deploy to Streamlit Cloud

### Step 1 — Connect repo
1. Go to **[share.streamlit.io](https://share.streamlit.io)** → **New app**
2. Repository: `SumedhPatil1507/RecoverAI`, Branch: `main`, Main file: `streamlit_app.py`

### Step 2 — Add Secrets
**Streamlit Cloud → your app → ⋮ → Settings → Secrets**

```toml
# Required
ENVIRONMENT   = "production"
DATABASE_PATH = "/tmp/recover_ai_enterprise.db"
ML_MODEL_PATH = "/tmp/recover_ai_lgbm.pkl"

# Razorpay (dashboard.razorpay.com → Settings → API Keys)
RAZORPAY_WEBHOOK_SECRET = "your_webhook_secret"
RAZORPAY_KEY_ID         = "rzp_test_xxxxxxxxxxxx"
RAZORPAY_KEY_SECRET     = "your_key_secret"

# Audit HMAC (generate: python -c "import secrets; print(secrets.token_hex(32))")
AUDIT_HMAC_KEY = "your_64_char_hex_key"

# Optional: OpenAI (leave blank for rule engine only)
OPENAI_API_KEY = ""

# Optional: Gmail SMTP (needs App Password, not account password)
# myaccount.google.com → Security → App passwords
SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = "587"
SMTP_USER  = "sumedhp612@gmail.com"
SMTP_PASS  = "your_16_char_app_password"
SMTP_FROM  = "sumedhp612@gmail.com"

# Optional: Slack + alert email
SLACK_WEBHOOK_URL = ""
ALERT_EMAIL_FROM  = "sumedhp612@gmail.com"
ALERT_EMAIL_TO    = "sumedhp612@gmail.com"
```

> **`DATABASE_PATH` must be `/tmp/...`** — the repo root is read-only on Streamlit Cloud.

### Step 3 — Verify
App auto-seeds demo data on first load. All 7 tabs should be live within ~60 seconds.

---

## 🔑 Gmail App Password

`SMTP_PASS` must be a 16-character **App Password**, not your regular password:
1. [myaccount.google.com](https://myaccount.google.com) → Security → 2-Step Verification → **ON**
2. Security → **App passwords** → Select app: Mail, Device: Other → name it `RecoverAI`
3. Copy the 16-character password (no spaces) → paste as `SMTP_PASS`

---

## 🧪 Testing

59 tests covering all 6 enterprise epics:

```bash
pip install -r requirements-dev.txt
pytest tests/test_enterprise_flow.py -v
```

| Test class | Coverage |
|------------|---------|
| `TestHMACSecurity` | HMAC signature accept/reject, constant-time comparison |
| `TestPIIRedaction` | Email, card, phone masking; nested dicts/lists |
| `TestAES256GCM` | Encrypt/decrypt round-trip, nonce uniqueness, wrong-key passthrough |
| `TestAuditChain` | SHA-256 hash-chain + HMAC-per-row integrity, tamper detection |
| `TestHITLStateMachine` | PENDING → APPROVED / REJECTED / MODIFIED, gate triggers |
| `TestCircuitBreaker` | CLOSED → OPEN → HALF-OPEN → CLOSED lifecycle |
| `TestMLDriftDetection` | KS test, PSI, scorer probability bounds, drift log |
| `TestWebhookEnqueue` | 202 ACK, 401 bad-sig, < 50ms mean latency |
| `TestChaosWebhookStorm` | 500 concurrent HMAC webhooks, 0 drops, p95 latency |
| `TestAuditVerifyEndpoint` | `/api/v1/audit/verify` shape + clean-chain assertion |

---

## 🐳 Docker

```bash
docker compose up api dashboard                     # core stack
docker compose --profile dev up                     # + simulator
docker compose --profile chaos up                   # + stress tester
docker compose --profile monitoring up              # + Prometheus + Grafana
```

---

## 📁 Project Structure

```
RecoverAI/
├── streamlit_app.py              # Streamlit Cloud entry (7-tab dashboard + ROI calc)
├── requirements.txt              # All dependencies (Python 3.14 compatible ranges)
├── requirements-dev.txt          # Test + lint tooling
├── .streamlit/
│   ├── config.toml
│   └── secrets.toml.example      # Copy → .streamlit/secrets.toml for local dev
├── recover_ai/
│   ├── main.py                   # FastAPI — 202 webhook (<15ms), Prometheus metrics
│   ├── agent_engine.py           # 9-node pipeline: OTel spans, A/B, HITL, guardrails
│   ├── ml_scorer.py              # LightGBM + KS/PSI drift + hot-swap retraining
│   ├── queue_worker.py           # asyncio.Queue / Celery+Redis DLQ + idempotency
│   ├── database.py               # SQLite WAL + SHA-256 chain + HMAC-per-row ledger
│   ├── schemas.py                # Pydantic v2 models (HITL, A/B, PENDING_APPROVAL)
│   ├── security.py               # PII redaction + HMAC verify + AES-256-GCM encrypt
│   ├── config.py                 # pydantic-settings (v1/v2 compat + Streamlit secrets)
│   ├── data_simulator.py         # Synthetic webhooks + --chaos 500 stress test
│   └── integrations/
│       ├── razorpay_links.py     # Async Razorpay API client + CircuitBreaker
│       └── whatsapp_notifier.py  # WhatsApp/SMS/Email dispatcher + per-channel CB
├── tests/
│   └── test_enterprise_flow.py   # 59-test integration + chaos suite
├── terraform/                    # AWS IaC (EKS Fargate, Aurora PG, Redis, Secrets Mgr)
├── .github/workflows/deploy.yml  # 8-stage CI/CD (ruff → Bandit → tests → ECR → EKS)
├── docker-compose.yml            # 4 profiles: dev, chaos, monitoring
└── monitoring/                   # Prometheus + Grafana config
```

---

## 🔐 Security Model

| Layer | Implementation |
|-------|---------------|
| Webhook auth | HMAC-SHA256 on every inbound event (`X-Razorpay-Signature`), constant-time compare |
| PII redaction | Regex + field-name walk strips email/phone/card before any DB write or LLM call |
| Audit ledger | Every action: SHA-256 hash-chain **+** HMAC-SHA256 per row keyed by `AUDIT_HMAC_KEY` |
| Column encryption | AES-256-GCM via `cryptography` with HKDF key derivation; b64-only fallback without it |
| Discount guardrail | LLM discount capped at 15% in two independent checks (LLM parse + pipeline outer) |
| HITL gate | Transactions > ₹50k or ML score 0.40–0.60 held for human review before any action |
| Tenant isolation | `merchant_id` on all DB tables; API-key middleware scopes all queries |

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| Dashboard | Streamlit 1.40+, Plotly |
| API | FastAPI, Uvicorn, asyncio |
| Queue | asyncio.Queue (dev) / Celery + Redis (prod, `USE_CELERY=1`) |
| ML | LightGBM, scikit-learn, scipy (KS drift) |
| Database | SQLite WAL, SHA-256 + HMAC audit ledger |
| Integrations | Razorpay Payment Links, Meta WhatsApp Cloud, Twilio, SMTP |
| Observability | Prometheus (`/metrics`), OpenTelemetry spans (Grafana Tempo / Jaeger) |
| IaC | Terraform (AWS EKS Fargate, Aurora PG Serverless v2, ElastiCache Redis) |
| CI/CD | GitHub Actions: ruff → Bandit SAST → 59 tests → Docker → Terraform → kubectl |

---

## 📄 License

MIT — see [LICENSE](LICENSE).
