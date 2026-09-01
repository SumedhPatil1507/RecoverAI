# 🏦 RecoverAI Enterprise

> **Agentic Payment Recovery Platform** — Razorpay AI Buildathon · Track 03

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://recoverai-enterprise.streamlit.app)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

RecoverAI autonomously recovers failed Razorpay payments using a multi-agent pipeline: LightGBM ML scoring → LLM root-cause classification → A/B-tested recovery strategies → cryptographic audit logging. No manual intervention needed for 85%+ of failures.

---

## ✨ Features

| Tab | What it does |
|-----|-------------|
| 📊 **Intelligence Hub** | Live KPIs, ML-augmented recovery funnel, time-series charts, SHA-256 audit ledger |
| 🔗 **Payment Links** | Create / bulk-generate Razorpay Payment Links (mock + real API) |
| 📨 **Dispatch** | Send recovery links via WhatsApp, SMS, or Email (mock + live) |
| 👤 **HITL Approvals** | Human-in-the-Loop queue for high-value or ambiguous transactions |
| 🧪 **A/B Testing** | Live experiment engine with z-score significance testing |
| 💥 **Chaos Simulator** | Inject payload corruption, latency spikes, and bad signatures |
| 🏢 **Merchants** | Multi-tenant isolation with per-merchant dashboards |

---

## 🚀 Live Demo

