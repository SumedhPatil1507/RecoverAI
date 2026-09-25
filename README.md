<div align="center">

# 🏦 RecoverAI Enterprise

### Agentic Payment Recovery Platform

*Razorpay AI Buildathon · Track 03*

---

[![Live Demo](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://recoverai-enterprise.streamlit.app)
&nbsp;&nbsp;
[![Tests](https://img.shields.io/badge/✅%20132%20tests%20passing-00c851?style=flat-square)](tests/)
&nbsp;&nbsp;
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
&nbsp;&nbsp;
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
&nbsp;&nbsp;
[![License: MIT](https://img.shields.io/badge/License-MIT-f0ad4e?style=flat-square)](LICENSE)

<br/>

> **RecoverAI autonomously recovers failed Razorpay payments using a stateful multi-agent pipeline.**  
> Thompson Sampling bandit routing · LightGBM ML scoring · EV guardrails · HITL approvals · Cryptographic audit ledger

<br/>

**[🚀 Live Demo](https://recoverai-enterprise.streamlit.app)** &nbsp;·&nbsp; **[⚡ Quick Start](#-quick-start)** &nbsp;·&nbsp; **[🏗 Architecture](#-architecture)** &nbsp;·&nbsp; **[📊 Outcomes](#-outcomes)**

</div>

---

## 💡 The Problem & Solution

> **85% of failed payments are recoverable — but most platforms never attempt it.**

RecoverAI intercepts every `payment.failed` webhook, scores the transaction with a LightGBM model, routes it through a unit-economic EV gate, and dispatches a personalised recovery link via WhatsApp / SMS / Email — all within milliseconds, with zero manual intervention.

```
Payment fails → HMAC webhook → Score → EV Gate → Dispatch → Monitor → Heal → Log
                    < 15 ms ACK                            async workers
```

---

## ✨ Feature Showcase

### 📊 Tab 1 — Intelligence Hub

| What you see | What it means |
|---|---|
| Revenue at Risk vs Recovered | Live ₹ impact across all failed transactions |
| ML-Augmented Recovery Funnel | Ingested → Scored → Evaluated → Dispatched → Recovered |
| Failure Root-Cause Donut | GATEWAY_DOWN / INSUFFICIENT_FUNDS / BANK_DECLINE / etc. |
| Time-Series Chart | Dual-trace: revenue at risk vs recovered over time |
| ML Score Histogram | Distribution of LightGBM recoverability scores |
| Audit Ledger Verify | One-click SHA-256 + HMAC chain verification with tamper-index |

**Outcome:** Full operational visibility into recovery pipeline health in real time.

---

### 🔗 Tab 2 — Razorpay Payment Links

- **Async HTTP client** with connection pooling targeting `/v1/payment_links`
- **Circuit Breaker** — CLOSED → OPEN → HALF-OPEN with configurable thresholds
- **Smart Routing** — alternative UPI/wallet checkout for GATEWAY_DOWN and BANK_DECLINE
- **15% discount hard-cap** enforced at two independent layers
- **Bulk creation** for batch recovery campaigns
- **Callback signature verification** for inbound Razorpay callbacks

**Outcome:** Payment links created and dispatched with zero blocking on provider outages.

---

### 📨 Tab 3 — Multi-Channel Dispatch

Three channels, each with its **own independent circuit breaker**:

| Channel | Provider | Circuit Breaker | Fallback |
|---|---|---|---|
| 📱 WhatsApp | Meta Business Cloud API | 3 failures → OPEN 60s | → SMS |
| 💬 SMS | Twilio REST API | 3 failures → OPEN 60s | → Email |
| 📧 Email | SMTP (Gmail / any) | 5 failures → OPEN 120s | → HITL notify |

Auto-fallback chain: **WhatsApp → SMS → Email → HITL notification**

**Outcome:** Zero dropped notifications — if WhatsApp is down, SMS fires; if SMS fails, Email takes over.

---

### 👤 Tab 4 — HITL Approvals + ROI Calculator

**Human-in-the-Loop queue** automatically triggered when:
- Transaction amount > ₹50,000 (HIGH_VALUE)
- LLM proposed discount > 10% (HIGH_DISCOUNT)
- ML score in ambiguous band 0.40–0.60 (AMBIGUOUS_SCORE)
- 3+ prior failed attempts (REPEATED_FAIL)

**Built-in ROI Calculator** computes:
- Net Recovered Revenue (₹) after AI costs and gateway fees
- Recovery rate lift % with Z-score statistical significance (95% CI)
- Revenue waterfall chart: Control → Variant
- Discount sensitivity what-if slider (0–15%)

**Outcome:** Every high-stakes decision has a human gate; operators see exact ROI before approving.

---

### 🧪 Tab 5 — A/B Testing Engine

- **Thompson Sampling** contextual bandit selects strategy arms in real time
- Context buckets: failure category × ML score band × amount band
- Live metrics: recovery rate, lift %, confidence intervals
- Statistical significance test at 95% CI (Z-score)
- Historical experiment table with past campaign results
- Modes: `thompson` (default) · `linucb` · `epsilon_greedy`

**Outcome:** Recovery strategy continuously improves based on real outcomes — no manual A/B setup.

---

### 💥 Tab 6 — Chaos Simulator

Fire **500 concurrent HMAC-signed webhooks** directly from the browser:
- Real-time latency histogram (p50 / p95 / p99)
- Throughput (requests/second)
- SLA pass/fail verdict (< 15 ms target)
- Chaos modes: payload corruption, duplicate injection, oversized payloads, bad signatures

**Outcome:** Demonstrates sub-15ms ACK SLA under 500 concurrent requests with 0 dropped webhooks.

---

### 🏢 Tab 7 — Multi-Tenant Merchants

- Per-merchant dashboards with isolated data views
- Plan tiers: Starter / Growth / Enterprise with rate limits
- Simulated revenue recovery time-series per merchant
- Tenant isolation audit table — DB namespace, API scope, enforcement status
- Multi-tenant comparison bar chart

**Outcome:** Enterprise SaaS isolation — Tenant A can never see Tenant B's data.

---

### 💡 Tab 8 — EV Engine & Agent Graph

**Expected Value Calculator:**
- Live EV = P × R − (OpFee + GwCost) with PROCEED / BYPASS / SHADOW decision
- 2-D heat-map: ML score vs discount → EV colour gradient
- Breakeven chart: minimum P required to PROCEED at each discount level
- Shadow ledger browser — browse all bypass and SHADOW intercept events

**Thompson Sampling Bandit Report:**
- Live arm performance chart per context bucket
- Alpha / Beta / Mean / Pulls per arm

**Agent Graph Diagram:**
- Interactive stateful pipeline: INGEST → SCORE → ROOT_CAUSE → EV_GATE → DISPATCH → MONITOR → REFLECT
- Kafka/Redpanda streaming status

**Outcome:** Full observability into why actions were taken, bypassed, or self-healed.

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Razorpay  →  POST /webhook/razorpay                                     │
│  HMAC-SHA256 verified  ──────────────────  202 ACK returned in < 15 ms  │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
            ┌────────────────▼─────────────────────┐
            │  Event Queue (3 backends)             │
            │  ├─ asyncio.Queue    (dev / Cloud)    │
            │  ├─ Celery + Redis   (USE_CELERY=1)   │
            │  └─ Kafka/Redpanda   (USE_KAFKA=1)    │
            │  Idempotency: Redis SET NX  TTL=24h   │
            └────────────────┬─────────────────────┘
                             │
   ┌─────────────────────────▼──────────────────────────────────────────┐
   │                 agent_graph.py — Stateful Agent Graph              │
   │                                                                    │
   │   INGEST ──▶ SCORE ──▶ ROOT_CAUSE ──▶ Contextual Bandit            │
   │                                            │                       │
   │                                         EV_GATE                   │
   │                              EV = P × R − (OpFee + GwCost)         │
   │                              ├─ BYPASS ──▶ shadow_ledger           │
   │                              └─ PROCEED ──▶ DISPATCH               │
   │                                                 │                  │
   │                                            MONITOR                 │
   │                                      ├─ ok ──▶ AUDIT_LOG           │
   │                                      └─ fail ──▶ REFLECT           │
   │                                                     │              │
   │                              (select fallback channel,             │
   │                               reduce op_fee, retry EV_GATE)        │
   └────────────────────────────────────────────────────────────────────┘
                             │
   ┌─────────────────────────▼──────────────────────────────────────────┐
   │  Storage                                                           │
   │  ├─ SQLite WAL          (dev / Streamlit Cloud)                    │
   │  └─ PostgreSQL Aurora   (production — RLS + monthly partitions)    │
   │  SHA-256 hash-chain + HMAC-SHA256 per-row immutable audit ledger   │
   └────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Option 1 — Streamlit Cloud (no install)

**[recoverai-enterprise.streamlit.app](https://recoverai-enterprise.streamlit.app)** — click and it works. No secrets required.

---

### Option 2 — Run locally

```bash
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI

python -m venv .venv
source .venv/bin/activate          # Linux/macOS
.venv\Scripts\Activate.ps1         # Windows PowerShell

pip install -r requirements.txt
streamlit run streamlit_app.py
# → opens http://localhost:8501
```

> First launch auto-seeds 60 synthetic transactions. Every chart populates immediately.  
> Click **🌱 Seed Demo Data** in the sidebar to add more.

---

### Option 3 — Docker

```bash
# Dashboard + API
docker compose up api dashboard

# With extras
docker compose --profile dev up          # + data simulator
docker compose --profile chaos up        # + stress tester
docker compose --profile monitoring up   # + Prometheus + Grafana (:3000)
```

---

### Option 4 — Run the FastAPI API separately

```bash
uvicorn recover_ai.main:app --host 0.0.0.0 --port 8000

# Simulate payment failures
python recover_ai/data_simulator.py --burst 50

# Chaos stress test (500 concurrent HMAC webhooks)
python recover_ai/data_simulator.py --chaos 500
```

---

## ☁️ Deploy to Streamlit Community Cloud

1. **Fork** or use `SumedhPatil1507/RecoverAI`
2. [share.streamlit.io](https://share.streamlit.io) → **New app** → branch `main` → main file `streamlit_app.py`
3. **Deploy** — no secrets needed for the demo

### Add secrets for full features

App → **⋮ → Settings → Secrets**:

```toml
# Required on Streamlit Cloud
DATABASE_PATH = "/tmp/recover_ai_enterprise.db"
ML_MODEL_PATH = "/tmp/recover_ai_lgbm.pkl"

# Razorpay keys  (dashboard.razorpay.com → Settings → API Keys)
RAZORPAY_WEBHOOK_SECRET = "your_webhook_secret"
RAZORPAY_KEY_ID         = "rzp_test_xxxxxxxxxxxx"
RAZORPAY_KEY_SECRET     = "your_key_secret"

# Security keys  (python -c "import secrets; print(secrets.token_hex(32))")
AUDIT_HMAC_KEY = "64-char-hex"
JWT_SECRET_KEY  = "64-char-hex"

# Optional — leave blank for rule-engine-only mode
OPENAI_API_KEY = ""

# Optional — Gmail SMTP (App Password, not account password)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "you@gmail.com"
SMTP_PASS = "16-char-app-password"

# Optional — agent graph + bandit mode
AGENT_GRAPH = "0"        # set "1" to enable stateful cyclic graph
BANDIT_MODE = "thompson" # thompson | linucb | epsilon_greedy
```

---

## 📊 Outcomes

| Metric | Result |
|---|---|
| Webhook ACK latency | **< 15 ms p99** — `202 Accepted`, all work async |
| Throughput | **> 1 000 txns/min** per worker process |
| ML recoverability | LightGBM Brier score < 0.30; LogisticRegression fallback |
| Drift detection | KS two-sample test + PSI on 500-call sliding window |
| Model hot-swap | `os.replace()` atomic — zero downtime on retrain |
| EV precision | Pure `Decimal` arithmetic — no floating-point drift |
| Audit integrity | SHA-256 chain + HMAC-SHA256 per row; tampered `log_id` list on breach |
| Discount guardrail | Hard-capped at 15 % at schema level AND pipeline level |
| Dispatch resilience | Per-channel circuit breaker + REFLECT fallback (2 cycles) |
| Test coverage | **132 tests**, 4 isolated files, cross-module secret isolation |
| Security | HMAC webhook auth, AES-256-GCM column encryption, JWT RBAC |
| Infrastructure | Terraform IaC — EKS Fargate, Aurora PG Serverless v2, Redis |

---

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -q --timeout=180
# → 132 passed, 1 skipped (live DeepEval — requires OPENAI_API_KEY)
```

| Test file | Count | Covers |
|---|---:|---|
| `test_enterprise_controls.py` | 7 | EV paise arithmetic, Principal RBAC, `authorize()` |
| `test_ev_engine.py` | 67 | EV edge cases, shadow mode, chaos, Thompson bandit, JWT |
| `test_llm_eval.py` | 14 | LLM faithfulness ≥ 0.80, tone, hallucination, guardrail |
| `test_enterprise_flow.py` | 59 | HMAC, PII, AES-GCM, tamper detection, HITL FSM, 500-concurrent chaos |

**Chaos scenarios tested:**
- DB partition failure mid-write → chain survives
- Thread worker crash after first write → no corruption
- 20 threads × 5 concurrent writes → all entries recoverable
- Mid-chain hash corruption → `verify_audit_integrity_detailed()` returns exact tampered `log_id`s
- 500 concurrent webhooks → 0 dropped, p95 within SLA

---

## 🔐 Security

| Control | How it works |
|---|---|
| Webhook auth | HMAC-SHA256 on every request, constant-time compare |
| PII redaction | Regex + field-name walk strips email/phone/card before DB or LLM |
| Audit ledger | SHA-256 hash-chain + HMAC-SHA256 per row, SOC2-style tamper report |
| Column encryption | AES-256-GCM, HKDF-SHA256 key derivation, b64 fallback |
| Discount cap | 15 % max enforced in Pydantic schema validator + pipeline outer check |
| EV gate | Negative-EV actions always bypassed; counterfactuals in shadow ledger |
| HITL gate | > ₹50k or ambiguous ML score → human approval before dispatch |
| JWT RBAC | HS256, 3 roles (Admin / Operator / Auditor), `Principal`, `authorize()` |
| Tenant isolation | `merchant_id` on every table; PostgreSQL RLS in production |

---

## 🛠 Tech Stack

| | Technology |
|---|---|
| **Dashboard** | Streamlit 1.40+, Plotly WebGL |
| **API** | FastAPI, Uvicorn, asyncio |
| **Agent graph** | Custom stateful cyclic graph (LangGraph-compatible) |
| **Bandit** | Thompson Sampling β(α,β) · LinUCB · ε-greedy |
| **Queue** | asyncio.Queue · Celery + Redis · Kafka/Redpanda (aiokafka) |
| **ML** | LightGBM, scikit-learn, scipy (KS + PSI drift) |
| **Database** | SQLite WAL (dev) · PostgreSQL Aurora Serverless v2 (prod) |
| **Security** | HMAC-SHA256 · AES-256-GCM · JWT HS256 · PII redaction |
| **EV engine** | Pure `Decimal` arithmetic |
| **Observability** | Prometheus `/metrics` · OpenTelemetry 9-node spans |
| **IaC** | Terraform — EKS Fargate · Aurora PG · ElastiCache · Secrets Manager |
| **CI/CD** | GitHub Actions 8-stage: ruff → Bandit → 132 tests → Docker → Terraform → kubectl |

---

## 📁 Project Layout

```
RecoverAI/
├── streamlit_app.py          # 8-tab Streamlit demo (no secrets needed)
├── requirements.txt
├── recover_ai/
│   ├── agent_graph.py        # Stateful cyclic graph (Epic 1)
│   ├── bandit.py             # Thompson Sampling bandit (Epic 2)
│   ├── kafka_worker.py       # Kafka/Redpanda streaming (Epic 3)
│   ├── agent_engine.py       # Linear pipeline fallback
│   ├── ev_engine.py          # EV gate + shadow mode
│   ├── expected_value.py     # Paise-exact EV arithmetic
│   ├── ml_scorer.py          # LightGBM + drift + hot-swap
│   ├── auth.py               # JWT + Principal + authorize()
│   ├── database.py           # SQLite + audit ledger
│   ├── db_postgres.py        # Async PostgreSQL backend
│   ├── security.py           # PII + HMAC + AES-256-GCM
│   ├── main.py               # FastAPI (< 15 ms ACK)
│   ├── queue_worker.py       # Celery / asyncio.Queue
│   └── integrations/
│       ├── razorpay_links.py     # Circuit-broken Razorpay client
│       └── whatsapp_notifier.py  # WA/SMS/Email dispatcher
├── tests/
│   ├── conftest.py               # DB + secret isolation
│   ├── test_enterprise_controls.py
│   ├── test_ev_engine.py
│   ├── test_llm_eval.py
│   └── test_enterprise_flow.py
├── terraform/                # AWS EKS, Aurora, Redis, Secrets Manager
├── .github/workflows/        # 8-stage CI/CD pipeline
├── docker-compose.yml        # dev / chaos / monitoring profiles
└── monitoring/               # Prometheus + Grafana
```

---

<div align="center">

---

Built with ❤️ for the **Razorpay AI Buildathon · Track 03**

**[Live Demo](https://recoverai-enterprise.streamlit.app)** · **[GitHub](https://github.com/SumedhPatil1507/RecoverAI)**

*Demo runs in staging/shadow mode — no live payments are made.*

</div>
