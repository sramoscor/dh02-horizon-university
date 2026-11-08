"""Configuración por entorno (§9.5). Secretos solo por variables de entorno; ejemplos en .env.example."""

import os
from dataclasses import dataclass, field


def _floats(raw: str) -> tuple[float, ...]:
    return tuple(float(x) for x in raw.split(",") if x.strip())


def _bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    xid_url: str = field(default_factory=lambda: os.environ.get("XID_URL", "http://mocks:8090/x-id"))
    xdir_url: str = field(default_factory=lambda: os.environ.get("XDIR_URL", "http://mocks:8090/x-dir"))
    xnotify_url: str = field(default_factory=lambda: os.environ.get("XNOTIFY_URL", "http://mocks:8090/x-notify"))
    demo_mode: bool = field(default_factory=lambda: _bool(os.environ.get("DEMO_MODE", "false")))
    cookie_secure: bool = field(default_factory=lambda: _bool(os.environ.get("COOKIE_SECURE", "false")))
    session_ttl_minutes: int = field(default_factory=lambda: int(os.environ.get("SESSION_TTL_MINUTES", "480")))
    directory_max_age_hours: float = field(
        default_factory=lambda: float(os.environ.get("DIRECTORY_MAX_AGE_HOURS", "24")))
    # Worker (QS-02, §9.5). Valores por defecto = diseño; la demo los acelera vía .env (documentado).
    worker_poll_seconds: float = field(default_factory=lambda: float(os.environ.get("WORKER_POLL_SECONDS", "2")))
    worker_lease_seconds: float = field(default_factory=lambda: float(os.environ.get("WORKER_LEASE_SECONDS", "15")))
    worker_backoff_seconds: tuple[float, ...] = field(
        default_factory=lambda: _floats(os.environ.get("WORKER_BACKOFF_SECONDS", "5,10,20,30")))
    worker_max_attempts: int = field(default_factory=lambda: int(os.environ.get("WORKER_MAX_ATTEMPTS", "12")))
    notify_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("NOTIFY_TIMEOUT_SECONDS", "5")))
    directory_refresh_seconds: float = field(
        default_factory=lambda: float(os.environ.get("DIRECTORY_REFRESH_SECONDS", "300")))
    overdue_check_seconds: float = field(
        default_factory=lambda: float(os.environ.get("OVERDUE_CHECK_SECONDS", "60")))
    campus_holidays: str = field(default_factory=lambda: os.environ.get("CAMPUS_HOLIDAYS", ""))
