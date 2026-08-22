"""
RecoverAI Configuration
Pydantic BaseSettings for all environment-driven config values.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from functools import lru_cache
import os


def _inject_streamlit_secrets() -> None:
    """
    On Streamlit Cloud, secrets are in st.secrets (not os.environ).
    This function copies them into os.environ so pydantic-settings picks them up
    transparently, without any Streamlit dependency in non-dashboard modules.
    """
    try:
        import streamlit as st  # noqa: PLC0415
        for key, value in st.secrets.items():
            if isinstance(value, str) and key not in os.environ:
                os.environ[key.upper()] = value
    except Exception:
        pass  # Not running inside Streamlit – no-op


_inject_streamlit_secrets()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = "RecoverAI"
    app_version: str = "1.0.0"
    debug: bool = False

    # ── Webhook Security ───────────────────────────────────────────────────
    razorpay_webhook_secret: str = Field(
        default="dev_webhook_secret_change_in_production",
        description="HMAC-SHA256 signing secret from Razorpay dashboard",
    )

    # ── Database ───────────────────────────────────────────────────────────
    database_path: str = "recover_ai.db"

    # ── AI / LLM ───────────────────────────────────────────────────────────
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key (optional – fallback rule engine activates if blank)",
    )
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 3.0          # Hard SLA – fall back if exceeded
    llm_max_tokens: int = 256

    # ── Recovery Business Rules ────────────────────────────────────────────
    max_recovery_attempts: int = 2
    recovery_window_hours: int = 24           # Transactions older than this are EXPIRED

    # ── Simulator ─────────────────────────────────────────────────────────
    simulator_interval_seconds: float = 5.0
    webhook_base_url: str = "http://127.0.0.1:8000"
    min_transaction_amount: float = 500.0
    max_transaction_amount: float = 15000.0

    # ── Dashboard ─────────────────────────────────────────────────────────
    dashboard_refresh_seconds: int = 5


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
