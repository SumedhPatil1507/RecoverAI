"""RecoverAI configuration with fail-closed production contracts."""
from __future__ import annotations

import os
from functools import lru_cache

try:
    from pydantic import Field, field_validator, model_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _V2 = True
except ImportError:
    from pydantic import Field, validator as field_validator  # type: ignore
    from pydantic import BaseSettings  # type: ignore
    SettingsConfigDict = None  # type: ignore
    _V2 = False


def _inject_streamlit_secrets() -> None:
    try:
        import streamlit as st
        for key, value in st.secrets.items():
            if isinstance(value, str):
                os.environ.setdefault(key.upper(), value)
    except Exception:
        pass


_inject_streamlit_secrets()


if _V2:
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

        app_name: str = "RecoverAI Enterprise"
        app_version: str = "3.0.0"
        environment: str = "development"
        razorpay_webhook_secret: str = Field(default="dev_secret_replace_in_production")
        audit_hmac_key: str = ""
        column_encryption_key: str = ""
        jwt_secret: str = ""
        database_url: str = ""
        database_path: str = "/tmp/recover_ai_enterprise.db"
        execution_mode: str = "SHADOW"
        use_celery: bool = False
        redis_url: str = "redis://localhost:6379/0"
        tenant_api_keys: str = ""
        openai_api_key: str = ""
        llm_model: str = "gpt-4o-mini"
        llm_timeout_seconds: float = 3.0
        llm_max_tokens: int = 300
        ml_model_path: str = "recover_ai_lgbm.pkl"
        ml_low_priority_threshold: float = 0.15
        max_recovery_attempts: int = 2
        max_discount_pct: float = 15.0
        recovery_window_hours: int = 24
        operational_fee_paise: int = 0
        gateway_cost_paise: int = 0
        queue_max_size: int = 10_000
        queue_workers: int = 4
        simulator_interval_seconds: float = 5.0
        webhook_base_url: str = "http://127.0.0.1:8000"
        min_transaction_amount_paise: int = 50_000
        max_transaction_amount_paise: int = 1_500_000
        dashboard_refresh_seconds: int = 60

        @field_validator("environment", "execution_mode", mode="before")
        @classmethod
        def normalize(cls, value: str) -> str:
            return str(value).lower() if str(value).lower() == "production" else str(value).upper() if str(value).upper() in {"SHADOW", "LIVE"} else str(value).lower()

        @model_validator(mode="after")
        def validate_production(self) -> "Settings":
            if self.environment == "production":
                if not self.database_url.startswith(("postgresql://", "postgres://")):
                    raise ValueError("Production requires DATABASE_URL using PostgreSQL")
                if not self.use_celery:
                    raise ValueError("Production requires USE_CELERY=1 and a distributed queue")
                if not self.jwt_secret or self.jwt_secret.startswith("dev_"):
                    raise ValueError("Production requires JWT_SECRET")
                if not self.audit_hmac_key or not self.column_encryption_key:
                    raise ValueError("Production requires audit and column-encryption keys")
            if self.execution_mode not in {"SHADOW", "LIVE"}:
                raise ValueError("EXECUTION_MODE must be SHADOW or LIVE")
            return self

        @property
        def is_production(self) -> bool:
            return self.environment == "production"

else:
    class Settings(BaseSettings):  # type: ignore[no-redef]
        app_name: str = "RecoverAI Enterprise"; app_version: str = "3.0.0"; environment: str = "development"
        razorpay_webhook_secret: str = "dev_secret_replace_in_production"; audit_hmac_key: str = ""; column_encryption_key: str = ""; jwt_secret: str = ""
        database_url: str = ""; database_path: str = "/tmp/recover_ai_enterprise.db"; execution_mode: str = "SHADOW"; use_celery: bool = False; redis_url: str = "redis://localhost:6379/0"; tenant_api_keys: str = ""
        openai_api_key: str = ""; llm_model: str = "gpt-4o-mini"; llm_timeout_seconds: float = 3.0; llm_max_tokens: int = 300; ml_model_path: str = "recover_ai_lgbm.pkl"; ml_low_priority_threshold: float = 0.15
        max_recovery_attempts: int = 2; max_discount_pct: float = 15.0; recovery_window_hours: int = 24; operational_fee_paise: int = 0; gateway_cost_paise: int = 0
        queue_max_size: int = 10_000; queue_workers: int = 4; simulator_interval_seconds: float = 5.0; webhook_base_url: str = "http://127.0.0.1:8000"; min_transaction_amount_paise: int = 50_000; max_transaction_amount_paise: int = 1_500_000; dashboard_refresh_seconds: int = 60
        @field_validator("environment", pre=True)
        @classmethod
        def lowercase_env(cls, value: str) -> str: return str(value).lower()
        @property
        def is_production(self) -> bool: return self.environment == "production"
        class Config:
            env_file = ".env"; case_sensitive = False; extra = "ignore"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
