# RecoverAI Enterprise — Deployment Guide

Complete step-by-step instructions for every supported platform.

---

## Repo Structure (what GitHub holds)

```
RecoverAI/
├── recover_ai/          application package (all Python source)
├── streamlit_app.py     Streamlit Cloud entry point  ← IMPORTANT
├── requirements.txt     root-level deps (used by all cloud platforms)
├── Dockerfile           multi-stage build
├── docker-compose.yml   local / VPS deployment
├── Procfile             Render / Railway
├── render.yaml          Render Blueprint
├── railway.toml         Railway config
├── .env.example         env var template
├── .streamlit/          Streamlit config + secrets template
├── .github/workflows/   CI/CD pipeline
├── aws/                 AWS App Runner config
└── azure/               Azure Bicep IaC
```

---

## Step 1 — Push to GitHub

### Option A: GitHub CLI

```bash
cd C:\Users\Sumedh\projects\RecoverAI

gh auth login
gh repo create RecoverAI \
  --public \
  --description "Agentic Payment Degradation & Revenue Recovery Engine – Razorpay AI Buildathon Track 03" \
  --push \
  --source .
```

### Option B: Git + GitHub Web UI

1. Create a new repo at github.com (no README, no .gitignore)
2. Then:

```bash
cd C:\Users\Sumedh\projects\RecoverAI
git remote add origin https://github.com/YOUR_USERNAME/RecoverAI.git
git branch -M main
git push -u origin main
```

### Add GitHub Actions Secrets

Go to **Repo → Settings → Secrets and variables → Actions**:

| Secret | Value |
|--------|-------|
| `RAZORPAY_WEBHOOK_SECRET` | your webhook secret |
| `OPENAI_API_KEY` | your OpenAI key (optional) |
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |

---

## Step 2 — Streamlit Cloud (Dashboard — Free)

> Streamlit Cloud hosts the **dashboard only**.
> The FastAPI API must run on a separate host (Render, Railway, etc.).

