"""T-TXN-01 (atomicidad del triaje) y T-API-01/02/03 (idempotencia y precondiciones) — §7.5, §8.4."""

import pytest

from tests.conftest import FailAt, counts, scalar

pytestmark = pytest.mark.db
APPROVE = {"action": "APPROVE_CREATE_WO", "category": "ELECTRICAL", "validatedUrgency": "HIGH",
           "discriminator": "", "assigneeId": "TECH-021"}
POINTS = ["after_work_order", "after_audit", "after_outbox", "after_receipt", "before_commit"]


@pytest.mark.evidence(
    "T-TXN-01",
    code_refs=["poc/backend/application/commands.py:CommandRunner.run",
               "poc/backend/application/triage.py:TriageApplicationService.decide",
               "poc/backend/infrastructure/sql.py:SqlUnitOfWork"],
    related={"requirements": ["E2-FR-03", "E2-FR-09"], "qualities": ["QS-01", "QS-03"],
             "decisions": ["E2-ADR-003", "E2-ADR-004", "E2-ADR-007"]},
    expected="Reversión total ante un fallo entre orden, auditoría, outbox, receipt y COMMIT; sin huérfanos; "
             "resultado incierto resuelto por receipt",
)
@pytest.mark.parametrize("point", POINTS)
def test_injected_failure_rolls_back_everything(app_factory, fresh_db, point, evidence):
    faulty = app_factory(faults=FailAt(point))
    coord = faulty.login("coord-03")
    before = counts(fresh_db)
    etag = coord.request_etag("SR-2043")
    r = coord.post("/requests/SR-2043/triage", APPROVE, if_match=etag, key=f"txn-{point}")
    after = counts(fresh_db)
    req_state = scalar(fresh_db, "SELECT status || '/v' || row_version FROM maintenance_request "
                                 "WHERE request_id = 'SR-2043'")
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL_ERROR" and "Traceback" not in r.text
    assert after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert req_state == "UNDER_TRIAGE/v3"
    # El reintento con la misma clave se ejecuta (no hubo receipt) y ahora sí confirma todo junto.
    healthy = app_factory()
    coord2 = healthy.login("coord-03")
    r2 = coord2.post("/requests/SR-2043/triage", APPROVE, if_match=etag, key=f"txn-{point}")
    assert r2.status_code == 201, r2.text
    final = counts(fresh_db)
    delta = {k: final[k] - after[k] for k in final if final[k] != after[k]}
    assert "maintenance_request" not in delta and delta["work_order"] == 1 and delta["command_receipt"] == 1
    evidence.observe(f"fallo@{point}", {"http": r.status_code, "filas_cambiadas": 0, "solicitud": req_state,
                                        "reintento_misma_clave": r2.status_code, "delta_tras_reintento": delta})


API_REFS = ["poc/backend/application/commands.py:CommandRunner", "poc/backend/http/deps.py:versioned",
            "poc/backend/domain/model.py:check_version"]
API_RELATED = {"requirements": ["E2-FR-03"], "qualities": ["QS-01"], "decisions": ["E2-ADR-007"]}


@pytest.mark.evidence("T-API-01", code_refs=API_REFS, related=API_RELATED,
                      expected="Reintentar un comando exitoso tras perder la respuesta reproduce el resultado; "
                               "sin mutación adicional")
def test_retry_after_lost_response_replays(api, fresh_db, evidence):
    coord = api.login("coord-03")
    etag = coord.request_etag("SR-2043")
    first = coord.post("/requests/SR-2043/triage", APPROVE, if_match=etag, key="lost-response-1")
    assert first.status_code == 201
    snapshot = counts(fresh_db)
    # La respuesta "se pierde": el cliente reintenta con la misma clave y la precondición ya vieja.
    retry = coord.post("/requests/SR-2043/triage", APPROVE, if_match=etag, key="lost-response-1")
    assert retry.status_code == 201 and retry.headers.get("Idempotent-Replayed") == "true"
    assert retry.json() == first.json() and retry.headers["ETag"] == first.headers["ETag"]
    assert counts(fresh_db) == snapshot
    evidence.observe("reintento", {"status": retry.status_code, "Idempotent-Replayed": "true",
                                   "mismo_cuerpo": True, "filas_nuevas": 0})


@pytest.mark.evidence("T-API-02", code_refs=API_REFS, related=API_RELATED,
                      expected="Misma clave con contenido distinto → 409 sin reinterpretar")
def test_key_reuse_with_different_body(api, fresh_db, evidence):
    coord = api.login("coord-03")
    etag = coord.request_etag("SR-2043")
    assert coord.post("/requests/SR-2043/triage", APPROVE, if_match=etag, key="reuse-1").status_code == 201
    snapshot = counts(fresh_db)
    other = {**APPROVE, "validatedUrgency": "LOW"}
    r = coord.post("/requests/SR-2043/triage", other, if_match=etag, key="reuse-1")
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    # Otra persona con la misma clave tampoco reproduce el resultado ajeno.
    r2 = api.login("coord-04").post("/requests/SR-2043/triage", APPROVE, if_match=etag, key="reuse-1")
    assert r2.status_code == 409 and r2.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert counts(fresh_db) == snapshot
    evidence.observe("misma_clave_otro_cuerpo", r.json()["code"])
    evidence.observe("misma_clave_otro_actor", r2.json()["code"])


@pytest.mark.evidence("T-API-03", code_refs=API_REFS, related=API_RELATED,
                      expected="Versiones obsoletas → 412 sin efectos")
def test_stale_versions(api, fresh_db, evidence):
    coord, tech = api.login("coord-03"), api.login("TECH-014")
    snapshot = counts(fresh_db)
    r = coord.post("/requests/SR-2043/triage", APPROVE, if_match='"request-SR-2043-v2"')
    assert r.status_code == 412 and r.json()["code"] == "PRECONDITION_FAILED"
    r_wo = tech.post("/work-orders/WO-3000/start", if_match='"work-order-WO-3000-v1"')
    assert r_wo.status_code == 412
    missing = coord.post("/requests/SR-2043/triage", APPROVE)
    assert missing.status_code == 400 and missing.json()["code"] == "REQUIRED_HEADER_MISSING"
    no_key = coord.client.post("/api/v1/requests/SR-2043/triage", json=APPROVE,
                               headers={"X-CSRF-Token": coord.csrf, "If-Match": coord.request_etag("SR-2043")})
    assert no_key.status_code == 400 and no_key.json()["header"] == "Idempotency-Key"
    assert counts(fresh_db) == snapshot
    evidence.observe("solicitud_v2_vs_v3", r.json()["code"])
    evidence.observe("orden_v1_vs_v2", r_wo.json()["code"])
    evidence.observe("sin_If-Match", missing.json()["code"])
    evidence.observe("sin_Idempotency-Key", no_key.json()["code"])
    evidence.observe("filas_cambiadas", 0)
