<div align="center">

<img src="https://img.shields.io/badge/RecoverAI-Enterprise-gold?style=for-the-badge&logo=razorpay&logoColor=white" alt="RecoverAI Enterprise" height="40"/>

# 🏦 RecoverAI Enterprise

### *Agentic Payment Recovery · Razorpay AI Buildathon · Track 03*

<br/>

[![Live Demo](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://recoverai-enterprise.streamlit.app)
&nbsp;
[![Tests](https://img.shields.io/badge/tests-132%20passed-00c851?style=flat-square&logo=pytest&logoColor=white)](tests/)
&nbsp;
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
&nbsp;
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
&nbsp;
[![License: MIT](https://img.shields.io/badge/License-MIT-f0ad4e?style=flat-square)](LICENSE)

<br/>

**RecoverAI autonomously recovers failed Razorpay payments using a stateful multi-agent graph,  
Thompson Sampling bandit routing, ML scoring, unit-economic EV guardrails,  
HITL approval workflows, and a tamper-proof cryptographic audit ledger.**

<br/>

[**🚀 Live Demo**](https://recoverai-enterprise.streamlit.app) &nbsp;·&nbsp; [**⚡ Quick Start**](#-quick-start) &nbsp;·&nbsp; [**🏗 Architecture**](#-architecture) &nbsp;·&nbsp; [**🧪 Tests**](#-testing)

</div>

---

## 🎯 What RecoverAI Does

When a payment fails on Razorpay, RecoverAI:

| Step | What happens | Time |
|------|-------------|------|
| **Ingest** | HMAC-verified webhook received, payment_id enqueued | < 1 ms |
| **Score** | LightGBM predicts recoverability 0–1 with KS/PSI drift detection | < 5 ms |
| **Route** | Thompson Sampling bandit selects control vs variant arm | < 1 ms |
| **Classify** | Root cause classified (gateway down / insufficient funds / etc.) | < 2 ms |
| **EV Gate** | `EV = P × R − (OpFee + GwCost)` — skips uneconomic recoveries | < 1 ms |
| **Dispatch** | Razorpay Payment Link created + WhatsApp / SMS / Email sent | async |
| **Monitor** | Outcome observed; REFLECT self-heals on failure | async |
| **Log** | Immutable SHA-256 + HMAC cryptographic audit entry written | < 2 ms |

**Webhook ACK returns `202 Accepted` in < 15 ms** — all heavy work is async.

---

## ✨ Feature Overview

<table>
<tr>
<td width="50%" valign="top">

### 📊 Intelligence Hub
Real-time KPIs — revenue at risk, recovered, recovery rate, avg ML score, ledger status. ML-augmented recovery funnel, dual-trace time-series chart, ML score histogram, failure root-cause donut, and one-click SHA-256 + HMAC ledger verification with tamper-index reporting.

### 🔗 Payment Links
Razorpay Payment Links API with async circuit breaker (CLOSED → OPEN → HALF-OPEN), **smart routing** for GATEWAY_DOWN / BANK_DECLINE failures, 15 % discount hard-cap guardrail, and bulk-create for batch recoveries.

### 📨 Multi-Channel Dispatch
WhatsApp Business Cloud API, Twilio SMS, and SMTP email — each channel has its **own independent circuit breaker**. Auto-fallback: WhatsApp → SMS → Email → HITL notification.

### 👤 HITL Approvals + ROI Calculator
Human-in-the-Loop approval queue for transactions > ₹50k or ambiguous ML scores. Built-in **A/B Financial Lift & ROI Calculator** with Z-score significance testing, net revenue lift, margin analysis, and discount sensitivity slider.

</td>
<td width="50%" valign="top">

### 🧪 A/B Testing Engine
Live experiment engine comparing rule engine (control) vs LLM-augmented (variant). Real-time recovery rates, lift %, confidence intervals, and historical experiment table.

### 💥 Chaos Simulator
Fire **500 concurrent HMAC-signed webhooks** from the UI. Reports p50 / p95 / p99 latency, throughput (req/s), and SLA pass/fail verdict. Also tests dispatch circuit breakers under sustained load.

### 🏢 Multi-Tenant Merchants
Per-merchant dashboards with plan tiers, simulated recovery timelines, comparison bar chart, and tenant isolation audit table showing DB namespace, API scope, and rate limits.

### 💡 EV Engine & Agent Graph
EV calculator with live decision, 2-D heat-map, breakeven chart, shadow ledger browser, **Thompson Sampling bandit arm report**, and an interactive agent graph node-transition diagram with Kafka streaming status.

</td>
</tr>
</table>

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Razorpay  POST /webhook/razorpay                                       │
│  ── HMAC-SHA256 verified ──────────────── 202 ACK in < 15 ms ──────────│
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
              ┌────────────────▼──────────────────┐
              │  Kafka / Redpanda  (aiokafka)      │  ← USE_KAFKA=1
              │  asyncio.Queue    (dev/Streamlit)  │  ← default
              │  Celery + Redis   (USE_CELERY=1)   │
              │  Idempotency: Redis SET NX 24 h    │
              └────────────────┬──────────────────┘
                               │
   ┌───────────────────────────▼────────────────────────────────────────┐
   │                  agent_graph.py  ─  Stateful Agent Graph           │
   │                                                                    │
   │  INGEST ──► SCORE ──► ROOT_CAUSE                                   │
   │                           │                                        │
   │              ┌────────────▼───────────────┐                        │
   │              │  Contextual Bandit          │                        │
   │              │  Thompson Sampling β(α,β)   │                        │
   │              │  per (category×band×amount) │                        │
   │              └────────────┬───────────────┘                        │
   │                           │                                        │
   │                        EV_GATE                                     │
   │              EV = P × R − (OpFee + GwCost)                         │
   │                 ├─ EV ≤ 0  ──► shadow_ledger  +  BYPASS            │
   │                 └─ EV > 0  ──► DISPATCH                            │
   │                                    │                               │
   │                               MONITOR                              │
   │                           ├─ ok  ──► AUDIT_LOG  ──► TERMINAL       │
   │                           └─ fail ──► REFLECT                      │
   │                                           │                        │
   │                         (fallback channel + lower op_fee)          │
   │                               ──► EV_GATE  (max 2 cycles)          │
   └────────────────────────────────────────────────────────────────────┘
                               │
   ┌───────────────────────────▼────────────────────────────────────────┐
   │  SQLite WAL (dev/Streamlit Cloud)                                  │
   │  PostgreSQL Aurora Serverless v2 (production)                      │
   │  ── Row-Level Security · Monthly range partitions ─────────────── │
   │  ── SHA-256 hash-chain + HMAC-SHA256 per-row audit ledger ──────── │
   └────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Run locally (< 2 minutes)

```bash
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
.venv\Scripts\Activate.ps1         # Windows PowerShell

pip install --upgrade pip
pip install -r requirements.txt

streamlit run streamlit_app.py
```

Open **http://localhost:8501**. The dashboard auto-seeds 60 synthetic transactions — every chart populates immediately. Click **🌱 Seed Demo Data** in the sidebar to add more.

> **Demo safety:** `streamlit_app.py` forces `ENVIRONMENT=staging`, `EXECUTION_MODE=SHADOW`, and `DATABASE_PATH=/tmp/...` at startup. **No live payments, notifications, or API calls are made.**

### Run the FastAPI service (optional)

```bash
uvicorn recover_ai.main:app --host 0.0.0.0 --port 8000

# Send 20 synthetic webhook events
python recover_ai/data_simulator.py --burst 20

# Chaos stress test (500 concurrent webhooks)
python recover_ai/data_simulator.py --chaos 500
```

### Docker

```bash
docker compose up api dashboard            # core stack
docker compose --profile dev up            # + data simulator
docker compose --profile chaos up          # + stress tester
docker compose --profile monitoring up     # + Prometheus + Grafana
```

---

## ☁️ Deploy on Streamlit Community Cloud

1. Go to **[share.streamlit.io](https://share.streamlit.io)** → **New app**
2. Repository: `SumedhPatil1507/RecoverAI` · Branch: `main` · Main file: `streamlit_app.py`
3. Click **Deploy** — **no secrets required** for the staging demo

### Optional secrets (for full features)

Navigate to your app → **⋮ → Settings → Secrets** and paste:

```toml
# ── Database (required on Streamlit Cloud) ────────────────────────────────
DATABASE_PATH = "/tmp/recover_ai_enterprise.db"
ML_MODEL_PATH = "/tmp/recover_ai_lgbm.pkl"

# ── Razorpay (dashboard.razorpay.com → Settings → API Keys) ─────────────
RAZORPAY_WEBHOOK_SECRET = "your_webhook_secret"
RAZORPAY_KEY_ID         = "rzp_test_xxxxxxxxxxxx"
RAZORPAY_KEY_SECRET     = "your_key_secret"

# ── Security (generate: python -c "import secrets; print(secrets.token_hex(32))") ──
AUDIT_HMAC_KEY = "your_64_char_hex_key"
JWT_SECRET_KEY = "your_64_char_jwt_key"

# ── Optional: OpenAI (leave blank = rule engine only) ────────────────────
OPENAI_API_KEY = ""

# ── Optional: Gmail SMTP ─────────────────────────────────────────────────
# App Password: myaccount.google.com → Security → App passwords
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "your@gmail.com"
SMTP_PASS = "your_16_char_app_password"
SMTP_FROM = "your@gmail.com"

# ── Optional: Agent graph + bandit ───────────────────────────────────────
AGENT_GRAPH = "0"        # "1" activates stateful graph
BANDIT_MODE = "thompson" # or "linucb" or "epsilon_greedy"

# ── Optional: Kafka/Redpanda streaming ───────────────────────────────────
# KAFKA_BOOTSTRAP_SERVERS = "broker:9092"
```

---

## 💡 Expected-Value Guardrail

Every recovery action is gated by a unit-economic calculation using **Decimal arithmetic** (no float rounding):

```
EV = (P_recovery × Recoverable_Amount) − (Operational_Fee + Gateway_Cost)
```

| EV result | Action |
|-----------|--------|
| `EV > threshold` (default 0) + LIVE mode | **PROCEED** — dispatch payment link + notification |
| `EV ≤ threshold` | **BYPASS** — no dispatch; event logged to shadow ledger |
| `EXECUTION_MODE=SHADOW` | **SHADOW INTERCEPT** — all dispatches suppressed; counterfactuals logged |

Configure via: `EV_MINIMUM_RUPEES`, `EV_OPERATIONAL_FEE`, `EV_GATEWAY_COST_PCT`, `EXECUTION_MODE`

---

## 📊 Key Outcomes

| Metric | Result |
|--------|--------|
| Webhook ACK latency | **< 15 ms p99** (202 Accepted) |
| ML recoverability scoring | LightGBM + LogisticRegression fallback, Brier < 0.30 |
| Drift detection | KS two-sample test + PSI on 500-call sliding window |
| Hot-swap retraining | `os.replace()` atomic swap — zero downtime |
| EV gate precision | Pure `Decimal` arithmetic — no float rounding errors |
| Audit ledger | SHA-256 hash-chain + HMAC-SHA256 per row, `verify_audit_integrity_detailed()` with tampered log_id list |
| Discount guardrail | Hard-capped at 15 % in two independent checks (schema + pipeline) |
| Dispatch resilience | Circuit breaker per channel (WA/SMS/Email) + REFLECT self-healing |
| Test coverage | **132 tests**, 4 isolated files, cross-module secret isolation |

---

## 🧪 Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -q --timeout=180
```

| File | Tests | Coverage |
|------|------:|---------|
| `test_enterprise_controls.py` | 7 | EV paise-exact arithmetic, `Principal` RBAC, `authorize()` |
| `test_ev_engine.py` | 67 | EV edge cases, shadow mode, chaos resilience, Thompson bandit, JWT |
| `test_llm_eval.py` | 14 | LLM faithfulness ≥ 0.80, tone compliance, zero hallucination, calibration |
| `test_enterprise_flow.py` | 59 | HMAC, PII, AES-GCM, audit tamper detection, HITL FSM, circuit breaker, 500-concurrent chaos |

### Chaos test highlights

- **DB partition failure** — forced connection close mid-write; chain survives intact
- **Thread worker crash** — daemon thread killed after first write; no corruption
- **Concurrent write storm** — 20 threads × 5 writes each; all entries recoverable
- **Mid-chain corruption** — corrupt entry #2; `verify_audit_integrity_detailed()` returns exact tampered `log_id`s
- **EV bypass under load** — 20 concurrent threads write shadow ledger; 0 entries lost

---

## 📁 Project Layout

```
RecoverAI/
├── streamlit_app.py              # 8-tab Streamlit demo dashboard
├── requirements.txt              # Runtime dependencies
├── requirements-dev.txt          # Test + lint tooling
│
├── recover_ai/
│   ├── agent_graph.py            # ★ Stateful graph runner (Epic 1)
│   ├── bandit.py                 # ★ Thompson Sampling contextual bandit (Epic 2)
│   ├── kafka_worker.py           # ★ Kafka/Redpanda async streaming (Epic 3)
│   ├── agent_engine.py           # Linear pipeline (AGENT_GRAPH=0 fallback)
│   ├── ev_engine.py              # EV = P×R − Costs; PROCEED / BYPASS / SHADOW
│   ├── expected_value.py         # Paise-exact EV guardrails
│   ├── ml_scorer.py              # LightGBM + KS/PSI drift + atomic hot-swap
│   ├── auth.py                   # JWT HS256 + Principal dataclass + authorize()
│   ├── database.py               # SQLite WAL + SHA-256 + HMAC audit ledger
│   ├── db_postgres.py            # Async PostgreSQL (asyncpg, RLS, partitions)
│   ├── schemas.py                # Pydantic v2 models
│   ├── security.py               # PII redaction + HMAC + AES-256-GCM
│   ├── config.py                 # pydantic-settings (v1/v2 compatible)
│   ├── main.py                   # FastAPI gateway (< 15 ms webhook ACK)
│   ├── queue_worker.py           # Celery + Redis / asyncio.Queue
│   └── integrations/
│       ├── razorpay_links.py     # Async Razorpay client + CircuitBreaker
│       └── whatsapp_notifier.py  # WA / SMS / SMTP + per-channel CB
│
├── tests/
│   ├── conftest.py               # Cross-module DB + secret isolation
│   ├── test_enterprise_controls.py
│   ├── test_ev_engine.py
│   ├── test_llm_eval.py
│   └── test_enterprise_flow.py
│
├── terraform/                    # AWS EKS Fargate, Aurora PG, ElastiCache
├── .github/workflows/deploy.yml  # 8-stage CI/CD (ruff→Bandit→tests→Docker→k8s)
├── docker-compose.yml            # 4 profiles: dev, chaos, monitoring
└── monitoring/                   # Prometheus + Grafana config
```

---

## 🔐 Security Model

| Control | Implementation |
|---------|---------------|
| Webhook authentication | HMAC-SHA256 on every inbound event, constant-time `hmac.compare_digest` |
| PII redaction | Regex + field-name walk strips email / phone / card before any DB write or LLM call |
| Audit ledger | SHA-256 hash-chain **+** HMAC-SHA256 per row; `verify_audit_integrity_detailed()` returns exact tampered `log_id`s |
| Column encryption | AES-256-GCM with HKDF-SHA256 key derivation; graceful `b64only:` fallback |
| Discount guardrail | LLM discount hard-capped at 15 % in schema validator **and** pipeline outer check |
| EV gate | Negative-EV actions bypassed; counterfactuals recorded in shadow ledger |
| HITL gate | Transactions > ₹50k or ML score 0.40–0.60 held for human review |
| JWT RBAC | HS256 · 3 roles (Admin / Operator / Auditor) · `Principal` · `authorize()` |
| Tenant isolation | `merchant_id` on all tables; PostgreSQL RLS enforces row-level access |

> **Production notice:** These controls require deployment-specific review, secret management, monitoring, and operational testing before any production use. Never commit credentials or populate production secrets in the demo dashboard.

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Dashboard** | Streamlit 1.40+, Plotly |
| **API** | FastAPI, Uvicorn, asyncio — 202 ACK < 15 ms |
| **Stateful Agent** | `agent_graph.py` — custom cyclic graph (LangGraph-compatible) |
| **Bandit** | Thompson Sampling β(α,β) · LinUCB · ε-greedy |
| **Queue** | asyncio.Queue (dev) · Celery + Redis · Kafka / Redpanda (aiokafka) |
| **ML** | LightGBM, scikit-learn, scipy (KS two-sample + PSI drift) |
| **Database** | SQLite WAL (dev / Streamlit Cloud) · PostgreSQL Aurora Serverless v2 (prod) |
| **Security** | HMAC-SHA256, AES-256-GCM, JWT HS256, PII redaction |
| **EV Engine** | Pure `Decimal` arithmetic, PROCEED / BYPASS / SHADOW modes |
| **Observability** | Prometheus `/metrics`, OpenTelemetry (9 node spans + token count, latency, prompt version) |
| **IaC** | Terraform — AWS EKS Fargate, Aurora PG, ElastiCache Redis, Secrets Manager |
| **CI/CD** | GitHub Actions: ruff → Bandit SAST → 132 tests → Docker → Terraform → kubectl |

---

<div align="center">

Built with ❤️ for the **Razorpay AI Buildathon · Track 03**

[Live Demo](https://recoverai-enterprise.streamlit.app) · [GitHub](https://github.com/SumedhPatil1507/RecoverAI)

</div>
