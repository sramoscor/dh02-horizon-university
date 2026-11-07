"""E2-FR-04/05: asignación con AssignmentPolicy (E2-AS-01), reasignación (BR-11) y registro de trabajo."""

import pytest
from sqlalchemy import text

from tests.conftest import counts, insert_request, scalar

pytestmark = pytest.mark.db
REFS = ["poc/backend/application/work_orders.py:apply_assignment",
        "poc/backend/domain/policies.py:AssignmentPolicy",
        "poc/backend/infrastructure/sql.py:SqlWorkLog.technician_candidates"]


def create_order(api, fresh_db, rid="SR-W1", category="PLUMBING", location="CIE-L-101"):
    coord = api.login("coord-03")
    etag = insert_request(fresh_db, rid, "usr-017", location, category=category)
    r = coord.post(f"/requests/{rid}/triage", {"action": "APPROVE_CREATE_WO", "category": category,
                                                "validatedUrgency": "MEDIUM"}, if_match=etag)
    assert r.status_code == 201 and r.json()["workOrder"]["status"] == "CREATED", r.text
    return coord, r.json()["workOrder"]["workOrderId"]


@pytest.mark.evidence("T-FUN-01", code_refs=REFS,
                      related={"requirements": ["E2-FR-04"], "decisions": ["E2-ADR-005"]})
def test_assignment_policy_recommends_least_loaded_specialist(api, fresh_db, evidence):
    with fresh_db.begin() as c:  # segundo plomero sin carga: la política debe preferirlo a TECH-014 (carga 1)
        c.execute(text("INSERT INTO technician_profile VALUES ('TECH-030', 'PLUMBING', true)"))
        c.execute(text("INSERT INTO role_assignment (subject_id, role, scope_type) VALUES "
                       "('TECH-030', 'TECHNICIAN', 'GLOBAL')"))
    coord, wo = create_order(api, fresh_db)
    cands = coord.get(f"/work-orders/{wo}/assignment-candidates").json()
    assert [(c["subjectId"], c["activeLoad"], c["recommended"]) for c in cands] == \
        [("TECH-030", 0, True), ("TECH-014", 1, False)]
    imposed = coord.post(f"/work-orders/{wo}/assignments", {"assigneeId": "TECH-014"}, if_match=coord.wo_etag(wo))
    assert imposed.status_code == 422 and imposed.json()["fields"] == {"reason": "obligatorio"}
    wrong = coord.post(f"/work-orders/{wo}/assignments", {"assigneeId": "TECH-021", "reason": "x"},
                       if_match=coord.wo_etag(wo))
    assert wrong.status_code == 422  # electricista para una orden de plomería
    auto = coord.post(f"/work-orders/{wo}/assignments", {}, if_match=coord.wo_etag(wo))
    assert auto.status_code == 200 and auto.json()["assigneeId"] == "TECH-030", auto.text
    no_reason = coord.post(f"/work-orders/{wo}/assignments", {"assigneeId": "TECH-014"},
                           if_match=coord.wo_etag(wo))
    assert no_reason.status_code == 422  # reasignación exige motivo (§6.6)
    re = coord.post(f"/work-orders/{wo}/assignments", {"assigneeId": "TECH-014", "reason": "conoce el lab"},
                    if_match=coord.wo_etag(wo))
    assert re.status_code == 200 and re.json()["assigneeId"] == "TECH-014"
    history = [a for a in coord.get(f"/work-orders/{wo}").json()["assignments"]]
    assert [(a["assigneeId"], a["isActive"]) for a in history] == [("TECH-030", False), ("TECH-014", True)]
    evidence.observe("politica_asignacion", {"candidatos": cands, "impuesta_sin_motivo": 422,
                                             "especialidad_incorrecta": 422, "automatica": "TECH-030",
                                             "reasignacion_con_motivo": "TECH-014", "historial_conservado": True})


@pytest.mark.evidence("T-FUN-01")
def test_no_eligible_technician_leaves_order_unassigned(api, fresh_db, evidence):
    with fresh_db.begin() as c:
        c.execute(text("UPDATE technician_profile SET available = false"))
    coord, wo = create_order(api, fresh_db, rid="SR-W2")
    before = counts(fresh_db)
    r = coord.post(f"/work-orders/{wo}/assignments", {}, if_match=coord.wo_etag(wo))
    assert r.status_code == 422 and "sin candidatos" in str(r.json()["fields"])
    assert counts(fresh_db) == before
    sup_view = api.login("sup-01").get("/work-orders", params={"status": "CREATED"}).json()
    assert wo in [w["workOrderId"] for w in sup_view]  # visible al supervisor sin asignar (E2-AS-01)
    evidence.observe("sin_candidatos", "422; la orden queda CREATED y visible al supervisor")


@pytest.mark.evidence("T-FUN-01")
def test_supervisor_reassigns_in_progress_and_keeps_state(api, fresh_db, evidence):
    sup, tech = api.login("sup-01"), api.login("TECH-014")
    assert tech.post("/work-orders/WO-3000/start", if_match=tech.wo_etag("WO-3000")).status_code == 200
    coord = api.login("coord-04")
    with fresh_db.begin() as c:
        c.execute(text("INSERT INTO technician_profile VALUES ('TECH-030', 'PLUMBING', true)"))
        c.execute(text("INSERT INTO role_assignment (subject_id, role, scope_type) VALUES "
                       "('TECH-030', 'TECHNICIAN', 'GLOBAL')"))
    denied = coord.post("/work-orders/WO-3000/assignments", {"assigneeId": "TECH-030", "reason": "x"},
                        if_match=coord.wo_etag("WO-3000"))
    assert denied.status_code == 403  # en IN_PROGRESS solo el supervisor reasigna (BR-11)
    r = sup.post("/work-orders/WO-3000/assignments", {"assigneeId": "TECH-030", "reason": "TECH-014 de licencia"},
                 if_match=sup.wo_etag("WO-3000"))
    assert r.status_code == 200 and r.json()["status"] == "IN_PROGRESS" and r.json()["assigneeId"] == "TECH-030"
    old = tech.post("/work-orders/WO-3000/observations", {"note": "ya no estoy asignada"})
    assert old.status_code == 403
    evidence.observe("reasignacion_supervisor", "IN_PROGRESS conservado; técnico anterior pierde acceso (403)")


@pytest.mark.evidence("T-FUN-01")
def test_technician_cannot_record_work_on_closed_order(api, fresh_db, evidence):
    coord = api.login("coord-04")
    assert coord.post("/work-orders/WO-3000/cancellations", {"reason": "duplicado"},
                      if_match=coord.wo_etag("WO-3000")).status_code == 200
    tech = api.login("TECH-014")
    r = tech.post("/work-orders/WO-3000/materials", {"materialName": "cinta", "quantity": 1})
    assert r.status_code in (403, 409)  # cancelada: la asignación se cerró
    assert scalar(fresh_db, "SELECT count(*) FROM material_usage") == 0
    evidence.observe("registro_en_orden_cancelada", r.status_code)
