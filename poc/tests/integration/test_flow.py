"""T-FUN-01: flujo completo con datos sintéticos por la API real, roles distintos y PostgreSQL real (§10.1, F19).

solicitud → triaje → orden → asignación → inicio → bloqueo → resolución → finalización → confirmación → cierre
"""

import pytest

from tests.conftest import counts, rows

pytestmark = pytest.mark.db


def status_of(user, request_id):
    r = user.get(f"/requests/{request_id}/status")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.evidence(
    "T-FUN-01",
    code_refs=["poc/backend/application/requests.py:RequestService",
               "poc/backend/application/triage.py:TriageApplicationService.decide",
               "poc/backend/application/work_orders.py:WorkOrderService",
               "poc/backend/http/routes.py"],
    related={"requirements": ["E2-FR-01", "E2-FR-02", "E2-FR-03", "E2-FR-04", "E2-FR-05", "E2-FR-06", "E2-FR-09"],
             "qualities": ["QS-03"], "decisions": ["E2-ADR-001", "E2-ADR-007"]},
    expected="Cada transición legal con su auditoría; estado visible al solicitante en cada paso",
)
def test_full_vertical_slice(api, fresh_db, evidence):
    student = api.login("usr-017")
    coord = api.login("coord-03")
    tech = api.login("TECH-014")
    rep = api.login("rep-CIE-01")
    trail = []

    # 1. Solicitante: borrador y envío (E2-FR-01). El propietario sale de la sesión.
    r = student.post("/requests", {"locationId": "CIE-L-204", "description": "Fuga en tubería del laboratorio",
                                   "suggestedUrgency": "MEDIUM", "suggestedCategory": "PLUMBING"})
    assert r.status_code == 201, r.text
    sr = r.json()["requestId"]
    assert sr == "SR-2041"  # §2.5 escenario sintético
    r = student.post(f"/requests/{sr}/submissions", if_match=r.headers["ETag"])
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED", r.text
    trail.append(("submitted", status_of(student, sr)))

    # 2. Coordinador: revisar, ver duplicados (SR-2042 es candidato), aprobar y asignar (E2-FR-02/03/04).
    r = coord.post(f"/requests/{sr}/triage-reviews", if_match=coord.request_etag(sr))
    assert r.status_code == 200 and r.json()["status"] == "UNDER_TRIAGE", r.text
    cands = coord.get(f"/requests/{sr}/duplicate-candidates", params={"category": "PLUMBING"}).json()
    assert {"kind": "REQUEST", "id": "SR-2042"} in [{"kind": c["kind"], "id": c["id"]} for c in cands]
    r = coord.post(f"/requests/{sr}/triage", {
        "action": "APPROVE_CREATE_WO", "category": "PLUMBING", "validatedUrgency": "HIGH", "safetyFlag": False,
        "discriminator": "", "assigneeId": "TECH-014", "comments": "Fuga de tubería validada en laboratorio."},
        if_match=coord.request_etag(sr), key="triage-sr2041-001")
    assert r.status_code == 201, r.text
    body = r.json()
    wo = body["workOrder"]["workOrderId"]
    assert wo == "WO-3001" and body["requestStatus"] == "APPROVED"
    assert body["workOrder"]["status"] == "ASSIGNED" and body["workOrder"]["assigneeId"] == "TECH-014"
    assert r.headers["Location"] == f"/api/v1/work-orders/{wo}" and r.headers["ETag"].startswith('"work-order-')
    trail.append(("approved+assigned", status_of(student, sr)))

    # 3. Técnico: iniciar, bloquear por MATERIALS, resolver, materiales, observación, finalizar (E2-FR-05).
    assert tech.post(f"/work-orders/{wo}/start", if_match=tech.wo_etag(wo)).json()["status"] == "IN_PROGRESS"
    r = tech.post(f"/work-orders/{wo}/blocks", {"blockType": "MATERIALS", "detail": "Falta una válvula"},
                  if_match=tech.wo_etag(wo))
    assert r.status_code == 200 and r.json()["status"] == "BLOCKED" and r.json()["slaPaused"] is True, r.text
    block_id = r.json()["blockId"]
    trail.append(("blocked", status_of(student, sr)))
    r = tech.post(f"/work-orders/{wo}/blocks/{block_id}/resolution", if_match=tech.wo_etag(wo))
    assert r.status_code == 200 and r.json()["status"] == "IN_PROGRESS", r.text
    assert tech.post(f"/work-orders/{wo}/materials", {"materialName": "Válvula 1/2\"", "quantity": 1}).status_code == 201
    r = tech.post(f"/work-orders/{wo}/completions", if_match=tech.wo_etag(wo))
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"  # guarda: sin observaciones
    assert tech.post(f"/work-orders/{wo}/observations",
                     {"note": "Se cambió la válvula", "attachmentRef": "sim://foto-001"}).status_code == 201
    r = tech.post(f"/work-orders/{wo}/completions", if_match=tech.wo_etag(wo))
    assert r.status_code == 200 and r.json()["status"] == "PENDING_CONFIRMATION", r.text
    trail.append(("pending_confirmation", status_of(student, sr)))

    # 4. Representante del edificio confirma (E2-FR-06) → cierre.
    r = rep.post(f"/work-orders/{wo}/confirmations", {"outcome": "CONFIRMED"}, if_match=rep.wo_etag(wo))
    assert r.status_code == 200 and r.json()["status"] == "CLOSED", r.text
    final = status_of(student, sr)
    trail.append(("closed", final))
    assert final["request"]["status"] == "APPROVED" and final["workOrder"]["status"] == "CLOSED"

    # 5. Auditoría: cada mutación aceptada dejó su AuditEvent (BR-08).
    history = coord.get(f"/work-orders/{wo}/history").json()
    actions = [h["action"] for h in history]
    expected_actions = ["REQUEST_DRAFTED", "REQUEST_SUBMITTED", "TRIAGE_STARTED", "WORK_ORDER_CREATED",
                        "WORK_ORDER_ASSIGNMENT", "TRIAGE_APPROVE_CREATE_WO", "WORK_ORDER_START",
                        "WORK_ORDER_BLOCK", "WORK_ORDER_RESOLVE_BLOCK", "MATERIAL_RECORDED",
                        "OBSERVATION_RECORDED", "WORK_ORDER_COMPLETE", "WORK_ORDER_CONFIRMED"]
    assert actions == expected_actions, actions
    statuses = [(h["action"], (h["before"] or {}).get("status"), (h["after"] or {}).get("status")) for h in history]
    events = [r["event_type"] for r in rows(fresh_db, "SELECT event_type FROM outbox_event WHERE request_id = :r "
                                                      "ORDER BY created_at", r=sr)]
    for step, st in trail:
        evidence.observe(f"estado_combinado[{step}]",
                         f"solicitud={st['request']['status']} · orden={(st['workOrder'] or {}).get('status')} · "
                         f"notificación={(st['notification'] or {}).get('state')}")
    evidence.observe("acciones_auditadas", actions)
    evidence.observe("transiciones", [f"{a}: {b}→{c}" for a, b, c in statuses])
    evidence.observe("eventos_outbox", events)
    evidence.observe("conteos_finales", counts(fresh_db))
    print("AUDIT", *statuses, sep="\n  ")
    print("OUTBOX", events)


