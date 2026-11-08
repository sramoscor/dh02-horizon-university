"""QS-02 · Falla de notificaciones tras una mutación (E2-ADR-004; V-SEQ-03; T-INT-01, T-INT-02).

Usa el simulador X-NOTIFY real (HTTP) y el mismo DeliveryService que corre en C-WORKER, contra PostgreSQL real.
"""

import threading
import time

import httpx
import pytest

from backend.application.delivery import DeliveryService, DeliverySettings
from backend.application.ports import Delivery
from backend.infrastructure.adapters import HttpNotificationAdapter
from backend.infrastructure.clock import SystemClock
from backend.infrastructure.sql import SqlDeliveryStore
from tests.conftest import MOCKS_URL, rows, scalar

pytestmark = [pytest.mark.db, pytest.mark.mocks]

INT_REFS = ["poc/backend/application/delivery.py:DeliveryService",
            "poc/backend/infrastructure/sql.py:SqlDeliveryStore.claim",
            "poc/backend/infrastructure/adapters.py:HttpNotificationAdapter",
            "poc/backend/bootstrap/worker.py", "poc/mocks/app.py:receive"]
INT_RELATED = {"requirements": ["E2-FR-08"], "qualities": ["QS-02"], "decisions": ["E2-ADR-004"]}


@pytest.fixture
def mocks():
    if not MOCKS_URL:
        pytest.skip("MOCKS_URL no definido: requiere el simulador X-NOTIFY")
    client = httpx.Client(base_url=MOCKS_URL, timeout=5)
    client.post("/_control/reset")
    yield client
    client.post("/_control/xnotify", json={"mode": "up"})
    client.close()


class WorkerLoop:
    """Bucle del worker en un hilo, como en `backend.bootstrap.worker`, con parámetros configurables."""

    def __init__(self, service: DeliveryService, poll: float) -> None:
        self.service, self.poll = service, poll
        self.stop = threading.Event()
        self.reports = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            self.reports.append(self.service.run_once())
            self.stop.wait(self.poll)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(30)


def assign_order(api):
    """Triaje que crea WO y asigna TECH-021 → eventos RequestTriaged + WorkOrderAssigned en el outbox."""
    coord = api.login("coord-03")
    r = coord.post("/requests/SR-2043/triage", {"action": "APPROVE_CREATE_WO", "category": "ELECTRICAL",
                                                 "validatedUrgency": "HIGH", "assigneeId": "TECH-021"},
                   if_match=coord.request_etag("SR-2043"))
    assert r.status_code == 201, r.text
    return r.json()["workOrder"]


def run_outage(api, fresh_db, mocks, evidence, *, settings: DeliverySettings, poll: float, timeout: float,
               outage_s: float, label: str):
    wo = assign_order(api)
    wo_id, version = wo["workOrderId"], wo["rowVersion"]
    total = scalar(fresh_db, "SELECT count(*) FROM outbox_event")
    mocks.post("/_control/xnotify", json={"mode": "down"})
    service = DeliveryService(SqlDeliveryStore(fresh_db),
                              HttpNotificationAdapter(f"{MOCKS_URL}/x-notify", timeout), SystemClock(), settings)
    timeline = []
    with WorkerLoop(service, poll):
        t0 = time.monotonic()
        while time.monotonic() - t0 < outage_s:
            time.sleep(max(poll, outage_s / 8))
            states = {r["delivery_state"]: r["n"] for r in
                      rows(fresh_db, "SELECT delivery_state, count(*) AS n FROM outbox_event GROUP BY 1")}
            wo_status = scalar(fresh_db, "SELECT status || '/v' || row_version FROM work_order "
                                         "WHERE work_order_id = :w", w=wo_id)
            timeline.append({"t": round(time.monotonic() - t0, 1), "orden": wo_status, "entregas": states})
            assert wo_status == f"ASSIGNED/v{version}"  # BR-09: la orden no se revierte ni se bloquea
            assert states.get("DELIVERED", 0) == 0
        max_attempts = scalar(fresh_db, "SELECT max(attempt_count) FROM outbox_event")
        mocks.post("/_control/xnotify", json={"mode": "up"})
        restored = time.monotonic()
        while scalar(fresh_db, "SELECT count(*) FROM outbox_event WHERE delivery_state = 'DELIVERED'") < total:
            assert time.monotonic() - restored <= 60, "no se completó la entrega en ≤ 60 s tras restaurar"
            time.sleep(0.2)
        recovery = time.monotonic() - restored
    received = mocks.get("/x-notify/messages").json()
    final_wo = scalar(fresh_db, "SELECT status || '/v' || row_version FROM work_order WHERE work_order_id = :w",
                      w=wo_id)
    dead = scalar(fresh_db, "SELECT count(*) FROM outbox_event WHERE delivery_state = 'DEAD_LETTER'")
    assert final_wo == f"ASSIGNED/v{version}" and dead == 0
    assert received["unique"] == total  # sin notificaciones duplicadas efectivas (receptor deduplica)
    evidence.set_execution(**{f"parametros[{label}]": {
        "caida_s": outage_s, "sondeo_s": poll, "timeout_s": timeout, "esperas_s": list(settings.backoff_seconds),
        "lease_s": settings.lease_seconds, "max_intentos": settings.max_attempts}})
    evidence.observe(f"{label}.orden_durante_y_despues", final_wo)
    evidence.observe(f"{label}.eventos", total)
    evidence.observe(f"{label}.intentos_maximos_durante_caida", max_attempts)
    evidence.observe(f"{label}.recuperacion_s", round(recovery, 2))
    evidence.observe(f"{label}.x_notify", {k: received[k] for k in ("unique", "duplicates", "attempts")})
    evidence.observe(f"{label}.linea_de_tiempo", timeline)
    print(label, f"recovery={recovery:.2f}s attempts={max_attempts}", received["unique"], received["duplicates"])
    return recovery


