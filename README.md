<div align="center">

# 🏦 RecoverAI Enterprise

### Agentic Payment Recovery Platform

*Razorpay AI Buildathon · Track 03*

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://recoverai-enterprise.streamlit.app)
[![Tests](https://img.shields.io/badge/tests-132%20passed-brightgreen?style=flat-square)](tests/)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

**RecoverAI autonomously recovers failed Razorpay payments using a stateful multi-agent pipeline  
with Thompson Sampling bandit routing, ML scoring, EV guardrails, and a cryptographic audit ledger.**

[Live Demo](https://recoverai-enterprise.streamlit.app) · [Quick Start](#-quick-start) · [Architecture](#-architecture) · [Tests](#-testing)

</div>

---

## 🎯 What it does

When a payment fails, RecoverAI:

1. **Scores** the transaction with LightGBM (recoverability 0–1)
2. **Routes** it via a Thompson Sampling contextual bandit (control vs variant)
3. **Classifies** the root cause (gateway down, insufficient funds, etc.)
4. **Gates** on Expected Value — skips economically negative recoveries
5. **Dispatches** a Razorpay Payment Link via WhatsApp / SMS / Email
6. **Monitors** the dispatch outcome and **self-heals** via REFLECT if it fails
7. **Logs** every decision to an immutable SHA-256 + HMAC cryptographic ledger

All of this runs in milliseconds — the webhook ACK returns `202 Accepted` in **< 15 ms**.

---

## ✨ Feature Overview

### 📊 Intelligence Hub
Real-time KPI dashboard with recovery funnel, revenue at risk vs recovered time-series, ML score histogram, failure root-cause donut chart, and the SHA-256 + HMAC tamper-evident audit ledger with one-click verification.

### 🔗 Payment Links
Razorpay Payment Links API integration with async circuit breaker (CLOSED → OPEN → HALF-OPEN), smart routing for GATEWAY_DOWN / BANK_DECLINE failures, 15% discount hard-cap guardrail, and bulk-create support.

### 📨 Multi-Channel Dispatch
WhatsApp Business Cloud API, Twilio SMS, and SMTP email — each channel has its own circuit breaker. Automatic fallback: WhatsApp → SMS → Email → HITL notification.

### 👤 HITL Approvals + ROI Calculator
Human-in-the-Loop queue for high-value (> ₹50k) or ambiguous transactions. Integrated A/B Financial Lift & ROI Calculator with Z-score significance testing, net revenue lift, and discount sensitivity heat-map.

### 🧪 A/B Testing Engine
Live experiment engine comparing rule engine (control) vs LLM-augmented (variant) strategies. Displays recovery rate, lift %, confidence intervals, and historical experiment table.

### 💥 Chaos Simulator
Fire 500 concurrent HMAC-signed webhooks from the UI. Reports p50 / p95 / p99 latency, throughput, and SLA pass/fail. Tests dispatch circuit breakers under load.

### 🏢 Multi-Tenant Merchants
Per-merchant dashboards with plan tiers (Starter / Growth / Enterprise), simulated revenue recovery timelines, and tenant isolation audit table.

### 💡 EV Engine & Agent Graph
- **Expected Value Calculator** — live EV = P × R − (op_fee + gateway_cost) with heat-map and breakeven chart
- **Shadow Ledger** — browse all EV-bypass and SHADOW-mode intercept events
- **Bandit Report** — Thompson Sampling arm performance chart per context bucket
- **Agent Graph Diagram** — stateful INGEST→SCORE→ROOT_CAUSE→EV_GATE→DISPATCH→MONITOR→REFLECT flow
- **Kafka Status** — streaming backend health indicator

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Razorpay Webhook                                  │
│                    POST /webhook/razorpay  (HMAC verified)           │
│                    202 ACK returned in < 15 ms                       │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
          ┌──────────────────▼──────────────────────┐
          │  Kafka / Redpanda  (or asyncio.Queue)   │
          │  aiokafka producer · at-least-once       │
          │  idempotency: Redis SET NX (24h TTL)     │
          └──────────────────┬──────────────────────┘
                             │
   ┌─────────────────────────▼────────────────────────────────────────┐
   │              agent_graph.py  — Stateful Graph Runner             │
   │                                                                   │
   │  INGEST ──► SCORE ──► ROOT_CAUSE                                  │
   │                           │                                       │
   │              Thompson Sampling Bandit                             │
   │              (failure_cat × ml_band × amount_band)               │
   │                           │                                       │
   │                        EV_GATE                                    │
   │                  EV = P×R − (OpFee + GwCost)                      │
   │                    ├─ EV ≤ 0  ──► shadow_ledger + BYPASS          │
   │                    └─ EV > 0  ──► DISPATCH                        │
   │                                       │                           │
   │                                   MONITOR                         │
   │                                ├─ ok ──► AUDIT_LOG                │
   │                                └─ fail ──► REFLECT                │
   │                                               │                   │
   │                            (select fallback channel,              │
   │                             reduce op_fee, retry EV_GATE)         │
   └───────────────────────────────────────────────────────────────────┘
                             │
   ┌─────────────────────────▼────────────────────────────────────────┐
   │  SQLite WAL (dev / Streamlit Cloud)                              │
   │  PostgreSQL Aurora Serverless v2 (production)                    │
   │  Row-Level Security · Monthly range partitions                   │
   │  SHA-256 hash-chain + HMAC-SHA256 per-row audit ledger           │
   └──────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Run locally

```bash
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open **http://localhost:8501**. The dashboard auto-seeds 60 synthetic transactions on first run — every chart is populated immediately. Click **🌱 Seed Demo Data** in the sidebar to refresh.

> **Demo safety:** `streamlit_app.py` forces `ENVIRONMENT=staging`, `EXECUTION_MODE=SHADOW`, and `DATABASE_PATH=/tmp/...` at startup. No live payments, notifications, or API calls are made.

### Run the FastAPI service (optional)

```bash
uvicorn recover_ai.main:app --host 0.0.0.0 --port 8000
# Send synthetic webhooks
python recover_ai/data_simulator.py --burst 20
# Chaos stress test (500 concurrent)
python recover_ai/data_simulator.py --chaos 500
```

### Docker

```bash
docker compose up api dashboard          # core stack
docker compose --profile dev up          # + simulator
docker compose --profile chaos up        # + stress tester
docker compose --profile monitoring up   # + Prometheus + Grafana
```

---

## ☁️ Deploy to Streamlit Cloud

1. Go to **[share.streamlit.io](https://share.streamlit.io)** → **New app**
2. Set: Repository `SumedhPatil1507/RecoverAI` · Branch `main` · Main file `streamlit_app.py`
3. Click **Deploy** — no secrets required for the staging demo

To enable optional features, add these to **Settings → Secrets**:

```toml
# Core (required for full functionality)
ENVIRONMENT   = "production"
DATABASE_PATH = "/tmp/recover_ai_enterprise.db"
ML_MODEL_PATH = "/tmp/recover_ai_lgbm.pkl"

# Razorpay (dashboard.razorpay.com → Settings → API Keys)
RAZORPAY_WEBHOOK_SECRET = "your_webhook_secret"
RAZORPAY_KEY_ID         = "rzp_test_xxxxxxxxxxxx"
RAZORPAY_KEY_SECRET     = "your_key_secret"

# Security keys
# Generate: python -c "import secrets; print(secrets.token_hex(32))"
AUDIT_HMAC_KEY = "your_64_char_hex_key"
JWT_SECRET_KEY = "your_64_char_jwt_key"

# Optional: OpenAI (blank = rule engine only)
OPENAI_API_KEY = ""

# Optional: Gmail SMTP
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "your@gmail.com"
SMTP_PASS = "your_16_char_app_password"
SMTP_FROM = "your@gmail.com"

# Optional: Stateful agent graph + bandit
AGENT_GRAPH = "0"        # "1" activates stateful graph
BANDIT_MODE = "thompson" # or "linucb" or "epsilon_greedy"
```

> **Gmail App Password:** myaccount.google.com → Security → 2-Step Verification → App passwords → Mail → Other

---

## 🧪 Testing

**132 tests passing** across 4 isolated test files:

```bash
pip install -r requirements-dev.txt
pytest tests/ -q --timeout=180
```

| File | Tests | What it covers |
|------|------:|----------------|
| `test_enterprise_controls.py` | 7 | EV unit-economic guardrails, Principal RBAC, `authorize()` |
| `test_ev_engine.py` | 67 | EV arithmetic, shadow mode, chaos resilience, Thompson bandit, JWT |
| `test_llm_eval.py` | 14 | LLM faithfulness ≥ 0.80, tone compliance, zero hallucination, calibration |
| `test_enterprise_flow.py` | 59 | HMAC, PII, AES-GCM, audit chain tamper, HITL FSM, circuit breaker, webhook chaos |

### Chaos test highlights
- **DB partition failure** — forced connection close mid-write; chain survives
- **Thread worker crash** — daemon thread killed after first write; no chain corruption
- **Concurrent write storm** — 20 threads × 5 writes each; all entries recoverable
- **Mid-chain corruption** — corrupt entry #2; `verify_audit_integrity_detailed()` returns exact tampered log_ids
- **500-concurrent webhook storm** — 0 drops, p95 < 2000 ms on TestClient

---

## 📊 Outcomes

| Metric | Value |
|--------|-------|
| Webhook ACK latency | < 15 ms p99 (202 Accepted) |
| Recovery pipeline throughput | > 1 000 transactions/min per worker |
| ML scoring | LightGBM, Brier < 0.30, KS + PSI drift detection |
| EV gate | Decimal-exact arithmetic, negative-EV bypass < 1 ms |
| Audit ledger | SHA-256 hash-chain + HMAC-SHA256 per row, SOC2-style tamper index |
| Test coverage | 132 tests, 4 files, isolated DBs, cross-module secret isolation |
| Dispatch fallback | WA → SMS → Email → HITL (circuit breaker per channel) |
| Self-healing | Up to 2 REFLECT cycles, adjusted op_fee per channel |

---

## 📁 Project Structure

```
RecoverAI/
├── streamlit_app.py              # 8-tab Streamlit demo dashboard
├── requirements.txt              # Runtime dependencies
├── requirements-dev.txt          # Test + lint tooling
│
├── recover_ai/
│   ├── agent_graph.py            # ★ Stateful graph (Epic 1)
│   ├── bandit.py                 # ★ Thompson Sampling bandit (Epic 2)
│   ├── kafka_worker.py           # ★ Kafka/Redpanda streaming (Epic 3)
│   ├── agent_engine.py           # Linear pipeline (fallback)
│   ├── ev_engine.py              # EV = P×R − Costs; PROCEED/BYPASS/SHADOW
│   ├── expected_value.py         # Paise-exact EV guardrails
│   ├── ml_scorer.py              # LightGBM + KS/PSI drift + hot-swap
│   ├── auth.py                   # JWT HS256 + Principal + authorize()
│   ├── database.py               # SQLite + SHA-256 + HMAC audit ledger
│   ├── db_postgres.py            # Async PostgreSQL (asyncpg, RLS, partitions)
│   ├── schemas.py                # Pydantic v2 models
│   ├── security.py               # PII redaction + HMAC + AES-256-GCM
│   ├── config.py                 # pydantic-settings (v1/v2 compat)
│   ├── main.py                   # FastAPI gateway (< 15 ms webhook ACK)
│   ├── queue_worker.py           # Celery + Redis / asyncio.Queue
│   └── integrations/
│       ├── razorpay_links.py     # Async Razorpay client + CircuitBreaker
│       └── whatsapp_notifier.py  # WA / SMS / SMTP + per-channel CB
│
├── tests/
│   ├── conftest.py               # Cross-module DB + secret isolation
│   ├── test_enterprise_controls.py  # EV guardrails + RBAC (7 tests)
│   ├── test_ev_engine.py         # EV + chaos + bandit (67 tests)
│   ├── test_llm_eval.py          # LLM eval harness (14 tests)
│   └── test_enterprise_flow.py   # Integration + chaos (59 tests)
│
├── terraform/                    # AWS EKS, Aurora PG, Redis, Secrets Mgr
├── .github/workflows/deploy.yml  # 8-stage CI/CD pipeline
├── docker-compose.yml            # 4 profiles: dev, chaos, monitoring
└── monitoring/                   # Prometheus + Grafana config
```

---

## 🔐 Security Model

| Control | Implementation |
|---------|---------------|
| Webhook authentication | HMAC-SHA256 on every inbound event, constant-time compare |
| PII redaction | Regex + field-name walk strips email/phone/card before DB or LLM |
| Audit ledger | SHA-256 hash-chain **+** HMAC-SHA256 per row, `verify_audit_integrity_detailed()` returns tampered log_ids |
| Column encryption | AES-256-GCM with HKDF-SHA256 key derivation; b64-only fallback |
| Discount guardrail | LLM discount capped at 15% in two independent checks |
| EV gate | Negative-EV actions bypassed; counterfactuals in shadow ledger |
| HITL gate | Transactions > ₹50k or ML score 0.40–0.60 queued for human review |
| JWT RBAC | HS256 tokens · 3 roles (Admin / Operator / Auditor) · `authorize()` |
| Tenant isolation | `merchant_id` on all tables; PostgreSQL RLS enforces row-level access |

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Dashboard** | Streamlit 1.40+, Plotly |
| **API** | FastAPI, Uvicorn, asyncio |
| **Stateful Agent** | `agent_graph.py` — custom graph (LangGraph-compatible) |
| **Bandit** | Thompson Sampling β(α, β) · LinUCB · ε-greedy |
| **Queue** | asyncio.Queue (dev) · Celery+Redis · Kafka/Redpanda (aiokafka) |
| **ML** | LightGBM, scikit-learn, scipy (KS/PSI drift) |
| **Database** | SQLite WAL (dev) · PostgreSQL Aurora Serverless v2 (prod) |
| **Security** | HMAC-SHA256, AES-256-GCM, JWT HS256, PII redaction |
| **EV Engine** | Decimal arithmetic, PROCEED/BYPASS/SHADOW modes |
| **Observability** | Prometheus `/metrics`, OpenTelemetry (9 node spans + token/latency) |
| **IaC** | Terraform — AWS EKS Fargate, Aurora PG, ElastiCache Redis |
| **CI/CD** | GitHub Actions: ruff → Bandit SAST → 132 tests → Docker → Terraform → kubectl |

---

## 🙏 Acknowledgments

Built for the **Razorpay AI Buildathon · Track 03**

> **Demo safety:** `streamlit_app.py` forces staging/shadow mode and a temporary `/tmp/` SQLite database. It does not make live payment or notification calls and is suitable for demos and evaluation only. Never use demo configuration for real payment processing.
