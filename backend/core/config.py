"""Validated application settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = 8000
    secret_key: str = "disputeflow-dev-secret-key-change-in-production"
    api_key: str = "disputeflow-dev-api-key-change-in-production"
    cors_origins: str = "http://localhost:3000"

    llm_provider: Literal["groq", "openai", "openrouter"] = "openrouter"
    groq_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    openai_model: str = "gpt-4o-mini"
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct"
    openrouter_api_base: str = "https://openrouter.ai/api/v1"
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 2

    database_url: str = "sqlite+aiosqlite:///./disputeflow.db"
    checkpoint_database_url: str = ""

    allow_heuristic_model: bool = True
    model_path: str = "backend/data/models/win_probability_model.pkl"
    auto_approve_human_review: bool = True

    github_repo_url: str = "https://github.com/IvayloP0709/dispute-automation"
    github_token: str = ""
    dispute_source: Literal["github", "local"] = "github"
    github_owner: str = ""
    github_repo: str = ""
    github_ref: str = "main"
    github_input_path: str = "disputes/input"
    github_webhook_secret: str = ""

    @property
    def github_owner_repo(self) -> tuple[str, str]:
        owner = (self.github_owner or "").strip()
        repo = (self.github_repo or "").strip()
        placeholders = {"your-github-username", "your-dispute-cases-repository"}
        if owner and repo and owner not in placeholders and repo not in placeholders:
            return owner, repo
        from urllib.parse import urlparse

        parsed = urlparse((self.github_repo_url or "").rstrip("/"))
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            parsed_owner, parsed_repo = parts[0], parts[1].removesuffix(".git")
            if parsed_owner not in placeholders and parsed_repo not in placeholders:
                return parsed_owner, parsed_repo
        return "", ""

    @property
    def allow_local_dispute_create(self) -> bool:
        if self.app_env == "test":
            return True
        return self.dispute_source == "local"

    rate_limit_per_minute: int = 120
    max_request_bytes: int = 262144

    @field_validator("secret_key", "api_key")
    @classmethod
    def reject_blank_secrets(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("SECRET_KEY and API_KEY must be set")
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        weak = {
            "disputeflow-dev-secret-key-change-in-production",
            "disputeflow-dev-api-key-change-in-production",
            "change-this-to-a-long-random-secret",
            "change-this-to-a-long-random-api-key",
        }
        if self.app_env == "production":
            if self.secret_key in weak or len(self.secret_key) < 24:
                raise ValueError("Production SECRET_KEY must be a long unique value")
            if self.api_key in weak or len(self.api_key) < 24:
                raise ValueError("Production API_KEY must be a long unique value")
            if self.allow_heuristic_model:
                raise ValueError("Production ALLOW_HEURISTIC_MODEL must be false")
        return self

    @staticmethod
    def _is_placeholder_key(value: str) -> bool:
        return not value or value.startswith("your_")

    @staticmethod
    def _is_openrouter_key(value: str) -> bool:
        return bool(value) and value.startswith("sk-or-v1-")

    @staticmethod
    def _is_groq_key(value: str) -> bool:
        return bool(value) and value.startswith("gsk_")

    @classmethod
    def _pick_openrouter_key(
        cls,
        openrouter_api_key: str,
        openai_api_key: str,
        groq_api_key: str,
    ) -> str:
        # OpenRouter credentials must be configured explicitly. Do not silently
        # reinterpret an OpenAI/Groq variable as an OpenRouter credential.
        return openrouter_api_key if cls._is_openrouter_key(openrouter_api_key) else ""

    @model_validator(mode="after")
    def resolve_llm_provider(self) -> "Settings":
        """Prefer a provider that matches the key actually present in .env."""
        if self.app_env == "test":
            return self

        openrouter_key = self._pick_openrouter_key(
            self.openrouter_api_key, self.openai_api_key, self.groq_api_key
        )
        groq_ready = not self._is_placeholder_key(self.groq_api_key) and self._is_groq_key(self.groq_api_key)
        openai_ready = (
            not self._is_placeholder_key(self.openai_api_key)
            and not self._is_openrouter_key(self.openai_api_key)
        )

        if openrouter_key and self.llm_provider in {"groq", "openai"} and not groq_ready:
            self.llm_provider = "openrouter"
        elif groq_ready and self.llm_provider == "openrouter" and not openrouter_key:
            self.llm_provider = "groq"
        elif openai_ready and self.llm_provider == "groq" and not groq_ready and not openrouter_key:
            self.llm_provider = "openai"
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///") and "+aiosqlite" not in url:
            return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    @property
    def sqlite_connect_args(self) -> dict[str, int | bool]:
        """Reduce 'database is locked' errors under uvicorn reload / concurrent requests."""
        if not self.is_sqlite:
            return {}
        return {"check_same_thread": False, "timeout": 30}

    @property
    def resolved_openrouter_api_key(self) -> str:
        return self._pick_openrouter_key(
            self.openrouter_api_key, self.openai_api_key, self.groq_api_key
        )

    @property
    def llm_credentials_configured(self) -> bool:
        if self.llm_provider == "groq":
            return not self._is_placeholder_key(self.groq_api_key) and self._is_groq_key(self.groq_api_key)
        if self.llm_provider == "openai":
            return (
                not self._is_placeholder_key(self.openai_api_key)
                and not self._is_openrouter_key(self.openai_api_key)
            )
        if self.llm_provider == "openrouter":
            return bool(self.resolved_openrouter_api_key)
        return False

    def missing_llm_message(self) -> str:
        if self.llm_provider == "groq":
            if self.resolved_openrouter_api_key:
                return (
                    "LLM_PROVIDER=groq but GROQ_API_KEY is missing. "
                    "Set LLM_PROVIDER=openrouter and OPENROUTER_API_KEY in .env."
                )
            return "GROQ_API_KEY must be set to a real Groq key (starts with gsk_)."
        if self.llm_provider == "openai":
            return (
                "OPENAI_API_KEY must be set to a real OpenAI key. "
                "For OpenRouter, use LLM_PROVIDER=openrouter and OPENROUTER_API_KEY."
            )
        return "OPENROUTER_API_KEY must be set in .env (OpenRouter keys start with sk-or-v1-)."

    def require_llm_credentials(self) -> None:
        if self.app_env == "test":
            return
        if self.app_env == "development" and self.allow_heuristic_model and not self.llm_credentials_configured:
            return
        if not self.llm_credentials_configured:
            raise RuntimeError(self.missing_llm_message())

    @property
    def llm_model(self) -> str:
        if self.llm_provider == "groq":
            return self.groq_model
        if self.llm_provider == "openrouter":
            return self.openrouter_model
        return self.openai_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
