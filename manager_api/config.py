"""Typed runtime configuration for the manager application."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
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
    vault_cache_ttl_seconds: float = Field(default=900.0, gt=0, le=86400)
    external_poll_interval_seconds: float = Field(default=15.0, gt=0, le=3600)
    external_poll_timeout_seconds: float = Field(default=900.0, gt=0, le=86400)
    healthcheck_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    worker_lease_ttl_seconds: float = Field(default=120.0, gt=0, le=86400)
    worker_recovery_interval_seconds: float = Field(default=30.0, gt=0, le=3600)
    worker_heartbeat_interval_seconds: float = Field(default=15.0, gt=0, le=3600)
    worker_heartbeat_ttl_seconds: float = Field(default=45.0, gt=0, le=86400)
    worker_idle_sleep_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_vault_password: SecretStr | None = None
    manager_x_adapter_factory: str = ""
    manager_kredo_workflow_factory: str = ""
    manager_kredo_browser_artifact_dir: str = "artifacts/kredo-worker"
    manager_kredo_browser_timeout_seconds: int = Field(default=120, ge=1, le=1800)
    manager_kredo_browser_headed: bool = False

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