@pytest.mark.evidence("T-INT-01", code_refs=INT_REFS, related=INT_RELATED,
                      expected="Orden preservada; entrega diferida; completada ≤ 60 s tras restaurar")
def test_notifications_down_accelerated(api, fresh_db, mocks, evidence):
    settings = DeliverySettings(lease_seconds=5, backoff_seconds=(1, 2, 4, 4), max_attempts=50)
    run_outage(api, fresh_db, mocks, evidence, settings=settings, poll=0.5, timeout=2, outage_s=20,
               label="acelerado")
    evidence.limit("Variante acelerada: caída de 20 s (no 2 min) y esperas 1/2/4/4 s para que la suite sea rápida. "
                   "La variante con los parámetros del diseño está en test_notifications_down_design_parameters.")


@pytest.mark.slow
@pytest.mark.evidence("T-INT-01", code_refs=INT_REFS, related=INT_RELATED)
def test_notifications_down_design_parameters(api, fresh_db, mocks, evidence):
    """QS-02 tal como está escrito: 2 minutos de caída; sondeo 2 s, timeout 5 s, esperas 5/10/20/30 s, lease 15 s."""
    settings = DeliverySettings(lease_seconds=15, backoff_seconds=(5, 10, 20, 30), max_attempts=12)
    recovery = run_outage(api, fresh_db, mocks, evidence, settings=settings, poll=2, timeout=5, outage_s=120,
                          label="diseno")
    assert recovery <= 60


@pytest.mark.evidence("T-INT-02", code_refs=INT_REFS, related=INT_RELATED,
                      expected="La clave estable evita efectos duplicados; el lease obsoleto no confirma")
def test_worker_crash_after_accepted_send(api, fresh_db, mocks, evidence):
    assign_order(api)
    store = SqlDeliveryStore(fresh_db)
    notifier = HttpNotificationAdapter(f"{MOCKS_URL}/x-notify", 2)
    clock = SystemClock()
    # Worker A reclama con lease corto, envía el primero y "se cae" antes de registrar el resultado.
    claimed = store.claim(clock.now(), lease_seconds=1, batch=10)
    first = claimed[0]
    notifier.send(Delivery(first.event_id, first.event_type, first.recipient_ref, first.channel,
                           {"eventId": first.event_id}))
    time.sleep(1.2)  # vence el lease
    # Worker B recupera todo lo reclamable (incluida la entrega ya aceptada) con la MISMA clave de entrega.
    report = DeliveryService(store, notifier, clock, DeliverySettings()).run_once()
    stale_confirm = store.mark_delivered(first.event_id, first.lease_token, clock.now())
    received = mocks.get("/x-notify/messages").json()
    final = scalar(fresh_db, "SELECT delivery_state || '/' || attempt_count FROM outbox_event WHERE event_id = :e",
                   e=first.event_id)
    assert stale_confirm is False  # un worker con lease vencido no confirma trabajo ajeno
    assert final == "DELIVERED/2"
    assert received["duplicates"] == 1 and received["unique"] == len(claimed)  # duplicado sin efecto
    evidence.observe("reclamo_tras_lease_vencido", report.claimed)
    evidence.observe("estado_final_entrega", final)
    evidence.observe("confirmacion_con_lease_obsoleto", stale_confirm)
    evidence.observe("x_notify", {k: received[k] for k in ("unique", "duplicates", "attempts")})
    evidence.limit("La caída del worker se simula deteniendo el flujo en proceso, no matando el contenedor "
                   "(eso corresponde a T-REC-01, no implementada).")


@pytest.mark.evidence("T-INT-01")
def test_permanent_error_goes_to_dead_letter_and_order_is_intact(api, fresh_db, mocks, evidence):
    wo = assign_order(api)
    mocks.post("/_control/xnotify", json={"mode": "error400"})
    service = DeliveryService(SqlDeliveryStore(fresh_db), HttpNotificationAdapter(f"{MOCKS_URL}/x-notify", 2),
                              SystemClock(), DeliverySettings())
    report = service.run_once()
    states = {r["delivery_state"] for r in rows(fresh_db, "SELECT delivery_state FROM outbox_event")}
    assert states == {"DEAD_LETTER"} and report.dead == report.claimed
    assert scalar(fresh_db, "SELECT status FROM work_order WHERE work_order_id = :w",
                  w=wo["workOrderId"]) == "ASSIGNED"
    evidence.observe("error_permanente", "DEAD_LETTER; orden ASSIGNED intacta (§7.5)")
