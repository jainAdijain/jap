"""Settings read from .env (see .env.example for what each one does)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://jap:jap@db:5432/jap"

    openrouter_api_key: str = ""
    jap_model_extract: str = "openai/gpt-5.6-luna"
    jap_model_write: str = "anthropic/claude-sonnet-4.5:batch"
    jap_model_embed: str = "nomic-embed-text"
    jap_embed_dim: int = 768
    ollama_base_url: str = "http://host.docker.internal:11434"

    jap_monthly_cost_cap_usd: float = 2.0

    gmail_address: str = ""
    gmail_app_password: str = ""

    inngest_base_url: str = "http://inngest:8288"

    jap_tz: str = "Asia/Kolkata"

    # The UI dev server is the only allowed browser origin.
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
