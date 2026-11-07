"""QS-04 · Denegación de acciones no autorizadas y QS-03 · auditoría (E2-ADR-006/007; §9.1; T-SEC-01..04, T-AUD-01).

La API se invoca directamente, sin interfaz. Cada caso negativo verifica 0 escrituras de negocio.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import counts, insert_request, rows, scalar

pytestmark = pytest.mark.db
SEC_REFS = ["poc/backend/application/authorization.py:AuthorizationService",
            "poc/backend/domain/transitions.py:plan_transition", "poc/backend/http/deps.py"]
SEC_RELATED = {"requirements": ["E2-FR-11"], "qualities": ["QS-04"], "decisions": ["E2-ADR-006"]}


@pytest.fixture
def world(api, fresh_db):
    """WO-3000 (semilla: ING-B-110, usr-017, TECH-014) y WO-3001 (CIE-L-204, usr-052, TECH-014)."""
    coord = api.login("coord-03")
    r = coord.post("/requests/SR-2042/triage-reviews", if_match=coord.request_etag("SR-2042"))
    assert r.status_code == 200, r.text
    r = coord.post("/requests/SR-2042/triage", {"action": "APPROVE_CREATE_WO", "category": "PLUMBING",
                                                 "validatedUrgency": "HIGH", "assigneeId": "TECH-014"},
                   if_match=coord.request_etag("SR-2042"))
    assert r.status_code == 201, r.text
    insert_request(fresh_db, "SR-SUR-1", "usr-017", "ING-B-305", category="GENERAL")
    users = {s: api.login(s) for s in ["usr-017", "usr-052", "coord-03", "coord-04", "TECH-014", "TECH-021",
                                        "rep-CIE-01", "sup-01", "ops-01", "dir-01"]}
    return users


def etag_wo(fresh_db, wo):
    return f'"work-order-{wo}-v{scalar(fresh_db, "SELECT row_version FROM work_order WHERE work_order_id=:w", w=wo)}"'


def etag_req(fresh_db, rid):
    v = scalar(fresh_db, "SELECT row_version FROM maintenance_request WHERE request_id=:r", r=rid)
    return f'"request-{rid}-v{v}"'


def call(users, fresh_db, actor, method, path, body=None, etag=None):
    u = users[actor]
    if method == "GET":
        return u.get(path)
    return u.post(path, body or {}, if_match=etag(fresh_db) if etag else None)


TRIAGE = {"action": "APPROVE_CREATE_WO", "category": "GENERAL", "validatedUrgency": "LOW"}
# (caso, actor, método, ruta, cuerpo, etag, estado esperado)
NEGATIVE = [
    ("solicitante lee solicitud ajena", "usr-052", "GET", "/requests/SR-2040", None, None, 403),
    ("solicitante lee orden ajena", "usr-017", "GET", "/work-orders/WO-3001", None, None, 403),
    ("técnico lee orden no asignada", "TECH-021", "GET", "/work-orders/WO-3000", None, None, 403),
    ("técnico inicia orden no asignada", "TECH-021", "POST", "/work-orders/WO-3000/start", None,
     lambda db: etag_wo(db, "WO-3000"), 403),
    ("coordinador fuera de alcance triagea", "coord-03", "POST", "/requests/SR-SUR-1/triage", TRIAGE,
     lambda db: etag_req(db, "SR-SUR-1"), 403),
    ("solicitante triagea", "usr-017", "POST", "/requests/SR-2043/triage", TRIAGE,
     lambda db: etag_req(db, "SR-2043"), 403),
    ("representante triagea", "rep-CIE-01", "POST", "/requests/SR-2043/triage", TRIAGE,
     lambda db: etag_req(db, "SR-2043"), 403),
    ("técnico asigna", "TECH-014", "POST", "/work-orders/WO-3001/assignments", {"assigneeId": "TECH-014"},
     lambda db: etag_wo(db, "WO-3001"), 403),
    ("coordinador confirma", "coord-03", "POST", "/work-orders/WO-3001/confirmations", {"outcome": "CONFIRMED"},
     lambda db: etag_wo(db, "WO-3001"), 403),
    ("coordinador cierra administrativamente", "coord-03", "POST", "/work-orders/WO-3001/admin-closures",
     {"reason": "x"}, lambda db: etag_wo(db, "WO-3001"), 403),
    ("representante lee orden de otro edificio", "rep-CIE-01", "GET", "/work-orders/WO-3000", None, None, 403),
    ("solicitante lee historial", "usr-017", "GET", "/work-orders/WO-3000/history", None, None, 403),
    ("técnico ajeno lee historial", "TECH-021", "GET", "/work-orders/WO-3000/history", None, None, 403),
    ("solicitante ve entregas", "usr-017", "GET", "/deliveries", None, None, 403),
    ("director inicia trabajo", "dir-01", "POST", "/work-orders/WO-3000/start", None,
     lambda db: etag_wo(db, "WO-3000"), 403),
    ("operaciones lee detalle de orden", "ops-01", "GET", "/work-orders/WO-3000", None, None, 403),
    ("solicitante registra materiales", "usr-017", "POST", "/work-orders/WO-3000/materials",
     {"materialName": "x", "quantity": 1}, None, 403),
    ("técnico ve carga de técnicos", "TECH-014", "GET", "/technicians/workload", None, None, 403),
]
POSITIVE = [
    ("solicitante lee su solicitud", "usr-017", "GET", "/requests/SR-2040", 200),
    ("técnico asignado lee su orden", "TECH-014", "GET", "/work-orders/WO-3000", 200),
    ("representante lee orden de su edificio", "rep-CIE-01", "GET", "/work-orders/WO-3001", 200),
    ("coordinador con alcance lee historial", "coord-04", "GET", "/work-orders/WO-3000/history", 200),
    ("director lee historial", "dir-01", "GET", "/work-orders/WO-3000/history", 200),
    ("supervisor ve entregas", "sup-01", "GET", "/deliveries", 200),
    ("operaciones ve entregas", "ops-01", "GET", "/deliveries", 200),
    ("supervisor ve carga", "sup-01", "GET", "/technicians/workload", 200),
]


@pytest.mark.evidence("T-SEC-01", code_refs=SEC_REFS, related=SEC_RELATED,
                      expected="Solo acciones autorizadas; sin fuga de campos; 0 escrituras en los casos negativos; "
                               "la matriz incluye controles positivos")
def test_authorization_matrix(world, fresh_db, api, evidence):
    results = {}
    for case, actor, method, path, body, etag, expected in NEGATIVE:
        before = counts(fresh_db)
        r = call(world, fresh_db, actor, method, path, body, etag)
        after = counts(fresh_db)
        ok = r.status_code == expected and r.json().get("code") == "ACTION_FORBIDDEN" and after == before
        leaked = any(k in r.json() for k in ("description", "requesterId", "observations", "assigneeId"))
        results[case] = f"{r.status_code} {r.json().get('code')} · escrituras={sum(after.values()) - sum(before.values())}"
        assert ok and not leaked, (case, r.status_code, r.text)
    for case, actor, method, path, expected in POSITIVE:
        r = call(world, fresh_db, actor, method, path)
        results[f"[control +] {case}"] = str(r.status_code)
        assert r.status_code == expected, (case, r.text)
    rep_view = world["rep-CIE-01"].get("/work-orders/WO-3001").json()
    assert "observations" not in rep_view and "materials" not in rep_view and "safetyFlag" not in rep_view
    anon = TestClient(api.app, raise_server_exceptions=False).get("/api/v1/work-orders/WO-3000")
    assert anon.status_code == 401 and anon.json()["code"] == "SESSION_INVALID"
    results["sin sesión lee orden"] = f"{anon.status_code} {anon.json()['code']}"
    # Positivo con escritura: el coordinador con alcance sí triagea (control legítimo de la matriz).
    r = world["coord-03"].post("/requests/SR-2043/triage", {**TRIAGE, "category": "ELECTRICAL"},
                               if_match=etag_req(fresh_db, "SR-2043"))
    assert r.status_code == 201
    results["[control +] coordinador con alcance triagea"] = "201"
    evidence.observe("matriz", results)
    evidence.observe("casos_negativos", len(NEGATIVE) + 1)
    evidence.observe("controles_positivos", len(POSITIVE) + 1)
    evidence.limit("Se responde 403 (no 404) a lecturas fuera de alcance: decisión D-08 (divergences.md).")


@pytest.mark.evidence("T-SEC-02", code_refs=SEC_REFS, related=SEC_RELATED,
                      expected="Triaje y confirmación de la propia solicitud/trabajo y rol inactivo: denegado; "
                               "sin escrituras")
def test_separation_of_duties_and_inactive_role(world, fresh_db, api, evidence):
    coord = world["coord-03"]
    before = counts(fresh_db)
    r1 = coord.post("/requests/SR-2045/triage-reviews", if_match=etag_req(fresh_db, "SR-2045"))
    r2 = coord.post("/requests/SR-2045/triage", TRIAGE, if_match=etag_req(fresh_db, "SR-2045"))
    inactive = api.login("coord-99")
    assert inactive.roles == []  # identidad válida, sin concesiones activas: identidad ≠ autorización
    r3 = inactive.post("/requests/SR-2043/triage", TRIAGE, if_match=etag_req(fresh_db, "SR-2043"))
    r4 = inactive.post("/requests", {"locationId": "ADM-201", "description": "x", "suggestedUrgency": "LOW"})
    # El técnico asignado no confirma su propio trabajo aunque sea el solicitante (hallazgo H-03).
    tech = world["TECH-014"]
    with fresh_db.begin() as c:
        c.execute(text("UPDATE maintenance_request SET requester_id = 'TECH-014' WHERE request_id = 'SR-2040'"))
        c.execute(text("INSERT INTO role_assignment (subject_id, role, scope_type) VALUES "
                       "('TECH-014', 'REQUESTER', 'OWN')"))
    tech.post("/work-orders/WO-3000/start", if_match=etag_wo(fresh_db, "WO-3000"))
    tech.post("/work-orders/WO-3000/observations", {"note": "listo"})
    assert tech.post("/work-orders/WO-3000/completions", if_match=etag_wo(fresh_db, "WO-3000")).status_code == 200
    mid = counts(fresh_db)
    r5 = tech.post("/work-orders/WO-3000/confirmations", {"outcome": "CONFIRMED"},
                   if_match=etag_wo(fresh_db, "WO-3000"))
    for r in (r1, r2, r3, r4, r5):
        assert r.status_code == 403 and r.json()["code"] == "ACTION_FORBIDDEN", r.text
    assert counts(fresh_db)["completion_confirmation"] == mid["completion_confirmation"]
    assert scalar(fresh_db, "SELECT status FROM maintenance_request WHERE request_id='SR-2045'") == "SUBMITTED"
    writes_by_denied = sum(counts(fresh_db)[t] - mid[t] for t in mid)
    assert writes_by_denied == 0
    evidence.observe("coordinador_triagea_su_solicitud", [r1.status_code, r2.status_code])
    evidence.observe("rol_inactivo", {"roles": inactive.roles, "triage": r3.status_code, "crear": r4.status_code})
    evidence.observe("tecnico_confirma_su_trabajo", r5.status_code)
    evidence.observe("escrituras_de_los_denegados", 0)
    assert before["audit_event"] <= mid["audit_event"]


@pytest.mark.evidence("T-SEC-03", code_refs=SEC_REFS + ["poc/backend/application/requests.py:create_draft"],
                      related=SEC_RELATED, expected="Propietario, estado y rol falsificados en el cuerpo: ignorados")
def test_forged_owner_state_and_role_are_ignored(world, fresh_db, evidence):
    student = world["usr-017"]
    r = student.post("/requests", {"locationId": "ADM-201", "description": "Luz parpadea", "suggestedUrgency": "LOW",
                                   "requesterId": "usr-052", "status": "APPROVED", "role": "SUPERVISOR",
                                   "rowVersion": 99}, headers={"X-Role": "SUPERVISOR"})
    assert r.status_code == 201
    body = r.json()
    assert body["requesterId"] == "usr-017" and body["status"] == "DRAFT" and body["rowVersion"] == 1
    r2 = student.post("/requests/SR-2043/triage", {**TRIAGE, "coordinatorId": "coord-03"},
                      if_match=etag_req(fresh_db, "SR-2043"), headers={"X-Role": "COORDINATOR"})
    assert r2.status_code == 403
    coord = world["coord-03"]
    r3 = coord.post("/requests/SR-2043/triage", {**TRIAGE, "category": "ELECTRICAL", "coordinatorId": "sup-01"},
                    if_match=etag_req(fresh_db, "SR-2043"))
    assert r3.status_code == 201
    assert scalar(fresh_db, "SELECT coordinator_id FROM triage_record WHERE request_id='SR-2043'") == "coord-03"
    evidence.observe("cuerpo_falsificado", {"requesterId": body["requesterId"], "status": body["status"]})
    evidence.observe("cabecera_X-Role", r2.status_code)
    evidence.observe("coordinatorId_falso_ignorado", "triage_record.coordinator_id = coord-03")


@pytest.mark.evidence("T-SEC-04", code_refs=SEC_REFS, related=SEC_RELATED,
                      expected="CSRF ausente o inválido, sesión expirada o cerrada: falla documentada, sin efecto")
def test_csrf_and_session(world, fresh_db, api, clock, evidence):
    coord = world["coord-03"]
    before = counts(fresh_db)
    no_csrf = coord.post("/requests/SR-2043/triage", TRIAGE, if_match=etag_req(fresh_db, "SR-2043"), csrf=False)
    bad_csrf = coord.post("/requests/SR-2043/triage", TRIAGE, if_match=etag_req(fresh_db, "SR-2043"), csrf=False,
                          headers={"X-CSRF-Token": "forjado"})
    clock.offset = timedelta(hours=9)  # TTL de sesión: 8 h
    expired = coord.post("/requests/SR-2043/triage", TRIAGE, if_match=etag_req(fresh_db, "SR-2043"))
    clock.offset = timedelta()
    other = api.login("coord-04")
    other.client.delete("/api/v1/auth/session", headers={"X-CSRF-Token": other.csrf})
    after_logout = other.get("/requests?view=triage")
    assert (no_csrf.status_code, no_csrf.json()["code"]) == (400, "REQUIRED_HEADER_MISSING")
    assert (bad_csrf.status_code, bad_csrf.json()["code"]) == (401, "SESSION_INVALID")
    assert (expired.status_code, expired.json()["code"]) == (401, "SESSION_INVALID")
    assert after_logout.status_code == 401
    assert counts(fresh_db) == before
    login = TestClient(api.app).post("/api/v1/auth/sessions", json={"assertion": "usr-017"})
    flags = login.headers["set-cookie"].split(";", 1)[1].strip()
    assert "HttpOnly" in flags and "SameSite=strict" in flags
    evidence.observe("sin_csrf", no_csrf.json()["code"])
    evidence.observe("csrf_invalido", bad_csrf.json()["code"])
    evidence.observe("sesion_expirada", expired.json()["code"])
    evidence.observe("tras_logout", after_logout.status_code)
    evidence.observe("cookie", flags)
    evidence.limit("Con Streamlit la cookie vive en el servidor de C-WEB, no en un navegador; CSRF y HttpOnly se "
                   "verifican contra C-API directamente (E2-ADR-009).")


@pytest.mark.evidence(
    "T-AUD-01",
    code_refs=["poc/backend/application/commands.py:record_audit", "poc/backend/infrastructure/sql.py:SqlAudit",
               "poc/migrations/versions/0001_initial_schema.py:audit_event_append_only"],
    related={"requirements": ["E2-FR-09"], "qualities": ["QS-03"], "decisions": ["E2-ADR-007"]},
    expected="Campos requeridos presentes; sin éxito falso tras reversión; acceso controlado; solo anexado",
)
def test_audit_trail(world, fresh_db, evidence):
    coord, tech, sup = world["coord-03"], world["TECH-014"], world["sup-01"]
    corr = {"X-Correlation-Id": "corr-demo-3001"}
    tech.post("/work-orders/WO-3001/start", if_match=etag_wo(fresh_db, "WO-3001"), headers=corr)
    tech.post("/work-orders/WO-3001/blocks", {"blockType": "MATERIALS", "detail": "válvula"},
              if_match=etag_wo(fresh_db, "WO-3001"), headers=corr)
    r = sup.post("/work-orders/WO-3001/assignments", {"assigneeId": "TECH-014", "reason": "mismo"},
                 if_match=etag_wo(fresh_db, "WO-3001"))
    assert r.status_code == 422  # reasignación al mismo técnico: rechazada → sin auditoría de éxito
    audit_before = scalar(fresh_db, "SELECT count(*) FROM audit_event")
    # Operación revertida por conflicto: sin registro de éxito.
    insert_request(fresh_db, "SR-AUD-1", "usr-017", "CIE-L-204", category="PLUMBING")
    dup = coord.post("/requests/SR-AUD-1/triage", {"action": "APPROVE_CREATE_WO", "category": "PLUMBING",
                                                    "validatedUrgency": "LOW"}, if_match=etag_req(fresh_db, "SR-AUD-1"))
    assert dup.status_code == 409
    assert scalar(fresh_db, "SELECT count(*) FROM audit_event") == audit_before
    history = coord.get("/work-orders/WO-3001/history").json()
    required = {"actorId", "actorRole", "action", "occurredAt", "correlationId", "after"}
    missing = [h["action"] for h in history if any(h.get(k) in (None, "") for k in required)]
    assert not missing, missing
    transitions = [h for h in history if (h["before"] or {}).get("status") != (h["after"] or {}).get("status")
                   and h["before"]]
    assert all(h["before"].get("status") and h["after"].get("status") for h in transitions)
    assert [h["seq"] for h in history] == sorted(h["seq"] for h in history)
    assert {h["correlationId"] for h in history if h["action"] in ("WORK_ORDER_START", "WORK_ORDER_BLOCK")} == \
        {"corr-demo-3001"}
    outbox_corr = rows(fresh_db, "SELECT DISTINCT correlation_id FROM outbox_event WHERE event_type = "
                                 "'WorkOrderBlocked'")
    assert outbox_corr == [{"correlation_id": "corr-demo-3001"}]
    with pytest.raises(Exception, match="solo anexado"), fresh_db.begin() as c:
        c.execute(text("UPDATE audit_event SET reason = 'alterado'"))
    with pytest.raises(Exception, match="solo anexado"), fresh_db.begin() as c:
        c.execute(text("DELETE FROM audit_event"))
    denied = world["usr-052"].get("/work-orders/WO-3001/history")
    assert denied.status_code == 403
    evidence.observe("historial_WO-3001", [f"{h['seq']} {h['action']} {h['actorId']}/{h['actorRole']} "
                                           f"{(h['before'] or {}).get('status')}→{(h['after'] or {}).get('status')} "
                                           f"{h['correlationId']}" for h in history])
    evidence.observe("revertida_sin_auditoria", "409 DUPLICATE_CANDIDATE_EXISTS → 0 filas de auditoría nuevas")
    evidence.observe("update_delete_audit", "rechazados por trigger audit_event_append_only")
    evidence.observe("solicitante_lee_historial", denied.status_code)
    evidence.limit("La auditoría no resiste a un superusuario de la base de datos (riesgo residual R-014): "
                   "TRUNCATE (usado por demo-reset) no dispara el trigger fila a fila.")
