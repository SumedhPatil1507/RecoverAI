# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a prefix directory (keeps final image small)
COPY recover_ai/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY recover_ai/ ./recover_ai/
COPY .streamlit/ ./.streamlit/

# Create a non-root user for security
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

# Expose ports for both services
# 8000 = FastAPI (uvicorn)  |  8501 = Streamlit
EXPOSE 8000 8501

# Default: run the FastAPI server
# Override CMD in docker-compose to run Streamlit
CMD ["uvicorn", "recover_ai.main:app", "--host", "0.0.0.0", "--port", "8000"]