**[recoverai-enterprise.streamlit.app](https://recoverai-enterprise.streamlit.app)**

On first load the app auto-seeds 60 synthetic transactions so every chart is populated immediately. Use the **🌱 Seed Demo Data** button in the sidebar to refresh at any time.

---

## 🏗 Architecture

```
Razorpay Webhook
      │
      ▼
┌─────────────┐    HMAC     ┌──────────────────────────────────────┐
│  FastAPI    │◄───verify───│  security.py  (PII redaction + HMAC) │
│  main.py   │             └──────────────────────────────────────┘
└──────┬──────┘
       │ asyncio.Queue
       ▼
┌─────────────┐
│queue_worker │  4 async workers
└──────┬──────┘
       │
       ▼
┌──────────────────────────────────────────────────────┐
│  agent_engine.py  (LangGraph-style 8-node pipeline)  │
│                                                      │
│  Ingest → ML Score → A/B Route → LLM / Rules →      │
│  HITL Gate → Dispatch → Razorpay Link → Audit Log    │
└──────┬───────────────────────────┬───────────────────┘
       │                           │
       ▼                           ▼
┌─────────────┐           ┌─────────────────┐
│ ml_scorer   │           │  integrations/  │
│ LightGBM    │           │  razorpay_links │
│ KS drift    │           │  whatsapp_notif │
│ hot-swap    │           └─────────────────┘
└─────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│  database.py  SQLite WAL            │
│  SHA-256 hash-chain + HMAC ledger   │
│  HITL queue · A/B counters          │
└─────────────────────────────────────┘
       │
       ▼
┌─────────────┐
│ streamlit   │  7-tab dashboard (this file)
│  app.py     │
└─────────────┘
```

---

## ⚡ Quick Start (local)

```bash
# 1. Clone
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and edit secrets
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit .streamlit/secrets.toml — minimum required: DATABASE_PATH

# 4. Run the dashboard
streamlit run streamlit_app.py

# 5. (Optional) Run the FastAPI backend in a separate terminal
uvicorn recover_ai.main:app --reload --port 8000

# 6. (Optional) Send synthetic webhook events
python recover_ai/data_simulator.py --burst 20
```

The dashboard auto-seeds 60 demo transactions on first run so charts are populated immediately.

---

## ☁️ Deploy to Streamlit Cloud

### Step 1 — Fork & connect

1. Fork this repo to your GitHub account (or use it directly if you're the owner).
2. Go to **[share.streamlit.io](https://share.streamlit.io)** → **New app**.
3. Set:
   - **Repository:** `SumedhPatil1507/RecoverAI`
   - **Branch:** `main`
   - **Main file path:** `streamlit_app.py`

### Step 2 — Add Secrets

In Streamlit Cloud: **your app → ⋮ (three dots) → Settings → Secrets**

Paste the following, replacing placeholder values:

```toml
# ── Required ──────────────────────────────────────────────────────────────────
ENVIRONMENT   = "production"
DATABASE_PATH = "/tmp/recover_ai_enterprise.db"
ML_MODEL_PATH = "/tmp/recover_ai_lgbm.pkl"

# ── Razorpay (get from dashboard.razorpay.com → Settings → API Keys) ─────────
RAZORPAY_WEBHOOK_SECRET = "your_webhook_secret_here"
RAZORPAY_KEY_ID         = "rzp_test_xxxxxxxxxxxx"
RAZORPAY_KEY_SECRET     = "your_key_secret_here"

# ── Optional: OpenAI (leave blank to use rule engine only) ───────────────────
OPENAI_API_KEY = ""

# ── Audit HMAC key (generate once, keep secret) ───────────────────────────────
# python -c "import secrets; print(secrets.token_hex(32))"
AUDIT_HMAC_KEY = "your_64_char_hex_key_here"

# ── Optional: Gmail SMTP for email dispatch ───────────────────────────────────
SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = "587"
SMTP_TLS   = "true"
SMTP_USER  = "sumedhp612@gmail.com"
SMTP_PASS  = "your_gmail_app_password"
SMTP_FROM  = "sumedhp612@gmail.com"

# ── Optional: Slack + alert email ────────────────────────────────────────────
SLACK_WEBHOOK_URL = ""
ALERT_EMAIL_FROM  = "sumedhp612@gmail.com"
ALERT_EMAIL_TO    = "sumedhp612@gmail.com"
```

Click **Save** — the app reboots automatically.

> **Important:** `DATABASE_PATH` must be `/tmp/recover_ai_enterprise.db`.  
> Streamlit Cloud mounts the repo as read-only; only `/tmp/` is writable.

### Step 3 — Verify

Once deployed the app auto-seeds demo data and all 7 tabs should be live within ~60 seconds.

---

## 🔑 Gmail App Password (for SMTP)

You need an **App Password**, not your regular Gmail password:

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. **Security** → **2-Step Verification** → make sure it's ON
3. **Security** → **App passwords** (search for it if not visible)
4. Select app: **Mail** · Select device: **Other** → type `RecoverAI`
5. Click **Generate** → copy the 16-character password
6. Paste it as `SMTP_PASS` in Streamlit Secrets (no spaces)

---

## 🌱 Demo Data

The app auto-seeds 60 realistic synthetic transactions on first load (empty DB). To refresh:

- Click **🌱 Seed Demo Data** in the sidebar → seeds another 60 transactions
- All charts, the funnel, root-cause donut, and audit ledger populate immediately
- No FastAPI server or Razorpay account needed for the dashboard

---

## 🧪 Chaos Stress Test

Test the FastAPI backend's sub-30ms ACK SLA:

```bash
# Start the API first
uvicorn recover_ai.main:app --port 8000

# Fire 500 concurrent HMAC-signed webhooks
python recover_ai/data_simulator.py --chaos 500 --workers 64
```

Output includes p50 / p95 / p99 latency, throughput, and SLA pass/fail.

---

## 🐳 Docker

```bash
# Full stack (API + dashboard)
docker compose up api dashboard

# Add synthetic data simulator
docker compose --profile dev up

# Run chaos test against the running API
docker compose --profile chaos up

# Full observability (+ Prometheus on :9090 + Grafana on :3000)
docker compose --profile monitoring up
```

---

## 📁 Project Structure

```
RecoverAI/
├── streamlit_app.py              # Streamlit Cloud entry point (this IS the app)
├── requirements.txt              # All dependencies
├── .streamlit/
│   ├── config.toml               # Theme + server settings
│   └── secrets.toml.example      # Copy → secrets.toml for local dev
├── recover_ai/
│   ├── main.py                   # FastAPI gateway (webhooks + metrics)
│   ├── agent_engine.py           # 8-node recovery pipeline (A/B + HITL)
│   ├── ml_scorer.py              # LightGBM + KS drift detection + hot-swap
│   ├── database.py               # SQLite + SHA-256/HMAC audit ledger
│   ├── schemas.py                # Pydantic models (incl. HITL + A/B)
│   ├── security.py               # PII redaction + HMAC signature verify
│   ├── queue_worker.py           # Async job queue (4 workers)
│   ├── config.py                 # pydantic-settings (v1 + v2 compat)
│   ├── data_simulator.py         # Webhook simulator + chaos stress test
│   └── integrations/
│       ├── razorpay_links.py     # Razorpay Payment Links API client
│       └── whatsapp_notifier.py  # WhatsApp / SMS / Email dispatcher
├── terraform/                    # AWS IaC (EKS, Aurora, Redis, Secrets Manager)
├── .github/workflows/deploy.yml  # 8-stage CI/CD pipeline
├── docker-compose.yml            # Local dev + chaos + monitoring profiles
└── monitoring/                   # Prometheus + Grafana configuration
```

---

## 🔐 Security Model

- **Webhook authentication** — HMAC-SHA256 verified on every inbound event (`X-Razorpay-Signature`)
- **PII redaction** — email, phone, card data scrubbed before any DB write
- **Audit ledger** — every action is SHA-256 hash-chained *and* HMAC-SHA256 signed with `AUDIT_HMAC_KEY`; tampering with any row breaks the full chain
- **Discount guardrail** — LLM cannot propose a discount > 15%; hard-capped in two places
- **HITL gate** — transactions over ₹50,000 or with ambiguous ML scores are held for human review before any action is taken

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| Dashboard | Streamlit 1.45+, Plotly |
| API | FastAPI, Uvicorn, asyncio |
| ML | LightGBM, scikit-learn, scipy (KS drift) |
| Database | SQLite (WAL), SHA-256 + HMAC ledger |
| Notifications | Razorpay Payment Links, Meta WhatsApp Cloud, Twilio, SMTP |
| Observability | Prometheus, Grafana |
| IaC | Terraform (AWS EKS Fargate, Aurora PG, ElastiCache Redis) |
| CI/CD | GitHub Actions (ruff, Bandit, Docker, Terraform, kubectl) |

---

## 📄 License

MIT — see [LICENSE](LICENSE).