@pytest.mark.evidence("T-FUN-01")
def test_reopen_returns_order_to_flow(api, fresh_db, evidence):
    """Reapertura por el solicitante con motivo y nueva asignación (§6.6 REOPENED → ASSIGNED)."""
    coord, tech, owner = api.login("coord-04"), api.login("TECH-014"), api.login("usr-017")
    wo = "WO-3000"  # semilla: SR-2040 de usr-017, ASSIGNED a TECH-014
    tech.post(f"/work-orders/{wo}/start", if_match=tech.wo_etag(wo))
    tech.post(f"/work-orders/{wo}/observations", {"note": "Ajuste de grifería"})
    assert tech.post(f"/work-orders/{wo}/completions", if_match=tech.wo_etag(wo)).status_code == 200
    r = owner.post(f"/work-orders/{wo}/confirmations", {"outcome": "REOPEN"}, if_match=owner.wo_etag(wo))
    assert r.status_code == 422  # BR-12/§6.6: reabrir exige motivo
    r = owner.post(f"/work-orders/{wo}/confirmations", {"outcome": "REOPEN", "reason": "Sigue goteando"},
                   if_match=owner.wo_etag(wo))
    assert r.status_code == 200 and r.json()["status"] == "REOPENED", r.text
    r = coord.post(f"/work-orders/{wo}/assignments", {"assigneeId": "TECH-014"}, if_match=coord.wo_etag(wo))
    assert r.status_code == 200 and r.json()["status"] == "ASSIGNED", r.text
    evidence.observe("reapertura", "PENDING_CONFIRMATION → REOPENED (motivo) → ASSIGNED")
