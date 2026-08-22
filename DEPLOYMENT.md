# RecoverAI — Deployment Guide

This guide covers every step from a blank machine to a live deployment on:
- **GitHub** (source control + CI/CD)
- **Streamlit Cloud** (dashboard)
- **Render** (API + dashboard, free tier)
- **Railway** (API)
- **AWS App Runner** (API via Docker)
- **Azure App Service** (API + dashboard)
- **Docker / Self-hosted** (any VPS, EC2, DigitalOcean)

---

## Repo Structure (what GitHub will hold)

```
RecoverAI/                     ← git repo root
├── recover_ai/                ← application package
│   ├── __init__.py
│   ├── config.py
│   ├── security.py
│   ├── database.py
│   ├── schemas.py
│   ├── agent_engine.py
│   ├── main.py                ← FastAPI app
│   ├── app.py                 ← Streamlit dashboard
│   ├── data_simulator.py
│   └── requirements.txt
├── streamlit_app.py           ← Streamlit Cloud entry point (repo root)
├── requirements.txt           ← root-level copy (required by cloud platforms)
├── Dockerfile
├── docker-compose.yml
├── Procfile                   ← Heroku / Railway / Render
├── render.yaml                ← Render blueprint
├── railway.toml               ← Railway config
├── azure/webapp.bicep         ← Azure Bicep IaC
├── aws/apprunner.yaml         ← AWS App Runner config
├── .streamlit/config.toml     ← Streamlit theme + server settings
├── .streamlit/secrets.toml.example
├── .github/workflows/deploy.yml
├── .gitignore
├── .env.example
└── DEPLOYMENT.md              ← this file
```

---

## Step 1 — Create the GitHub Repository

### Option A: GitHub CLI (fastest)

```bash
# Install GitHub CLI if needed: https://cli.github.com/
gh auth login

cd C:\Users\Sumedh\projects\RecoverAI   # your project root
git init
git add .
git commit -m "feat: initial RecoverAI system"

gh repo create RecoverAI \
  --public \
  --description "Agentic Payment Degradation & Abandonment Engine – Razorpay AI Buildathon Track 03" \
  --push \
  --source .
```

### Option B: GitHub Web UI

1. Go to **github.com → New repository**
2. Name: `RecoverAI`, set to Public, **do NOT** tick "Add README" (we have one)
3. Copy the remote URL, then:

```bash
cd C:\Users\Sumedh\projects\RecoverAI
git init
git add .
git commit -m "feat: initial RecoverAI system"
git remote add origin https://github.com/YOUR_USERNAME/RecoverAI.git
git branch -M main
git push -u origin main
```

### Add GitHub Secrets (for CI/CD)

Go to **Repo → Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name | Value |
|---|---|
| `RAZORPAY_WEBHOOK_SECRET` | your webhook signing secret |
| `OPENAI_API_KEY` | your OpenAI key (or leave empty) |
| `DOCKERHUB_USERNAME` | your Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |

---

## Step 2 — Deploy Dashboard on Streamlit Cloud (Free)

> Streamlit Cloud hosts the **dashboard only**. The FastAPI API needs a separate host (Render, Railway, etc.).

1. Go to **share.streamlit.io** → Sign in with GitHub
2. Click **New app**
3. Fill in:
   - **Repository**: `YOUR_USERNAME/RecoverAI`
   - **Branch**: `main`
   - **Main file path**: `streamlit_app.py`   ← root-level entry point
4. Click **Advanced settings → Secrets** and paste:

```toml
RAZORPAY_WEBHOOK_SECRET = "your_secret"
OPENAI_API_KEY          = ""
DATABASE_PATH           = "recover_ai.db"
WEBHOOK_BASE_URL        = "https://your-api-on-render.onrender.com"
```

5. Click **Deploy** — live in ~2 minutes.

> **Note:** Streamlit Cloud uses an ephemeral filesystem. The SQLite DB resets on each redeploy. For persistence, swap SQLite for a hosted PostgreSQL (Supabase free tier works well).

---

## Step 3 — Deploy API on Render (Free Tier)

1. Go to **render.com** → New → **Blueprint**
2. Connect your GitHub repo `RecoverAI`
3. Render auto-detects `render.yaml` and creates both services
4. Set the **environment variables** in the Render dashboard:
   - `RAZORPAY_WEBHOOK_SECRET`
   - `OPENAI_API_KEY` (optional)
5. Click **Apply** — both services deploy automatically.

Your API will be live at: `https://recoverai-api.onrender.com`

Update `WEBHOOK_BASE_URL` in Streamlit Cloud secrets to this URL.

---

## Step 4 — Deploy API on Railway

1. Go to **railway.app** → New Project → **Deploy from GitHub repo**
2. Select `RecoverAI`
3. Railway reads `railway.toml` automatically
4. Add environment variables in the Railway dashboard:
   - `RAZORPAY_WEBHOOK_SECRET`
   - `OPENAI_API_KEY`
   - `PORT` is injected automatically
5. Click **Deploy**

---

## Step 5 — Deploy on AWS (App Runner)

### Prerequisites
```bash
aws configure   # set your AWS credentials
```

### Push image to ECR

```bash
# Create ECR repo
aws ecr create-repository --repository-name recoverai --region us-east-1

# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin \
    YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t recoverai .
docker tag recoverai:latest \
  YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/recoverai:latest
docker push \
  YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/recoverai:latest
```

