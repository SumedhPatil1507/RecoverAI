"""
RecoverAI Enterprise – Configuration
All settings are env-driven via pydantic-settings.
Secrets are NEVER hard-coded; defaults are safe for local dev only.
"""
from __future__ import annotations

import os
from functools import lru_cache

# ── Pydantic v1 / v2 compatibility ───────────────────────────────────────────
# Streamlit Cloud may resolve an older pydantic; support both.
try:
    from pydantic import Field, field_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _PYDANTIC_V2 = True
except ImportError:
    from pydantic import Field, validator as field_validator  # type: ignore[assignment]
    from pydantic import BaseSettings  # type: ignore[no-redef]
    SettingsConfigDict = None  # type: ignore[assignment,misc]
    _PYDANTIC_V2 = False


def _inject_streamlit_secrets() -> None:
    """
    Streamlit Cloud injects secrets via st.secrets, not os.environ.
    Copy them into os.environ so pydantic-settings picks them up
    transparently across all modules.
    """
    try:
        import streamlit as st  # noqa: PLC0415
        for k, v in st.secrets.items():
            if isinstance(v, str) and k.upper() not in os.environ:
                os.environ[k.upper()] = v
    except Exception:
        pass


_inject_streamlit_secrets()


if _PYDANTIC_V2:
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )

        # ── Identity ──────────────────────────────────────────────────────────
        app_name: str = "RecoverAI Enterprise"
        app_version: str = "2.0.0"
        environment: str = "development"

        # ── Security ──────────────────────────────────────────────────────────
        razorpay_webhook_secret: str = Field(
            default="dev_secret_replace_in_production",
            description="HMAC-SHA256 secret from Razorpay dashboard",
        )

        # ── Database ──────────────────────────────────────────────────────────
        # Use platform-appropriate temp directory (Windows: %TEMP%, Linux/Mac: /tmp)
        database_path: str = "recover_ai_enterprise.db"

        # ── LLM / AI ──────────────────────────────────────────────────────────
        openai_api_key: str = Field(default="", description="Leave blank → rule engine only")
        llm_model: str = "gpt-4o-mini"
        llm_timeout_seconds: float = 3.0
        llm_max_tokens: int = 300

        # ── ML Scorer ─────────────────────────────────────────────────────────
        ml_model_path: str = "recover_ai_lgbm.pkl"
        ml_low_priority_threshold: float = 0.15

        # ── Business Rules ────────────────────────────────────────────────────
        max_recovery_attempts: int = 2
        max_discount_pct: float = 15.0
        recovery_window_hours: int = 24

        # ── Queue ─────────────────────────────────────────────────────────────
        queue_max_size: int = 10_000
        queue_workers: int = 4

        # ── Simulator ─────────────────────────────────────────────────────────
        simulator_interval_seconds: float = 5.0
        webhook_base_url: str = "http://127.0.0.1:8000"
        min_transaction_amount_paise: int = 50_000     # ₹500
        max_transaction_amount_paise: int = 1_500_000  # ₹15,000

        # ── Dashboard ─────────────────────────────────────────────────────────
        dashboard_refresh_seconds: int = 10

        @field_validator("environment", mode="before")
        @classmethod
        def lowercase_env(cls, v: str) -> str:
            return v.lower()

        @property
        def is_production(self) -> bool:
            return self.environment == "production"

else:
    # ── pydantic v1 fallback ──────────────────────────────────────────────────
    class Settings(BaseSettings):  # type: ignore[no-redef]
        app_name: str = "RecoverAI Enterprise"
        app_version: str = "2.0.0"
        environment: str = "development"
        razorpay_webhook_secret: str = "dev_secret_replace_in_production"
        database_path: str = "recover_ai_enterprise.db"
        openai_api_key: str = ""
        llm_model: str = "gpt-4o-mini"
        llm_timeout_seconds: float = 3.0
        llm_max_tokens: int = 300
        ml_model_path: str = "recover_ai_lgbm.pkl"
        ml_low_priority_threshold: float = 0.15
        max_recovery_attempts: int = 2
        max_discount_pct: float = 15.0
        recovery_window_hours: int = 24
        queue_max_size: int = 10_000
        queue_workers: int = 4
        simulator_interval_seconds: float = 5.0
        webhook_base_url: str = "http://127.0.0.1:8000"
        min_transaction_amount_paise: int = 50_000
        max_transaction_amount_paise: int = 1_500_000
        dashboard_refresh_seconds: int = 10

        @field_validator("environment", pre=True)
        @classmethod
        def lowercase_env(cls, v: str) -> str:
            return v.lower()

        @property
        def is_production(self) -> bool:
            return self.environment == "production"

        class Config:
            env_file = ".env"
            case_sensitive = False
            extra = "ignore"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
