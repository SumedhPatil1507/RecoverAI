# RecoverAI Enterprise

> Agentic payment-recovery platform with an interactive Streamlit demo and a separately runnable FastAPI service.

[![Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://recoverai-enterprise.streamlit.app)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

RecoverAI demonstrates an end-to-end failed-payment recovery workflow: synthetic transaction data, recovery scoring and expected-value guardrails, approval queues, experimentation, and an integrity-checked audit ledger. The repository also contains a FastAPI webhook/API service and optional external integration modules.

> **Demo safety:** `streamlit_app.py` intentionally runs in staging/shadow mode against a temporary SQLite database. The dashboard does not make live payment or notification calls. It is suitable for demos and evaluation, not as a production payment-recovery control plane. The `/tmp` database is ephemeral and may be reset when a hosted app restarts.

## Streamlit dashboard

The dashboard includes eight tabs: Intelligence Hub, Payment Links, Dispatch, HITL Approvals, A/B Testing, Chaos Simulator, Merchants, and EV Engine. Interactive operations in the dashboard are demo/simulation flows; credentials for Razorpay, WhatsApp, Twilio, SMTP, or OpenAI are **not required** to run it.

### Run locally

Python 3.11 or newer is recommended.

```bash
git clone https://github.com/SumedhPatil1507/RecoverAI.git
cd RecoverAI
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open the local URL printed by Streamlit (normally `http://localhost:8501`). On first launch the dashboard seeds synthetic data into `/tmp/recover_ai_enterprise.db`. Use the **Seed Demo Data** control in the sidebar to add another batch.

### Deploy on Streamlit Community Cloud

1. In [Streamlit Community Cloud](https://share.streamlit.io/), create an app for `SumedhPatil1507/RecoverAI`, branch `main`, with `streamlit_app.py` as the main file.
2. Keep `requirements.txt` at the repository root so the hosted app installs the project's dependencies.
3. Deploy. No Streamlit secrets are required for the staging/shadow demo dashboard.

The dashboard forces `ENVIRONMENT=staging`, `EXECUTION_MODE=SHADOW`, and `DATABASE_PATH=/tmp/recover_ai_enterprise.db` at startup, and disables the distributed queue and database URL for this app process. These safeguards keep the dashboard independent of production API secrets and writable repository storage. Streamlit-hosted local storage is temporary; use the separately configured API and a managed database for persistent deployments.

### Run the API separately (optional)

The FastAPI service is a separate process and has its own configuration and deployment requirements. For local development:

```bash
python -m pip install -r requirements.txt
uvicorn recover_ai.main:app --host 0.0.0.0 --port 8000
```

The API's live integrations and production settings must be configured independently. Do not reuse a demo configuration for real payment processing.

## Expected-value guardrail

The recovery pipeline's unit-economic decision is:

```text
EV = (P(recovery) × recoverable amount) − (operational fee + gateway cost)
```

An action with non-positive expected value is bypassed. In shadow mode, dispatch actions are intercepted and counterfactuals can be recorded instead of sending a payment link or notification. Related API settings include `EV_MINIMUM_RUPEES`, `EV_OPERATIONAL_FEE`, `EV_GATEWAY_COST_PCT`, and `EXECUTION_MODE`; the Streamlit dashboard itself pins shadow mode.

## Tests

Install the development dependencies and run the full test suite:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

Tests cover the EV engine, access-control and tenant boundaries, cryptographic protections, audit integrity, HITL workflow, circuit breakers, drift detection, webhook behavior, and the Streamlit entrypoint.

## Docker

```bash
docker compose up api dashboard
```

See `docker-compose.yml`, `DEPLOYMENT.md`, and the infrastructure configuration for optional simulator, chaos, monitoring, and cloud deployment profiles. The Docker dashboard service also uses the Streamlit entrypoint.

## Project layout

```text
RecoverAI/
├── streamlit_app.py           # Streamlit demo dashboard
├── requirements.txt           # Runtime dependencies for dashboard and API
├── recover_ai/
│   ├── main.py                # FastAPI application
│   ├── ev_engine.py           # Expected-value decisions and shadow behavior
│   ├── auth.py                # JWT authentication and role checks
│   ├── database.py            # SQLite persistence and audit ledger
│   ├── db_postgres.py         # Optional PostgreSQL backend
│   ├── agent_engine.py        # Recovery decision pipeline
│   ├── ml_scorer.py           # Recovery scoring and drift checks
│   ├── security.py            # PII handling, HMAC, and encryption helpers
│   └── integrations/          # Optional payment-link and notification clients
├── tests/                     # Unit, integration, and dashboard smoke tests
├── docs/                      # Requirements, design, and implementation notes
└── terraform/                 # Infrastructure-as-code examples
```

## Security and production considerations

The codebase includes webhook signature verification, PII redaction, an HMAC/hash-chain audit ledger, encryption helpers, JWT role checks, and tenant-aware data access. These controls still require deployment-specific review, secret management, monitoring, and operational testing before any production use. Never commit credentials or populate production secrets in the demo dashboard.

## Acknowledgments

Razorpay AI Buildathon · Track 03
