"""Punto de entrada de C-API: `uvicorn backend.bootstrap.api:app`."""

import logging

from sqlalchemy import text

from backend.bootstrap.container import build_container
from backend.http.app import create_http_app
from backend.infrastructure.clock import SystemClock
from backend.infrastructure.settings import Settings
from backend.infrastructure.sql import make_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

settings = Settings()
engine = make_engine(settings.database_url)
container = build_container(settings, engine, SystemClock())


def health() -> dict:
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
    return {"status": "ok", "demoMode": settings.demo_mode}


app = create_http_app(container.services, health)
