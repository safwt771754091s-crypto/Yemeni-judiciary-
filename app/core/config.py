"""Application configuration (pydantic-settings, 12-factor)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.permissions import Role


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
    #: Bound on establishing a TCP/TLS connection to the database. Without it a
    #: blocked or unreachable host stalls the caller until the OS gives up
    #: (minutes), which reads as a hung command.
    db_connect_timeout: float = 10.0

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
    #: Retries for transient provider failures (429/5xx/timeout). A rate-limited
    #: provider must not discard a drafted judgment mid-pipeline.
    llm_max_retries: int = 3
    llm_retry_base_delay_seconds: float = 2.0
    llm_retry_max_delay_seconds: float = 60.0

    # --- retrieval ---
    retrieval_top_k: int = 8
    retrieval_candidates: int = 40
    vector_weight: float = 0.65
    keyword_weight: float = 0.35
    rrf_k: int = 60
    min_citation_score: float = 0.18

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
    upload_dir: str = "./var/uploads"
    max_upload_files: int = 10
    max_case_text_chars: int = 200_000

    # --- document extraction / OCR ---
    # Scanned Yemeni court filings carry no text layer, so OCR is the only way
    # to read them. Set OCR_ENABLED=false where Tesseract is not installed.
    ocr_enabled: bool = True
    ocr_languages: str = "ara+eng"
    ocr_dpi: int = 300
    ocr_psm: int = 6
    # A PDF page whose embedded text layer yields fewer characters than this is
    # re-read with OCR (typical of scanned filings).
    pdf_text_min_chars: int = 80

    # --- intake / classification ---
    # Below this score the intake response flags the case type for human review.
    classification_min_confidence: float = 0.35
    intake_auto_judgment: bool = True

    # --- auth / RBAC ---
    # Signing key for access tokens. MUST be set in production; the default is
    # only usable for local development.
    jwt_secret: str = "dev-insecure-change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720
    # The platform owner, seeded on startup if the users table is empty.
    owner_username: str = "owner"
    owner_password: str = "Owner@Yemen2026"
    owner_email: str | None = None
    owner_full_name: str = "مالك المنصة"

    # Self-service signup. Enabled by default so the platform can be tried
    # without an operator; set ALLOW_SELF_SIGNUP=false to require an invitation.
    # Signups may only pick from these roles - never owner, admin or editor.
    allow_self_signup: bool = True
    signup_default_role: Role = Role.VIEWER
    signup_roles: list[Role] = [
        Role.VIEWER,
        Role.RESEARCHER,
        Role.LAWYER,
        Role.JUDGE,
        Role.PROSECUTOR,
    ]

    @field_validator("llm_api_key", "n8n_hmac_secret", "owner_email", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def database_enabled(self) -> bool:
        return bool(self.supabase_db_url)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def auth_secret(self) -> str:
        """Signing key, warning loudly when the insecure default is in use."""
        if self.jwt_secret == "dev-insecure-change-me-in-production" and self.is_production:
            raise RuntimeError(
                "JWT_SECRET must be set to a strong random value in production"
            )
        return self.jwt_secret


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
