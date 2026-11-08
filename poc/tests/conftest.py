"""Fixtures compartidas. Las pruebas de integración usan PostgreSQL REAL (DATABASE_URL_TEST), nunca un
repositorio en memoria: un repositorio simulado no puede ejercitar el riesgo de concurrencia (§10.1).
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.application.ports import VerifiedIdentity
from backend.bootstrap.container import build_container
from backend.domain.errors import SessionInvalid
from backend.http.app import create_http_app
from backend.infrastructure.clock import OffsetClock
from backend.infrastructure.seeding import FIXTURES, reset
from backend.infrastructure.settings import Settings
from backend.infrastructure.sql import make_engine

pytest_plugins = ["tests.evidence_plugin"]

POC = Path(__file__).resolve().parents[1]
DATABASE_URL_TEST = os.environ.get("DATABASE_URL_TEST")
MOCKS_URL = os.environ.get("MOCKS_URL")
USERS = {u["subject"]: u["displayName"] for u in json.loads((FIXTURES / "users.json").read_text())["users"]}
TABLES = ["maintenance_request", "triage_record", "work_order", "assignment", "block_event", "material_usage",
          "observation", "completion_confirmation", "audit_event", "outbox_event", "command_receipt"]


@pytest.fixture(scope="session")
def db_engine():
    if not DATABASE_URL_TEST:
        pytest.skip("DATABASE_URL_TEST no definido: la prueba requiere PostgreSQL real")
    engine = make_engine(DATABASE_URL_TEST, pool_size=20)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
        os.environ["EVIDENCE_DB_VERSION"] = c.execute(text("SHOW server_version")).scalar_one()
    subprocess.check_call([sys.executable, "-m", "alembic", "-x", f"url={DATABASE_URL_TEST}", "upgrade", "head"],
                          cwd=POC)
    yield engine
    engine.dispose()


@pytest.fixture
def clock() -> OffsetClock:
    return OffsetClock()


@pytest.fixture
def fresh_db(db_engine, clock):
    reset(db_engine, clock.now())
    return db_engine


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=DATABASE_URL_TEST or "", demo_mode=True)


class FakeIdentity:
    """X-ID en proceso: la aserción es el sujeto. El adaptador HTTP real se prueba en tests/contract."""

    def verify(self, assertion: str) -> VerifiedIdentity:
        if assertion not in USERS:
            raise SessionInvalid("Aserción rechazada.")
        return VerifiedIdentity(assertion, USERS[assertion])


class User:
    def __init__(self, app, subject: str) -> None:
        self.subject = subject
        self.client = TestClient(app, raise_server_exceptions=False)
        r = self.client.post("/api/v1/auth/sessions", json={"assertion": subject})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrfToken"]
        self.roles = r.json()["roles"]

    def get(self, path: str, **kw):
        return self.client.get(f"/api/v1{path}", **kw)

    def post(self, path: str, json: Any = None, *, if_match: str | None = None, key: str | None = None,
             csrf: bool = True, headers: dict | None = None):
        h = {"Idempotency-Key": key or f"k-{uuid.uuid4()}"}
        if csrf:
            h["X-CSRF-Token"] = self.csrf
        if if_match:
            h["If-Match"] = if_match
        h.update(headers or {})
        return self.client.post(f"/api/v1{path}", json=json, headers=h)

    def request_etag(self, request_id: str) -> str:
        r = self.get(f"/requests/{request_id}")
        assert r.status_code == 200, r.text
        return r.json()["etag"]

    def wo_etag(self, work_order_id: str) -> str:
        r = self.get(f"/work-orders/{work_order_id}")
        assert r.status_code == 200, r.text
        return r.json()["etag"]


class Api:
    def __init__(self, app, container) -> None:
        self.app = app
        self.container = container

    def login(self, subject: str) -> User:
        return User(self.app, subject)


@pytest.fixture
def app_factory(fresh_db, clock, settings):
    def make(faults=None) -> Api:
        container = build_container(settings, fresh_db, clock, identity=FakeIdentity(), faults=faults)
        return Api(create_http_app(container.services, lambda: {"status": "ok"}), container)

    return make


@pytest.fixture
def api(app_factory) -> Api:
    return app_factory()


def counts(engine) -> dict[str, int]:
    with engine.connect() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in TABLES}


def scalar(engine, sql: str, **params):
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def rows(engine, sql: str, **params) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text(sql), params).mappings()]


def insert_location(engine, location_id: str, campus: str = "NORTE", building: str = "ADM") -> None:
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO location_projection (location_id, building_id, campus, display_name, active,
                restricted_access, building_representative_id, directory_version, source_revision, fetched_at)
            VALUES (:id, :b, :c, :id, true, false, NULL, 'DIR-v118', 118, now())
        """), {"id": location_id, "b": building, "c": campus})


def insert_request(engine, request_id: str, requester: str, location_id: str, status: str = "UNDER_TRIAGE",
                   category: str = "ELECTRICAL", version: int = 3) -> str:
    """Fixture directa (sin auditoría): estado inicial de una prueba, no una mutación de negocio."""
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO maintenance_request (request_id, requester_id, location_id, description, suggested_category,
                suggested_urgency, status, row_version, created_at, submitted_at, updated_at)
            VALUES (:id, :req, :loc, 'Prueba sintética', :cat, 'HIGH', :status, :ver, now(), now(), now())
        """), {"id": request_id, "req": requester, "loc": location_id, "cat": category, "status": status,
               "ver": version})
    return f'"request-{request_id}-v{version}"'


class FailAt:
    """Inyector de fallos para T-TXN-01: lanza en el punto de control indicado."""

    def __init__(self, point: str) -> None:
        self.point = point

    def checkpoint(self, name: str) -> None:
        if name == self.point:
            raise RuntimeError(f"fallo inyectado en {name}")


class DelayAt:
    """Retiene la transacción ganadora tras insertar la orden: garantiza contención real en la BD."""

    def __init__(self, point: str, seconds: float) -> None:
        self.point, self.seconds = point, seconds

    def checkpoint(self, name: str) -> None:
        if name == self.point:
            import time

            time.sleep(self.seconds)
