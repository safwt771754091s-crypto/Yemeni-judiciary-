"""Application configuration (pydantic-settings, 12-factor)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- app ---
    app_name: str = "Legal AI Platform - Yemen"
    app_env: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # --- database ---
    supabase_db_url: str | None = None
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None
    db_min_pool: int = 1
    db_max_pool: int = 10
    db_command_timeout: float = 60.0

    # --- embeddings ---
    embedding_provider: Literal["hash", "openai"] = "hash"
    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 1536
    embedding_batch_size: int = 64

    # --- llm ---
    llm_provider: Literal["mock", "openai", "anthropic", "openai_compatible"] = "mock"
    llm_model: str = "gpt-4o"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 120.0

    # --- retrieval ---
    retrieval_top_k: int = 8
    retrieval_candidates: int = 40
    vector_weight: float = 0.65
    keyword_weight: float = 0.35
    rrf_k: int = 60

    # --- legal defaults ---
    jurisdiction: str = "الجمهورية اليمنية"
    default_court_name: str = "المحكمة الابتدائية"
    default_case_type: str = "مدني"

    # --- n8n ---
    n8n_enabled: bool = False
    n8n_webhook_url: str | None = None
    n8n_hmac_secret: str | None = None

    # --- uploads ---
    max_upload_mb: int = 25

    @field_validator("llm_api_key", "n8n_hmac_secret", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def database_enabled(self) -> bool:
        return bool(self.supabase_db_url)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
