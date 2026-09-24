# 🏦 RecoverAI Enterprise

> **Agentic Payment Recovery Platform** — Razorpay AI Buildathon · Track 03

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://recoverai-enterprise.streamlit.app)
[![Tests](https://img.shields.io/badge/tests-132%20passed-brightgreen.svg)](#testing)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

RecoverAI autonomously recovers failed Razorpay payments using a **stateful multi-agent graph** with Thompson Sampling bandit routing, LightGBM ML scoring, KS drift detection, EV-gated dispatch, HITL approval workflows, cryptographic audit ledger, and Kafka/Redpanda streaming.

---

## ✨ Features

| Tab | What it does |
|-----|-------------|
| 📊 **Intelligence Hub** | Live KPIs, ML funnel, time-series, SHA-256+HMAC audit ledger |
| 🔗 **Payment Links** | Razorpay Payment Links API with circuit breaker + smart routing |
| 📨 **Dispatch** | WhatsApp/SMS/Email with per-channel circuit breakers |
| 👤 **HITL Approvals** | Human-in-the-Loop queue + A/B Financial ROI Calculator |
| 🧪 **A/B Testing** | Live experiment engine with z-score and net revenue lift |
| 💥 **Chaos Simulator** | 500-concurrent webhook stress test |
| 🏢 **Merchants** | Multi-tenant isolation with per-merchant dashboards |
| 💡 **EV Engine** | EV calculator, heat-map, bandit report, agent graph diagram |

---

## 🏗 Architecture

```
Webhook (202 ACK < 15ms)
        │
   ┌────▼─────────────────────────────────────────┐
   │  Kafka / asyncio.Queue                       │
   └────────────────────────┬─────────────────────┘
                            │
   ┌────────────────────────▼──────────────────────┐
   │  agent_graph.py  — Stateful Graph Runner      │
   │                                               │
   │  INGEST → SCORE → ROOT_CAUSE                  │
   │       ↓                                       │
   │  Contextual Bandit (Thompson Sampling)        │
   │       ↓                                       │
   │  EV_GATE  (EV = P×R − Costs)                  │
   │   ├─ BYPASS → shadow_ledger + audit_log       │
   │   └─ PROCEED → DISPATCH                       │
   │                    │                          │
   │               MONITOR                         │
   │               ├─ ok → AUDIT_LOG               │
   │               └─ fail → REFLECT (≤2 cycles)   │
   │                    │                          │
   │             (fallback channel)                │
   │               → EV_GATE retry                 │
   └───────────────────────────────────────────────┘
                            │
   ┌────────────────────────▼──────────────────────┐
   │  SQLite (dev) / PostgreSQL Aurora (prod)      │
   │  SHA-256 hash-chain + HMAC-per-row ledger     │
   └───────────────────────────────────────────────┘
```

---

## 🆕 Epic Changes (latest)

### Epic 1 — LangGraph Stateful Agent
- **`recover_ai/agent_graph.py`** — Full stateful graph: INGEST→SCORE→ROOT_CAUSE→EV_GATE→DISPATCH→MONITOR→REFLECT
- REFLECT self-healing: on dispatch failure → select fallback channel (WA→SMS→Email) → re-evaluate EV → retry
- OTel spans on every node with `token_count`, `latency_ms`, `prompt_version` attributes
- Enable with `AGENT_GRAPH=1`; falls back to linear pipeline without it

### Epic 2 — Contextual Bandit
- **`recover_ai/bandit.py`** — Thompson Sampling Beta(α,β) per (arm, context_bucket)
- Context = failure_category × ml_score_band × amount_band → isolated priors per bucket
- Modes: `BANDIT_MODE=thompson` (default) | `linucb` | `epsilon_greedy`
- **`tests/test_llm_eval.py`** — 14 LLM eval tests: faithfulness, tone compliance, zero hallucination, discount guardrail, confidence calibration

### Epic 3 — Kafka Streaming
- **`recover_ai/kafka_worker.py`** — async aiokafka producer/consumer, at-least-once semantics
- Manual offset commit after successful processing; DLQ topic for exhausted retries
- Falls back to asyncio.Queue when `KAFKA_BOOTSTRAP_SERVERS` is not set
- Enable with `KAFKA_BOOTSTRAP_SERVERS=broker:9092`

### Epic 4 — Chaos Tests + OTel
- **`tests/test_ev_engine.py`** — 13 new chaos tests: DB partition failure, thread crashes, concurrent write storms, mid-chain corruption
- **`tests/test_enterprise_controls.py`** — 7 EV unit-economic + RBAC authorization tests
- `auth.py` — `Principal` dataclass + `authorize()` function added for non-HTTP RBAC

---

## ⚡ Quick Start (local)

```bash
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
streamlit run streamlit_app.py
```

---

## ☁️ Deploy to Streamlit Cloud

### Step 1 — Connect
[share.streamlit.io](https://share.streamlit.io) → New app → `SumedhPatil1507/RecoverAI`, branch `main`, main file `streamlit_app.py`

### Step 2 — Secrets
Streamlit Cloud → your app → ⋮ → Settings → Secrets:

```toml
# Required
ENVIRONMENT   = "production"
DATABASE_PATH = "/tmp/recover_ai_enterprise.db"
ML_MODEL_PATH = "/tmp/recover_ai_lgbm.pkl"

# Razorpay
RAZORPAY_WEBHOOK_SECRET = "your_webhook_secret"
RAZORPAY_KEY_ID         = "rzp_test_xxxxxxxxxxxx"
RAZORPAY_KEY_SECRET     = "your_key_secret"

# Security
AUDIT_HMAC_KEY = "your_64_char_hex_key"
JWT_SECRET_KEY = "your_64_char_jwt_key"

# Optional: OpenAI (leave blank for rule engine only)
OPENAI_API_KEY = ""

# Optional: Gmail SMTP
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "sumedhp612@gmail.com"
SMTP_PASS = "your_16_char_app_password"
SMTP_FROM = "sumedhp612@gmail.com"

# Optional: Agent Graph + Bandit
AGENT_GRAPH  = "0"         # set to "1" to enable stateful graph
BANDIT_MODE  = "thompson"  # or "linucb" or "epsilon_greedy"

# Optional: Kafka/Redpanda streaming
# KAFKA_BOOTSTRAP_SERVERS = "broker:9092"
```

> **`DATABASE_PATH` must be `/tmp/...`** — Streamlit Cloud repo root is read-only.

---

## 🧪 Testing

132 tests across 4 test files (1 skipped = live DeepEval):

```bash
pip install -r requirements-dev.txt
pytest tests/ -v --timeout=180
```

| File | Tests | Coverage |
|------|-------|---------|
| `test_enterprise_controls.py` | 7 | EV unit-economic guardrails, Principal RBAC |
| `test_ev_engine.py` | 67 | EV arithmetic, shadow mode, chaos resilience, bandit, JWT |
| `test_llm_eval.py` | 14 | LLM faithfulness, tone, hallucination, guardrail, calibration |
| `test_enterprise_flow.py` | 59 | HMAC, PII, AES-GCM, audit chain, HITL FSM, circuit breaker, webhook chaos |

---

## 🔑 Gmail App Password

`SMTP_PASS` must be a **16-character App Password**:
1. [myaccount.google.com](https://myaccount.google.com) → Security → 2-Step Verification → ON
2. Security → **App passwords** → Mail → Other → `RecoverAI`
3. Copy the 16-char password → paste as `SMTP_PASS`

---

## 📁 Project Structure

```
RecoverAI/
├── streamlit_app.py            # 8-tab dashboard + bandit/graph controls
├── requirements.txt
├── recover_ai/
│   ├── agent_graph.py          # Stateful graph: INGEST→…→REFLECT (Epic 1)
│   ├── bandit.py               # Thompson Sampling contextual bandit (Epic 2)
│   ├── kafka_worker.py         # Kafka/Redpanda async streaming (Epic 3)
│   ├── agent_engine.py         # Linear pipeline (fallback when AGENT_GRAPH=0)
│   ├── ml_scorer.py            # LightGBM + KS/PSI drift + hot-swap
│   ├── ev_engine.py            # EV = P×R − Costs; PROCEED/BYPASS
│   ├── auth.py                 # JWT HS256, Principal, authorize()
│   ├── expected_value.py       # Deterministic EV guardrails (paise arithmetic)
│   ├── database.py             # SQLite + SHA-256+HMAC audit ledger + shadow
│   ├── schemas.py              # Pydantic models
│   ├── security.py             # PII redaction + HMAC + AES-256-GCM
│   └── main.py                 # FastAPI gateway
├── tests/
│   ├── conftest.py             # Cross-module DB/secret isolation
│   ├── test_enterprise_controls.py   # EV guardrails + RBAC (7 tests)
│   ├── test_ev_engine.py       # EV + chaos + bandit (67 tests)
│   ├── test_llm_eval.py        # LLM evaluation harness (14 tests)
│   └── test_enterprise_flow.py # Integration + chaos (59 tests)
└── terraform/                  # AWS EKS, Aurora, Redis, Secrets Manager
```

---

## 🔐 Security Model

| Layer | Implementation |
|-------|---------------|
| Webhook auth | HMAC-SHA256 + constant-time compare |
| PII redaction | Regex + field-name walk before DB/LLM |
| Audit ledger | SHA-256 hash-chain + HMAC-SHA256 per row |
| Column encryption | AES-256-GCM with HKDF |
| Discount guardrail | 15% cap in 2 independent checks + EV guardrail |
| EV gate | Negative-EV bypassed + shadow logged |
| HITL gate | >₹50k or ambiguous ML score → human review |
| JWT RBAC | HS256, Principal, authorize(), 3 roles |
| Tenant isolation | `merchant_id` on all tables; PostgreSQL RLS in prod |

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| Dashboard | Streamlit 1.40+, Plotly |
| API | FastAPI, Uvicorn, asyncio |
| Agent Graph | Custom stateful graph (LangGraph-compatible) |
| Bandit | Thompson Sampling + LinUCB + ε-greedy |
| Queue | asyncio.Queue (dev) / Celery+Redis / Kafka+aiokafka (prod) |
| ML | LightGBM, KS/PSI drift, hot-swap retraining |
| Database | SQLite WAL (dev) / PostgreSQL Aurora Serverless v2 (prod) |
| Security | HMAC-SHA256, AES-256-GCM, JWT HS256, PII redaction |
| EV Engine | Decimal arithmetic, EV gate, shadow ledger |
| Observability | Prometheus, OpenTelemetry (9 node spans + token/latency attrs) |
| IaC | Terraform (AWS EKS Fargate, Aurora PG, ElastiCache Redis) |
| CI/CD | GitHub Actions: ruff → Bandit → 132 tests → Docker → Terraform → kubectl |
