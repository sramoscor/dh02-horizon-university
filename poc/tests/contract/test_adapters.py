"""T-CONTRACT-01 · Los adaptadores y los simuladores coinciden; datos inválidos se rechazan (E2-ADR-008; QS-09).

Un simulador no prueba el comportamiento no documentado de un proveedor real (limitación declarada).
"""

import base64
import json

import httpx
import pytest

from backend.application.delivery import DirectoryRefreshService
from backend.application.ports import Delivery, PermanentDeliveryFailure, TemporaryDeliveryFailure
from backend.domain.errors import SessionInvalid
from backend.infrastructure.adapters import (
    DirectoryDataInvalid,
    HttpDirectoryAdapter,
    HttpIdentityAdapter,
    HttpNotificationAdapter,
)
from backend.infrastructure.clock import SystemClock
from backend.infrastructure.sql import SqlUnitOfWorkFactory
from tests.conftest import MOCKS_URL, rows

pytestmark = pytest.mark.mocks
REFS = ["poc/backend/infrastructure/adapters.py", "poc/backend/application/delivery.py:DirectoryRefreshService",
        "poc/backend/infrastructure/sql.py:SqlLocations.upsert", "poc/mocks/app.py"]
RELATED = {"requirements": ["E2-FR-01", "E2-FR-08", "E2-FR-11"], "qualities": ["QS-09"],
           "decisions": ["E2-ADR-008"]}


@pytest.fixture
def mocks():
    if not MOCKS_URL:
        pytest.skip("MOCKS_URL no definido: requiere los simuladores")
    client = httpx.Client(base_url=MOCKS_URL, timeout=5)
    client.post("/_control/reset")
    yield client
    client.post("/_control/reset")
    client.close()


@pytest.mark.evidence("T-CONTRACT-01", code_refs=REFS, related=RELATED,
                      expected="Datos inválidos rechazados o en cuarentena; sin degradar proyecciones")
def test_identity_contract(mocks, evidence):
    adapter = HttpIdentityAdapter(f"{MOCKS_URL}/x-id")
    assertion = mocks.post("/x-id/authorize", json={"subject": "coord-03"}).json()["assertion"]
    assert adapter.verify(assertion).subject_id == "coord-03"
    with pytest.raises(SessionInvalid):
        adapter.verify(assertion)  # un solo uso
    raw, sig = mocks.post("/x-id/authorize", json={"subject": "usr-017"}).json()["assertion"].split(".")
    forged = json.loads(base64.urlsafe_b64decode(raw))
    forged["sub"] = "sup-01"
    forged_assertion = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode() + "." + sig
    with pytest.raises(SessionInvalid):
        adapter.verify(forged_assertion)
    mocks.post("/_control/xid", json={"mode": "down"})
    with pytest.raises(SessionInvalid):
        adapter.verify("cualquiera")
    evidence.observe("x_id", "válida → sujeto; reutilizada, falsificada o proveedor caído → SESSION_INVALID")


@pytest.mark.db
@pytest.mark.evidence("T-CONTRACT-01")
def test_directory_contract_and_projection_is_not_degraded(mocks, fresh_db, clock, evidence):
    service = DirectoryRefreshService(SqlUnitOfWorkFactory(fresh_db), HttpDirectoryAdapter(f"{MOCKS_URL}/x-dir"),
                                      SystemClock())
    ok = service.refresh()
    assert ok == {"updated": 6, "rejected": 0}
    snapshot = rows(fresh_db, "SELECT location_id, source_revision, active, fetched_at FROM location_projection "
                              "ORDER BY 1")
    mocks.post("/_control/xdir", json={"mode": "invalid"})
    with pytest.raises(DirectoryDataInvalid):
        service.refresh()
    mocks.post("/_control/xdir", json={"mode": "regress"})
    regress = service.refresh()
    assert regress == {"updated": 0, "rejected": 6}
    mocks.post("/_control/xdir", json={"mode": "down"})
    with pytest.raises(httpx.HTTPStatusError):
        service.refresh()
    after = rows(fresh_db, "SELECT location_id, source_revision, active, fetched_at FROM location_projection "
                           "ORDER BY 1")
    assert after == snapshot
    evidence.observe("x_dir", {"válido": ok, "esquema_inválido": "DirectoryDataInvalid; sin escritura",
                               "revisión_que_retrocede": regress, "caído": "HTTP 503; proyección intacta"})


@pytest.mark.evidence("T-CONTRACT-01")
def test_notification_contract(mocks, evidence):
    adapter = HttpNotificationAdapter(f"{MOCKS_URL}/x-notify", timeout=1)
    d = Delivery("key-1", "WorkOrderAssigned", "TECH-021", "EMAIL", {"eventId": "key-1"})
    adapter.send(d)
    adapter.send(d)  # misma clave → el receptor deduplica
    received = mocks.get("/x-notify/messages").json()
    assert received["unique"] == 1 and received["duplicates"] == 1
    mocks.post("/_control/xnotify", json={"mode": "down"})
    with pytest.raises(TemporaryDeliveryFailure):
        adapter.send(d)
    mocks.post("/_control/xnotify", json={"mode": "error400"})
    with pytest.raises(PermanentDeliveryFailure):
        adapter.send(d)
    evidence.observe("x_notify", "2xx entregado; misma clave deduplicada; 5xx/timeout temporal; 4xx permanente")
    evidence.limit("No se implementó la cuarentena de esquemas desconocidos del outbox (§6.3): solo existe "
                   "schema_version=1. Queda como diseñado, no implementado.")
