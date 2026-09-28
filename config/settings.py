"""
Configuration settings loaded from .env file with pydantic-settings.
All secrets are stored in .env only - never hardcoded.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Email ──────────────────────────────────────────────────────────────
    EMAIL: str = ""
    EMAIL_PASSWORD: str = ""
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    USE_GMAIL_API: bool = False
    GMAIL_CREDENTIALS_FILE: str = "credentials.json"

    # ── Schedule ──────────────────────────────────────────────────────────
    CHECK_INTERVAL: int = 60  # minutes
    CRON_SCHEDULE: str = "*/60 * * * *"

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_PATH: str = "data/opportunities.db"

    # ── Logging ───────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/dedan_remote.log"

    # ── Telegram ──────────────────────────────────────────────────────────
    ENABLE_TELEGRAM: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ── Discord ───────────────────────────────────────────────────────────
    ENABLE_DISCORD: bool = False
    DISCORD_WEBHOOK_URL: str = ""

    # ── Scraper ───────────────────────────────────────────────────────────
    REQUEST_TIMEOUT: int = 30
    MAX_RETRIES: int = 3
    CONCURRENT_SCRAPERS: int = 5
    RATE_LIMIT_DELAY: float = 1.0
    CACHE_TTL: int = 300  # seconds

    # ── Ranking Weights (must sum to 1.0) ────────────────────────────────
    RANKING_WEIGHT_REMOTE: float = 0.20
    RANKING_WEIGHT_WORLDWIDE: float = 0.15
    RANKING_WEIGHT_SALARY: float = 0.15
    RANKING_WEIGHT_BEGINNER: float = 0.10
    RANKING_WEIGHT_AI_RELATED: float = 0.15
    RANKING_WEIGHT_ENGLISH: float = 0.05
    RANKING_WEIGHT_SIMPLICITY: float = 0.10
    RANKING_WEIGHT_FRESHNESS: float = 0.10

    # ── Notification ─────────────────────────────────────────────────────
    MIN_SCORE_FOR_NOTIFICATION: int = 50

    # ── Circuit Breaker ──────────────────────────────────────────────────
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = 60  # seconds

    CACHED_WEIGHTS: ClassVar[dict[str, float] | None] = None

    @field_validator(
        "RANKING_WEIGHT_REMOTE",
        "RANKING_WEIGHT_WORLDWIDE",
        "RANKING_WEIGHT_SALARY",
        "RANKING_WEIGHT_BEGINNER",
        "RANKING_WEIGHT_AI_RELATED",
        "RANKING_WEIGHT_ENGLISH",
        "RANKING_WEIGHT_SIMPLICITY",
        "RANKING_WEIGHT_FRESHNESS",
    )
    @classmethod
    def validate_weight_range(cls, v: float) -> float:
        """Ensure weight is between 0 and 1."""
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Weight must be between 0.0 and 1.0, got {v}")
        return v

    def get_ranking_weights(self) -> dict[str, float]:
        """Return dict of ranking weights, cached for performance."""
        if self.CACHED_WEIGHTS is not None:
            return self.CACHED_WEIGHTS
        weights = {
            "remote": self.RANKING_WEIGHT_REMOTE,
            "worldwide": self.RANKING_WEIGHT_WORLDWIDE,
            "salary": self.RANKING_WEIGHT_SALARY,
            "beginner": self.RANKING_WEIGHT_BEGINNER,
            "ai_related": self.RANKING_WEIGHT_AI_RELATED,
            "english": self.RANKING_WEIGHT_ENGLISH,
            "simplicity": self.RANKING_WEIGHT_SIMPLICITY,
            "freshness": self.RANKING_WEIGHT_FRESHNESS,
        }
        total = sum(weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Ranking weights must sum to 1.0, got {total}")
        Settings.CACHED_WEIGHTS = weights
        return weights

    # ── DEDAN Remote (public API layer) ─────────────────────────────────────
    # Token protecting the internal /api/system dashboard. When empty, the
    # endpoint reports "not configured" instead of exposing anything.
    DEDAN_SYSTEM_TOKEN: str = ""
    # Comma-separated browser origins allowed by CORS (dev + prod).
    DEDAN_CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Trust X-Forwarded-For / X-Real-IP when behind a reverse proxy or load
    # balancer. Leave false when clients connect directly: otherwise anyone
    # could spoof the header to escape per-IP rate limiting.
    DEDAN_TRUST_PROXY: bool = False
    # Deployment environment: "production" enables HSTS and strict host checks.
    DEDAN_ENV: str = "development"
    # Send Strict-Transport-Security (only meaningful over HTTPS).
    DEDAN_ENABLE_HSTS: bool = False
    # Reject request bodies larger than this (bytes). Guards the JSON API.
    DEDAN_MAX_BODY_BYTES: int = 1_000_000
    # TTL (seconds) for cached read-only aggregates (stats/status/sources/…).
    # 0 disables caching. Values must stay short so the API never shows data
    # older than the discovery cycle that produced it.
    DEDAN_CACHE_TTL_SECONDS: float = 60.0

    # ── AE-OS Core Configuration ────────────────────────────────────────────
    AEOS_ENABLED: bool = False
    AEOS_POSTGRES_DSN: str = ""
    AEOS_POSTGRES_HOST: str = "localhost"
    AEOS_POSTGRES_PORT: int = 5432
    AEOS_POSTGRES_USER: str = "aeos"
    AEOS_POSTGRES_PASSWORD: str = "aeos_password"
    AEOS_POSTGRES_DATABASE: str = "aeos_episodic"

    AEOS_REDIS_URL: str = "redis://localhost:6379/0"

    AEOS_NEO4J_URI: str = "bolt://localhost:7687"
    AEOS_NEO4J_USER: str = "neo4j"
    AEOS_NEO4J_PASSWORD: str = "aeos_neo4j"

    AEOS_QDRANT_HOST: str = "localhost"
    AEOS_QDRANT_PORT: int = 6333

    AEOS_METRICS_PORT: int = 9090
    AEOS_IDEMPOTENCY_SECRET: str = "aeos-idempotency-secret-change-me"
    AEOS_TREASURY_MAX: float = 1000.0

    def validate_email_config(self) -> None:
        """Validate that email config is present if email notifications are expected."""
        if not self.EMAIL or not self.EMAIL_PASSWORD:
            raise ValueError(
                "EMAIL and EMAIL_PASSWORD must be set in .env for email notifications."
            )

    @property
    def database_dir(self) -> Path:
        """Return directory for database file."""
        return Path(self.DATABASE_PATH).parent

    @property
    def log_dir(self) -> Path:
        """Return directory for log file."""
        return Path(self.LOG_FILE).parent

    @property
    def is_production(self) -> bool:
        """True when running in a production deployment."""
        return self.DEDAN_ENV.strip().lower() in ("production", "prod")


def get_settings() -> Settings:
    """Load and return singleton Settings instance."""
    env_path = Path(".env")
    if not env_path.exists():
        # Copy from .env.example if .env doesn't exist
        example_path = Path(".env.example")
        if example_path.exists():
            import shutil

            shutil.copy(example_path, env_path)
            print("[WARN] .env created from .env.example — please update with your credentials.")
    return Settings()  # type: ignore[call-arg]
