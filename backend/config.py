"""Settings read from .env (see .env.example for what each one does)."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# PRD 1.30 AH1: who does the AI work. "agent" is any outside harness working through the
# export and import commands (agent/ in the repo); "api" is JAP calling a model itself.
Mode = Literal["agent", "api"]
OLD_MODE_NAMES = {"claude": "agent", "openrouter": "api"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://jap:jap@db:5432/jap"

    # Which environment this stack is (H3): dev, test or prod. The UI shows a DEV or TEST badge.
    jap_env: str = "dev"
    # Off in test (H4): an application is never submitted to a real job site from there.
    jap_submit_enabled: bool = True

    openrouter_api_key: str = ""
    # Empty means OpenRouter itself (llm/client.py); only a stand-in for testing sets it.
    openrouter_url: str = ""
    jap_model_extract: str = "openai/gpt-5.6-luna"
    jap_model_write: str = "anthropic/claude-sonnet-4.5"
    jap_model_embed: str = "nomic-embed-text"
    jap_embed_dim: int = 768
    ollama_base_url: str = "http://host.docker.internal:11434"

    jap_monthly_cost_cap_usd: float = 2.0
    # Who reads postings for skills, work mode and the rest (I3, PRD 1.25 R1, 1.30 AH1):
    # "agent", an outside AI harness through make reader-export / reader-import (the runner in
    # agent/ starts it), or "api", JAP's own model call through OpenRouter as before 1.25 (the
    # fallback, needs a funded key). The older names "claude" and "openrouter" still work.
    jap_reader: Mode = "agent"
    # Who drafts free-text form answers, cover letters and resume reads (PRD 1.27 W10, 1.30
    # AH1): "agent", the outside AI writer through make writer-export / writer-import, so such a
    # question waits at Needs you, or "api", JAP's own model call (the fallback).
    jap_writer: Mode = "agent"

    @field_validator("jap_reader", "jap_writer", mode="before")
    @classmethod
    def _old_mode_names(cls, value: object) -> object:
        """AH1: claude and openrouter, the names before 1.30, mean agent and api."""
        if isinstance(value, str):
            return OLD_MODE_NAMES.get(value.strip().lower(), value.strip().lower())
        return value

    # The mailbox JAP sends from (E4): any SMTP server with a password, GoDaddy Professional
    # Email by default. A local test mailbox can take plain SMTP (SMTP_SSL=false) instead.
    smtp_host: str = "smtpout.secureserver.net"
    smtp_port: int = 465
    smtp_ssl: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    # The same mailbox read over IMAP for a security code a job site emails (A5).
    imap_host: str = "imap.secureserver.net"
    imap_port: int = 993
    imap_ssl: bool = True
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
    # Up to this many seconds more on top of the gap, different for each job (A1, PRD 1.29).
    jap_apply_jitter_seconds: int = 30
    # Automatic at volume (V3 to V7, PRD 1.29): at most this many attempts a day at one
    # company, at most this many submits a day on one ATS (Greenhouse, Lever, Ashby) with at
    # least this gap between two on the same ATS, and how many jobs past the daily cap a run
    # picks so held or flagged jobs do not leave the day short.
    jap_per_company_day: int = 2
    jap_per_ats_day: int = 8
    jap_ats_gap_seconds: int = 300
    jap_auto_extra_picks: int = 5

    # Sign-in (PRD S1 to S5). Secure cookies need HTTPS: true on dev, test and prod hosts.
    jap_cookie_secure: bool = False
    # When set, only this address can create the first (admin) account.
    jap_admin_email: str = ""
    jap_session_idle_days: int = 7
    jap_session_max_days: int = 30

    # Logging (PRD O1 to O4). LOG_JSON=false prints plain lines, easier to read by eye in dev.
    log_level: str = "INFO"
    log_json: bool = True
    # Keep warnings, errors and timeline steps in the log_event table (O3, O8).
    jap_log_db: bool = True

    # Health alerts by email (O6). Off where nothing should be sent (dev and test).
    jap_alerts_enabled: bool = True
    jap_alert_errors_per_hour: int = 10

    # Sentry's free plan (O15): empty means off. Only redacted errors are sent.
    sentry_dsn: str = ""

    # The UI dev server is the only allowed browser origin.
    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
