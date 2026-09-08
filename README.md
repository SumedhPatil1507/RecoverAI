# 🏦 RecoverAI Enterprise

> **Agentic Payment Recovery Platform** — Razorpay AI Buildathon · Track 03

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://recoverai-enterprise.streamlit.app)
[![Tests](https://img.shields.io/badge/tests-100%20passed-brightgreen.svg)](#testing)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

RecoverAI autonomously recovers failed Razorpay payments using a multi-agent pipeline with enterprise-grade financial controls: LightGBM ML scoring → KS drift detection → LLM root-cause classification → **Expected Value gate** → A/B-tested recovery strategies → Razorpay Payment Links → WhatsApp/SMS dispatch → HITL approval queue → SHA-256 + HMAC cryptographic audit ledger.

---

## ✨ Features

| Tab | What it does |
|-----|-------------|
| 📊 **Intelligence Hub** | Live KPIs, ML funnel, time-series, SHA-256+HMAC audit ledger |
| 🔗 **Payment Links** | Razorpay Payment Links API with circuit breaker + smart routing |
| 📨 **Dispatch** | WhatsApp / Twilio SMS / SMTP with per-channel circuit breakers |
| 👤 **HITL Approvals** | Human-in-the-Loop queue + A/B Financial ROI Calculator |
| 🧪 **A/B Testing** | Live experiment engine with z-score, net revenue lift, margin |
| 💥 **Chaos Simulator** | 500-concurrent webhook stress test with p95/p99 latency |
| 🏢 **Merchants** | Multi-tenant isolation with per-merchant dashboards |
| 💡 **EV Engine** | Expected Value calculator, heat-map, shadow ledger, RBAC inspector |

---

## 🏗 Enterprise Architecture

```
Razorpay Webhook  ──HMAC──▶  POST /webhook/razorpay  (202 ACK < 15 ms)
                                      │
                              asyncio.Queue / Celery+Redis
                                      │
              ┌───────────────────────▼──────────────────────────┐
              │   agent_engine.py  (9-node OTel-traced pipeline) │
              │                                                   │
              │  Ingest → ML Score (KS drift) → A/B Route →      │
              │  LLM/Rules → Discount Guardrail (15% cap) →       │
              │  HITL Gate (> ₹50k / ambiguous) →                │
              │  ┌─────────────────────────────┐                 │
              │  │  EV Gate (NEW)              │                 │
              │  │  EV = P×R − (OpFee+GwCost)  │                 │
              │  │  EV ≤ 0  → Shadow Ledger    │                 │
              │  │  EV > 0  → Razorpay Link +  │                 │
              │  │           WhatsApp/SMS       │                 │
              │  └─────────────────────────────┘                 │
              │  SHA-256 + HMAC Audit Log                        │
              └──────────────────────────────────────────────────┘
                                      │
              ┌───────────────────────▼──────────────────────────┐
              │  SQLite (local) / PostgreSQL Aurora (production)  │
              │  Row-Level Security · Monthly range partitions    │
              └──────────────────────────────────────────────────┘
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

Dashboard auto-seeds 60 demo transactions on first load.

---

## ☁️ Deploy to Streamlit Cloud

### Step 1 — Connect
**[share.streamlit.io](https://share.streamlit.io)** → New app → `SumedhPatil1507/RecoverAI`, branch `main`, main file `streamlit_app.py`

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

# Audit HMAC (generate: python -c "import secrets; print(secrets.token_hex(32))")
AUDIT_HMAC_KEY = "your_64_char_hex_key"

# JWT RBAC (generate: python -c "import secrets; print(secrets.token_hex(32))")
JWT_SECRET_KEY = "your_64_char_jwt_signing_key"

# EV Engine tuning (optional — shown with defaults)
EV_MINIMUM_RUPEES   = "0.0"
EV_OPERATIONAL_FEE  = "2.50"
EV_GATEWAY_COST_PCT = "1.5"
EXECUTION_MODE      = "LIVE"   # change to SHADOW for dry-run mode

# Optional: Gmail SMTP
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "sumedhp612@gmail.com"
SMTP_PASS = "your_16_char_app_password"
SMTP_FROM = "sumedhp612@gmail.com"

# Optional: OpenAI (leave blank for rule engine)
OPENAI_API_KEY = ""
```

> **`DATABASE_PATH` must be `/tmp/...`** — Streamlit Cloud repo root is read-only.

---

## 💡 EV Engine

Before dispatching any recovery action, the pipeline calculates Expected Value:

```
EV = (P_recovery × Recoverable_Amount) − (Operational_Fee + Gateway_Cost)
```

| EV | Action |
|----|--------|
| EV > threshold (default 0) | **PROCEED** — Razorpay link created + notifications sent |
| EV ≤ threshold | **BYPASS** — no dispatch; event logged to shadow ledger |
| EXECUTION_MODE = SHADOW | **SHADOW INTERCEPT** — all dispatches suppressed; counterfactuals logged |

Configure via env vars: `EV_MINIMUM_RUPEES`, `EV_OPERATIONAL_FEE`, `EV_GATEWAY_COST_PCT`, `EXECUTION_MODE`.

---

## 🔑 JWT RBAC

Three roles with scoped API access:

| Role | Scope | Use case |
|------|-------|----------|
| `admin` | All endpoints | Engineering, platform owners |
| `operator` | HITL queue + read-only stats | Recovery agents, ops team |
| `auditor` | Read-only audit + stats | Compliance, finance |

Issue a token:
```bash
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"merchant_id": "mid_001", "api_key": "secret", "role": "admin"}'
```

---

## 🧪 Testing

100 tests across 2 test files:

```bash
pip install -r requirements-dev.txt
pytest tests/ -v --timeout=180
```

| File | Tests | Coverage |
|------|-------|---------|
| `test_enterprise_flow.py` | 59 | HMAC, PII, AES-GCM, audit chain, HITL FSM, CircuitBreaker, KS/PSI, webhook chaos |
| `test_ev_engine.py` | 41 | EV arithmetic, zero/negative/shadow, tenant isolation, JWT auth, audit chain with EV |

---

## 🐳 Docker

```bash
docker compose up api dashboard
docker compose --profile dev up      # + simulator
docker compose --profile chaos up    # + stress tester
docker compose --profile monitoring up  # + Prometheus + Grafana
```

---

## 📁 Project Structure

```
RecoverAI/
├── streamlit_app.py         # 8-tab dashboard (EV Engine tab added)
├── requirements.txt
├── recover_ai/
│   ├── main.py              # FastAPI: /auth/token + /api/v1/ev/* + /api/v1/shadow/*
│   ├── ev_engine.py         # EV = P×R−Costs; PROCEED/BYPASS; shadow intercept
│   ├── auth.py              # JWT HS256; Admin/Operator/Auditor RBAC
│   ├── db_postgres.py       # Async PG (asyncpg pool, RLS, monthly partitions)
│   ├── agent_engine.py      # 9-node pipeline with EV gate at Node 8
│   ├── ml_scorer.py         # LightGBM + KS/PSI drift + hot-swap
│   ├── database.py          # SQLite WAL + shadow_ledger table + audit ledger
│   ├── schemas.py           # Pydantic models (EV_BYPASSED status added)
│   ├── security.py          # PII redaction + HMAC + AES-256-GCM
│   └── config.py            # All settings incl. EV/JWT/PG tunables
├── tests/
│   ├── test_enterprise_flow.py   # 59 tests
│   └── test_ev_engine.py         # 41 tests
└── terraform/               # AWS EKS, Aurora PG, ElastiCache, Secrets Manager
```

---

## 🔐 Security Model

| Layer | Implementation |
|-------|---------------|
| Webhook auth | HMAC-SHA256 on every inbound event, constant-time compare |
| PII redaction | Regex + field-name walk before any DB write or LLM call |
| Audit ledger | SHA-256 hash-chain **+** HMAC-SHA256 per row; tamper index reporting |
| Column encryption | AES-256-GCM with HKDF key derivation |
| Discount guardrail | LLM discount capped at 15% (two independent checks) |
| EV gate | Negative-EV actions bypassed; shadow events recorded as counterfactuals |
| HITL gate | Transactions > ₹50k or ambiguous ML scores held for human review |
| JWT RBAC | HS256 tokens, 3 roles, 8-hour expiry, constant-time comparison |
| Tenant isolation | `merchant_id` on all tables; PostgreSQL RLS enforces row-level access |

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| Dashboard | Streamlit 1.40+, Plotly |
| API | FastAPI, Uvicorn, asyncio |
| Queue | asyncio.Queue (dev) / Celery + Redis (prod) |
| ML | LightGBM, scikit-learn, scipy (KS drift) |
| Database | SQLite WAL (dev) / PostgreSQL Aurora Serverless v2 (prod) |
| Integrations | Razorpay Payment Links, Meta WhatsApp Cloud, Twilio, SMTP |
| Security | HMAC-SHA256, AES-256-GCM, JWT RBAC, PII redaction |
| EV Engine | Decimal arithmetic, EV gate, shadow ledger, counterfactual logging |
| Observability | Prometheus, OpenTelemetry spans, Grafana |
| IaC | Terraform (AWS EKS Fargate, Aurora PG, ElastiCache Redis) |
| CI/CD | GitHub Actions: ruff → Bandit SAST → 100 tests → Docker → Terraform → kubectl |