### Store secrets in AWS Secrets Manager

```bash
aws secretsmanager create-secret \
  --name recoverai/webhook-secret \
  --secret-string "your_webhook_secret"

aws secretsmanager create-secret \
  --name recoverai/openai-key \
  --secret-string "your_openai_key"
```

### Deploy via App Runner

```bash
# Update aws/apprunner.yaml with your ECR image URI, then:
aws apprunner create-service \
  --cli-input-yaml file://aws/apprunner.yaml \
  --region us-east-1
```

### Deploy Streamlit on AWS EC2 (alongside API)

```bash
# SSH into your EC2 instance
ssh -i your-key.pem ec2-user@YOUR_EC2_IP

# Install dependencies
sudo yum update -y
sudo yum install python3-pip git -y
git clone https://github.com/YOUR_USERNAME/RecoverAI.git
cd RecoverAI
pip3 install -r requirements.txt

# Run with systemd (see below) or tmux
streamlit run streamlit_app.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true
```

Open port 8501 in your EC2 Security Group inbound rules.

---

## Step 6 — Deploy on Azure App Service

### Prerequisites
```bash
az login
az group create --name RecoverAI --location eastus
```

### Deploy via Bicep

```bash
az deployment group create \
  --resource-group RecoverAI \
  --template-file azure/webapp.bicep \
  --parameters razorpayWebhookSecret="your_secret" openaiApiKey="your_key"
```

### Or deploy directly via CLI (no Bicep)

```bash
# Create App Service plan
az appservice plan create \
  --name recoverai-plan \
  --resource-group RecoverAI \
  --sku B1 \
  --is-linux

# Deploy FastAPI
az webapp create \
  --resource-group RecoverAI \
  --plan recoverai-plan \
  --name recoverai-api \
  --runtime "PYTHON:3.11" \
  --startup-file "uvicorn recover_ai.main:app --host 0.0.0.0 --port 8000"

# Deploy Streamlit
az webapp create \
  --resource-group RecoverAI \
  --plan recoverai-plan \
  --name recoverai-dashboard \
  --runtime "PYTHON:3.11" \
  --startup-file "streamlit run streamlit_app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true"

# Set secrets
az webapp config appsettings set \
  --resource-group RecoverAI \
  --name recoverai-api \
  --settings \
    RAZORPAY_WEBHOOK_SECRET="your_secret" \
    OPENAI_API_KEY="your_key"

# Deploy code
az webapp up --name recoverai-api --resource-group RecoverAI
```

---

## Step 7 — Docker / Self-Hosted (VPS, DigitalOcean, EC2)

```bash
# Clone repo on your server
git clone https://github.com/YOUR_USERNAME/RecoverAI.git
cd RecoverAI

# Copy and fill in env vars
cp .env.example .env
nano .env   # set RAZORPAY_WEBHOOK_SECRET etc.

# Build and start all services
docker compose up --build -d

# With simulator for testing
docker compose --profile dev up --build -d

# View logs
docker compose logs -f api
docker compose logs -f dashboard

# Stop
docker compose down
```

Services will be available at:
- API: `http://YOUR_SERVER_IP:8000`
- Dashboard: `http://YOUR_SERVER_IP:8501`

---

## Environment Variables Reference

| Variable | Required | Description | Example |
|---|---|---|---|
| `RAZORPAY_WEBHOOK_SECRET` | **Yes** | HMAC signing secret | `whsec_abc123…` |
| `OPENAI_API_KEY` | No | Enables LLM path; blank = rule engine | `sk-…` |
| `DATABASE_PATH` | No | SQLite file path | `recover_ai.db` |
| `LLM_MODEL` | No | OpenAI model name | `gpt-4o-mini` |
| `LLM_TIMEOUT_SECONDS` | No | Hard timeout for LLM | `3.0` |
| `MAX_RECOVERY_ATTEMPTS` | No | Business rule cap | `2` |
| `WEBHOOK_BASE_URL` | No | Used by simulator | `http://127.0.0.1:8000` |
| `SIMULATOR_INTERVAL_SECONDS` | No | Event frequency | `5.0` |
| `DASHBOARD_REFRESH_SECONDS` | No | Auto-refresh rate | `5` |

---

## Platform Comparison

| Platform | FastAPI API | Streamlit Dashboard | Free Tier | Best For |
|---|---|---|---|---|
| **Streamlit Cloud** | ✗ | ✓ | ✓ | Dashboard hosting only |
| **Render** | ✓ | ✓ | ✓ (sleeps) | Full stack, quick setup |
| **Railway** | ✓ | ✓ | ✓ ($5 credit) | Dev / staging |
| **AWS App Runner** | ✓ | Via EC2 | ✗ | Production scale |
| **Azure App Service** | ✓ | ✓ | ✗ (F1 is free) | Enterprise / Azure shop |
| **Docker (self-hosted)** | ✓ | ✓ | ✓ | Full control, any VPS |

---

## Recommended Setup for the Buildathon Demo

1. **GitHub** — push code (takes 2 minutes)
2. **Render** — deploy API (Blueprint auto-reads `render.yaml`)
3. **Streamlit Cloud** — deploy dashboard (point to `streamlit_app.py`)
4. Update `WEBHOOK_BASE_URL` in Streamlit secrets to the Render API URL
5. Run `python recover_ai/data_simulator.py` locally to seed data

Total setup time: ~15 minutes, zero cost.
