"""Settings read from .env (see .env.example for what each one does)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://jap:jap@db:5432/jap"

    openrouter_api_key: str = ""
    # Empty means OpenRouter itself (llm/client.py); only a stand-in for testing sets it.
    openrouter_url: str = ""
    jap_model_extract: str = "openai/gpt-5.6-luna"
    jap_model_write: str = "anthropic/claude-sonnet-4.5"
    jap_model_embed: str = "nomic-embed-text"
    jap_embed_dim: int = 768
    ollama_base_url: str = "http://host.docker.internal:11434"

    jap_monthly_cost_cap_usd: float = 2.0

    # The mailbox JAP sends from (E4): any SMTP server with a password, GoDaddy Professional
    # Email by default. A local test mailbox can take plain SMTP (SMTP_SSL=false) instead.
    smtp_host: str = "smtpout.secureserver.net"
    smtp_port: int = 465
    smtp_ssl: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    # Older .env files name the Gmail login these; used when SMTP_USERNAME is empty.
    gmail_address: str = ""
    gmail_app_password: str = ""

    @property
    def email_from(self) -> str:
        return self.smtp_username or self.gmail_address

    @property
    def email_password(self) -> str:
        return self.smtp_password if self.smtp_username else self.gmail_app_password

    inngest_base_url: str = "http://inngest:8288"

    jap_tz: str = "Asia/Kolkata"

    # Resumes, screenshots, cover letters and backups (data/ on the Mac).
    jap_data_dir: str = "/data"

    # Where the confirmation email links back to (the frozen JD in the side panel).
    jap_ui_url: str = "http://localhost:5173"

    # Apply (A1): at least this many seconds between two submissions.
    jap_apply_gap_seconds: int = 30

    # The UI dev server is the only allowed browser origin.
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