1. Go to **[share.streamlit.io](https://share.streamlit.io)** → Sign in with GitHub
2. Click **New app**
3. Fill in:
   - **Repository:** `YOUR_USERNAME/RecoverAI`
   - **Branch:** `main`
   - **Main file path:** `streamlit_app.py`
4. Click **Advanced settings → Secrets** and paste:

```toml
RAZORPAY_WEBHOOK_SECRET = "your_secret"
OPENAI_API_KEY          = ""
DATABASE_PATH           = "recover_ai_enterprise.db"
ML_MODEL_PATH           = "recover_ai_lgbm.pkl"
ENVIRONMENT             = "production"
WEBHOOK_BASE_URL        = "https://YOUR-API.onrender.com"
```

5. Click **Deploy** — live in ~3 minutes.

> **Note on persistence:** Streamlit Cloud uses an ephemeral filesystem.
> The SQLite DB resets on redeploy. For production persistence, swap SQLite
> for a hosted PostgreSQL (Supabase free tier recommended).

---

## Step 3 — Render (API + Dashboard — Free Tier)

### Via Blueprint (automatic — recommended)

1. Go to **[render.com](https://render.com)** → New → **Blueprint**
2. Connect your GitHub repo `RecoverAI`
3. Render reads `render.yaml` and creates both services automatically
4. Set environment variables in the Render dashboard:
   - `RAZORPAY_WEBHOOK_SECRET`
   - `OPENAI_API_KEY` (optional)
5. Click **Apply**

API URL: `https://recoverai-api-XXXX.onrender.com`
Update `WEBHOOK_BASE_URL` in your Streamlit Cloud secrets to this URL.

### Manual Render Deploy

```bash
# In render.com dashboard: New Web Service → GitHub → RecoverAI
# Build Command:  pip install -r requirements.txt
# Start Command:  uvicorn recover_ai.main:app --host 0.0.0.0 --port $PORT
```

---

## Step 4 — Railway

1. Go to **[railway.app](https://railway.app)** → New Project → **Deploy from GitHub**
2. Select `RecoverAI`
3. Railway reads `railway.toml` automatically
4. Add environment variables:
   - `RAZORPAY_WEBHOOK_SECRET`
   - `OPENAI_API_KEY` (optional)
5. Deploy

---

## Step 5 — AWS App Runner

### Build and push Docker image to ECR

```bash
# Create ECR repository
aws ecr create-repository --repository-name recoverai --region us-east-1

# Authenticate
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
  --name prod/recoverai/webhook-secret \
  --secret-string "your_webhook_secret"
```

### Deploy App Runner

Edit `aws/apprunner.yaml` to set your ECR image URI, then:

```bash
aws apprunner create-service \
  --cli-input-yaml file://aws/apprunner.yaml \
  --region us-east-1
```

### Streamlit on EC2

```bash
# SSH into EC2 instance
git clone https://github.com/YOUR_USERNAME/RecoverAI.git
cd RecoverAI
pip3 install -r requirements.txt
streamlit run streamlit_app.py \
  --server.port 8501 --server.address 0.0.0.0 --server.headless true
```

Open port 8501 in EC2 Security Group inbound rules.

---

## Step 6 — Azure App Service

### Prerequisites

```bash
az login
az group create --name RecoverAI --location eastus
```

### Deploy via Bicep (IaC)

```bash
az deployment group create \
  --resource-group RecoverAI \
  --template-file azure/webapp.bicep \
  --parameters \
    razorpayWebhookSecret="your_secret" \
    openaiApiKey="your_key"
```

### Or deploy via CLI

```bash
az appservice plan create \
  --name recoverai-plan --resource-group RecoverAI \
  --sku B1 --is-linux

# FastAPI
az webapp create \
  --resource-group RecoverAI --plan recoverai-plan \
  --name recoverai-api --runtime "PYTHON:3.11" \
  --startup-file "uvicorn recover_ai.main:app --host 0.0.0.0 --port 8000"

# Streamlit
az webapp create \
  --resource-group RecoverAI --plan recoverai-plan \
  --name recoverai-dash --runtime "PYTHON:3.11" \
  --startup-file "streamlit run streamlit_app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true"

# Set secrets
az webapp config appsettings set \
  --resource-group RecoverAI --name recoverai-api \
  --settings RAZORPAY_WEBHOOK_SECRET="your_secret" OPENAI_API_KEY=""

# Deploy code
az webapp up --name recoverai-api --resource-group RecoverAI
```

---

## Step 7 — Docker / Self-Hosted VPS

```bash
git clone https://github.com/YOUR_USERNAME/RecoverAI.git
cd RecoverAI
cp .env.example .env
# Edit .env — set RAZORPAY_WEBHOOK_SECRET

# Production (API + dashboard)
docker compose up --build -d

# Development (adds simulator)
docker compose --profile dev up --build -d

# Monitor
docker compose logs -f
docker compose ps
```

Services:
- API: `http://YOUR_IP:8000`
- Dashboard: `http://YOUR_IP:8501`

---

## Platform Quick-Reference

| Platform | API | Dashboard | Free | Best For |
|----------|-----|-----------|------|----------|
| Streamlit Cloud | ✗ | ✓ | ✓ | Dashboard hosting |
| Render | ✓ | ✓ | ✓ (sleeps) | Full stack, quick |
| Railway | ✓ | ✓ | ✓ ($5 credit) | Dev / staging |
| AWS App Runner | ✓ | Via EC2 | ✗ | Production scale |
| Azure App Service | ✓ | ✓ | ✗ (F1 free) | Enterprise / Azure |
| Docker (VPS) | ✓ | ✓ | ✓ | Full control |

---

## Recommended Buildathon Demo Setup (~15 min, zero cost)

```
1. git push → GitHub                  (2 min)
2. Render Blueprint → API             (5 min, reads render.yaml automatically)
3. Streamlit Cloud → Dashboard        (3 min, point to streamlit_app.py)
4. Update WEBHOOK_BASE_URL secret     (1 min)
5. python recover_ai/data_simulator.py --burst 30   (seeds dashboard with data)
```

---

## Testing the Webhook Manually

```bash
# On Linux/macOS:
BODY='{"entity":"event","event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_manual01","order_id":"order_manual01","amount":500000,"currency":"INR","error_code":"GATEWAY_ERROR","error_description":"Bank timeout"}}}}'
SECRET="dev_secret_replace_in_production"
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')

curl -X POST https://YOUR-API-URL/webhook/razorpay \
  -H "Content-Type: application/json" \
  -H "X-Razorpay-Signature: $SIG" \
  -d "$BODY"
```

Expected:
```json
{"status":"ok","message":"Payment pay_manual01 queued for recovery analysis.","payment_id":"pay_manual01"}
```
