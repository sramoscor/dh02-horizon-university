"""QS-01 · Unicidad de la orden bajo concurrencia (E2-ADR-003; V-SEQ-02; T-CON-01, T-CON-02).

Se prueba con conexiones independientes contra PostgreSQL real, a través de la API y con dos sesiones de
coordinador distintas. Cuál gana no está especificado; lo que se garantiza es que no existan dos órdenes
activas indebidas.
"""

import threading
import time

import pytest

from backend.domain.enums import Category, Urgency, WorkOrderStatus
from backend.domain.errors import DuplicateCandidateExists
from backend.domain.model import WorkOrder
from tests.conftest import DelayAt, insert_location, insert_request, rows, scalar

pytestmark = pytest.mark.db
TRIALS = 100
APPROVE = {"action": "APPROVE_CREATE_WO", "category": "ELECTRICAL", "validatedUrgency": "HIGH", "discriminator": ""}


def race(calls):
    """Ejecuta las llamadas en hilos liberados a la vez por una barrera; devuelve (respuesta, duración)."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i, fn):
        barrier.wait()
        t0 = time.perf_counter()
        r = fn()
        results[i] = (r, time.perf_counter() - t0, t0, time.perf_counter())

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


CON01 = dict(
    code_refs=["poc/migrations/versions/0001_initial_schema.py:uq_work_order_active_slot",
               "poc/migrations/versions/0001_initial_schema.py:uq_work_order_request",
               "poc/backend/infrastructure/sql.py:SqlWorkOrders.add",
               "poc/backend/application/triage.py:TriageApplicationService.decide"],
    related={"requirements": ["E2-FR-03"], "qualities": ["QS-01"], "decisions": ["E2-ADR-003"]},
)


@pytest.mark.slow
@pytest.mark.evidence("T-CON-01", **CON01,
                      expected="Un éxito y un 409 por ensayo sano; ningún par activo indebido; "
                               "efectos solo del ganador")
def test_two_coordinators_race_distinct_requests(app_factory, fresh_db, evidence):
    api = app_factory(faults=DelayAt("after_work_order", 0.05))
    a, b = api.login("coord-03"), api.login("coord-04")
    outcomes, contended, violations = [], 0, []
    for i in range(TRIALS):
        loc = f"RACE-{i:03d}"
        insert_location(fresh_db, loc)
        ra, rb = f"SR-R{i:03d}A", f"SR-R{i:03d}B"
        ea, eb = insert_request(fresh_db, ra, "usr-017", loc), insert_request(fresh_db, rb, "usr-052", loc)
        ka, kb = f"race-{i}-a", f"race-{i}-b"
        (r1, d1, s1, e1), (r2, d2, s2, e2) = race([
            lambda ra=ra, ea=ea, ka=ka: a.post(f"/requests/{ra}/triage", APPROVE, if_match=ea, key=ka),
            lambda rb=rb, eb=eb, kb=kb: b.post(f"/requests/{rb}/triage", APPROVE, if_match=eb, key=kb),
        ])
        codes = sorted([r1.status_code, r2.status_code])
        winner_r, loser_r = (r1, r2) if r1.status_code == 201 else (r2, r1)
        winner_req, loser_req = (ra, rb) if winner_r is r1 else (rb, ra)
        loser_key = kb if winner_r is r1 else ka
        active = scalar(fresh_db, "SELECT count(*) FROM work_order WHERE location_id = :l AND status IN "
                                  "('CREATED','ASSIGNED','IN_PROGRESS','BLOCKED','PENDING_CONFIRMATION','REOPENED')",
                        l=loc)
        loser_effects = {
            "status": scalar(fresh_db, "SELECT status || '/v' || row_version FROM maintenance_request "
                                       "WHERE request_id = :r", r=loser_req),
            "triage": scalar(fresh_db, "SELECT count(*) FROM triage_record WHERE request_id = :r", r=loser_req),
            "audit": scalar(fresh_db, "SELECT count(*) FROM audit_event WHERE entity_id = :r", r=loser_req),
            "outbox": scalar(fresh_db, "SELECT count(*) FROM outbox_event WHERE request_id = :r", r=loser_req),
            "receipt": scalar(fresh_db, "SELECT count(*) FROM command_receipt WHERE idempotency_key = :k",
                              k=loser_key),
        }
        winner_ok = (
            scalar(fresh_db, "SELECT count(*) FROM audit_event WHERE entity_id = :r AND action = "
                             "'TRIAGE_APPROVE_CREATE_WO'", r=winner_req) == 1
            and scalar(fresh_db, "SELECT count(*) FROM outbox_event WHERE request_id = :r", r=winner_req) == 1
        )
        if s1 < e2 and s2 < e1 and max(d1, d2) >= 0.045:
            contended += 1  # ambas transacciones coexistieron y la perdedora esperó al índice
        trial_ok = (
            codes == [201, 409]
            and loser_r.json().get("code") == "DUPLICATE_CANDIDATE_EXISTS"
            and loser_r.json().get("existingWorkOrderId") == winner_r.json()["workOrder"]["workOrderId"]
            and active == 1
            and loser_effects == {"status": "UNDER_TRIAGE/v3", "triage": 0, "audit": 0, "outbox": 0, "receipt": 0}
            and winner_ok
        )
        outcomes.append(trial_ok)
        if not trial_ok:
            violations.append({"trial": i, "codes": codes, "active": active, "loser": loser_effects,
                               "loser_body": loser_r.text[:300]})
    pairs = scalar(fresh_db, "SELECT count(*) FROM (SELECT location_id, category, discriminator FROM work_order "
                             "WHERE status IN ('CREATED','ASSIGNED','IN_PROGRESS','BLOCKED','PENDING_CONFIRMATION',"
                             "'REOPENED') GROUP BY 1,2,3 HAVING count(*) > 1) d")
    evidence.set_execution(independent_connections=True, trials=TRIALS,
                           mechanism="2 sesiones (coord-03, coord-04) · 2 hilos · barrera · API en proceso · "
                                     "pool SQLAlchemy (una conexión por transacción)")
    evidence.observe("ensayos_correctos", f"{sum(outcomes)}/{TRIALS}")
    evidence.observe("ensayos_con_contencion_real", f"{contended}/{TRIALS}")
    evidence.observe("pares_activos_indebidos", pairs)
    evidence.observe("violaciones", violations[:5])
    evidence.limit("La API corre en proceso (TestClient) con dos clientes y sesiones independientes; la escena del "
                   "video repite la carrera contra el contenedor C-API real desde el Panel de demostración.")
    evidence.limit("Se retiene 50 ms la transacción ganadora tras insertar la orden (DelayAt) para forzar que la "
                   "perdedora llegue al índice mientras la ganadora sigue abierta; sin esa espera la carrera puede "
                   "serializarse sola y no ejercitar el bloqueo.")
    print(f"correctos={sum(outcomes)}/{TRIALS} contencion={contended} pares_indebidos={pairs}")
    assert not violations, violations[:3]
    assert pairs == 0


@pytest.mark.slow
@pytest.mark.evidence("T-CON-01", **CON01)
def test_two_coordinators_race_same_request(app_factory, fresh_db, evidence):
    api = app_factory(faults=DelayAt("after_work_order", 0.02))
    a, b = api.login("coord-03"), api.login("coord-04")
    insert_location(fresh_db, "RACE-SAME")
    bad = []
    for i in range(TRIALS):
        rid = f"SR-S{i:03d}"
        etag = insert_request(fresh_db, rid, "usr-017", "RACE-SAME", category="GENERAL")
        body = {**APPROVE, "category": "GENERAL", "discriminator": f"d{i}"}
        (r1, *_), (r2, *_) = race([
            lambda rid=rid, body=body, etag=etag, i=i: a.post(f"/requests/{rid}/triage", body, if_match=etag,
                                                              key=f"same-{i}-a"),
            lambda rid=rid, body=body, etag=etag, i=i: b.post(f"/requests/{rid}/triage", body, if_match=etag,
                                                              key=f"same-{i}-b"),
        ])
        orders = scalar(fresh_db, "SELECT count(*) FROM work_order WHERE request_id = :r", r=rid)
        codes = sorted([r1.status_code, r2.status_code])
        if orders != 1 or codes[0] != 201 or codes[1] not in (409, 412):
            bad.append({"trial": i, "codes": codes, "orders": orders})
    evidence.observe("variante_misma_solicitud", f"{TRIALS - len(bad)}/{TRIALS} ensayos con exactamente 1 orden")
    evidence.observe("codigo_del_perdedor", "412 PRECONDITION_FAILED (versión obsoleta tras el bloqueo FOR UPDATE)"
                     " o 409; nunca una segunda orden")
    assert not bad, bad[:3]


def approve(user, rid, etag, **extra):
    return user.post(f"/requests/{rid}/triage", {**APPROVE, **extra}, if_match=etag)


@pytest.mark.evidence(
    "T-CON-02", **{**CON01, "code_refs": CON01["code_refs"] + ["poc/backend/http/problems.py"]},
    expected="Comportamiento exacto en los límites; errores de restricción traducidos a errores de negocio")
def test_boundaries(api, fresh_db, app_factory, evidence):
    coord, coord_sur = api.login("coord-03"), api.login("coord-04")
    insert_location(fresh_db, "EDGE-1")
    insert_location(fresh_db, "EDGE-2")

    # a) discriminator distinto ⇒ problema distinto declarado ⇒ ambas órdenes
    e1 = insert_request(fresh_db, "SR-E1", "usr-017", "EDGE-1")
    e2 = insert_request(fresh_db, "SR-E2", "usr-052", "EDGE-1")
    assert approve(coord, "SR-E1", e1).status_code == 201
    r = approve(coord, "SR-E2", e2)
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_CANDIDATE_EXISTS"
    body409 = r.json()
    r = approve(coord, "SR-E2", e2, discriminator="pared-norte")
    assert r.status_code == 201, r.text
    evidence.observe("discriminador_distinto", "409 sin discriminador → 201 con discriminator='pared-norte'")

    # b) ubicaciones distintas ⇒ sin conflicto
    e3 = insert_request(fresh_db, "SR-E3", "usr-017", "EDGE-2")
    assert approve(coord, "SR-E3", e3).status_code == 201
    evidence.observe("ubicacion_distinta", "201")

    # c) orden previa cerrada/cancelada ⇒ ya no es activa ⇒ se permite una nueva
    e4 = insert_request(fresh_db, "SR-E4", "usr-017", "ING-B-110", category="PLUMBING")
    r = coord_sur.post("/requests/SR-E4/triage", {**APPROVE, "category": "PLUMBING"}, if_match=e4)
    assert r.status_code == 409 and r.json()["existingWorkOrderId"] == "WO-3000"
    assert coord_sur.post("/work-orders/WO-3000/cancellations", {"reason": "duplicado administrativo"},
                          if_match=coord_sur.wo_etag("WO-3000")).status_code == 200
    r = coord_sur.post("/requests/SR-E4/triage", {**APPROVE, "category": "PLUMBING"}, if_match=e4)
    assert r.status_code == 201, r.text
    evidence.observe("orden_previa_cancelada", "409 (WO-3000 activa) → cancelar → 201")

    # d) solicitud repetida: ya aprobada ⇒ transición inválida; y en BD, UNIQUE(request_id)
    r = approve(coord, "SR-E1", coord.request_etag("SR-E1"))
    assert r.status_code == 409 and r.json()["code"] == "INVALID_STATE_TRANSITION"
    existing = scalar(fresh_db, "SELECT work_order_id FROM work_order WHERE request_id = 'SR-E1'")
    uow = api.container.uow_factory()
    with uow:
        wo = uow.work_orders.get(existing)
        clone = WorkOrder(**{**wo.to_dict(), "work_order_id": "WO-CLONE", "discriminator": "otro",
                             "category": Category(wo.category), "urgency": Urgency(wo.urgency),
                             "status": WorkOrderStatus.CREATED})
        with pytest.raises(DuplicateCandidateExists) as exc:
            uow.work_orders.add(clone)
    assert exc.value.existing_work_order_id == existing
    evidence.observe("solicitud_repetida", "API: 409 INVALID_STATE_TRANSITION · BD: uq_work_order_request → "
                                           f"DuplicateCandidateExists({existing})")

    # e) el error es de negocio (problem-details), nunca técnico
    assert set(body409) >= {"type", "title", "status", "code", "detail", "instance", "existingWorkOrderId",
                            "correlationId"}
    assert "Traceback" not in str(body409) and "IntegrityError" not in str(body409)
    evidence.observe("cuerpo_409", body409)
    active_pairs = rows(fresh_db, "SELECT location_id, category, discriminator, count(*) n FROM work_order WHERE "
                                  "status NOT IN ('CLOSED','ADMIN_CLOSED','CANCELLED') GROUP BY 1,2,3 "
                                  "HAVING count(*) > 1")
    assert active_pairs == []
