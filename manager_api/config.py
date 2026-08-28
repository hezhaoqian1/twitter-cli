"""Typed runtime configuration for the manager application."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ManagerSettings(BaseSettings):
    """Load manager settings from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env.manager",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(..., description="PostgreSQL connection URL")
    redis_url: str = Field(..., description="Redis connection URL")
    session_secret: str = Field(..., min_length=16)
    worker_concurrency: int = Field(default=3, ge=1, le=32)
    browser_concurrency: int = Field(default=2, ge=1, le=16)
    external_poll_interval_seconds: float = Field(default=15.0, gt=0, le=3600)
    external_poll_timeout_seconds: float = Field(default=900.0, gt=0, le=86400)
    healthcheck_timeout_seconds: float = Field(default=3.0, gt=0, le=30)

    @property
    def sqlalchemy_url(self) -> str:
        """Return a SQLAlchemy URL with the psycopg driver selected."""
        if self.database_url.startswith("postgres://"):
            return "postgresql+psycopg://" + self.database_url[len("postgres://") :]
        if self.database_url.startswith("postgresql://"):
            return "postgresql+psycopg://" + self.database_url[len("postgresql://") :]
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> ManagerSettings:
    """Return the process-wide settings snapshot."""
    # Pydantic Settings resolves the required values from the process environment.
    return ManagerSettings()  # type: ignore[call-arg]
