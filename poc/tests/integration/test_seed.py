"""`make seed` y `make demo-reset` son idempotentes y dejan el estado inicial de la demo (T-ENV-01, §6 del prompt)."""

import pytest
from sqlalchemy import text

from backend.infrastructure.seeding import reset, seed
from tests.conftest import rows

pytestmark = pytest.mark.db
SNAPSHOT_SQL = {
    "solicitudes": "SELECT request_id, requester_id, location_id, status, row_version FROM maintenance_request "
                   "ORDER BY 1",
    "ordenes": "SELECT work_order_id, status, row_version FROM work_order ORDER BY 1",
    "roles": "SELECT subject_id, role, scope_type, scope_id, active FROM role_assignment ORDER BY 1, 2, 3, 4",
    "tecnicos": "SELECT * FROM technician_profile ORDER BY 1",
    "politicas": "SELECT policy_version, installed_at IS NOT NULL AS instalada FROM policy_version ORDER BY 1",
    "ubicaciones": "SELECT location_id, campus, active, directory_version FROM location_projection ORDER BY 1",
}


def snapshot(engine):
    return {k: rows(engine, sql) for k, sql in SNAPSHOT_SQL.items()}


@pytest.mark.evidence("T-ENV-01", code_refs=["poc/backend/infrastructure/seeding.py", "poc/Makefile"],
                      related={"decisions": ["E2-ADR-002", "E2-ADR-008"]},
                      expected="seed y demo-reset idempotentes; estado inicial de la demo reproducible")
def test_seed_and_reset_are_idempotent(fresh_db, clock, evidence):
    initial = snapshot(fresh_db)
    seed(fresh_db, clock.now())
    seed(fresh_db, clock.now())
    assert snapshot(fresh_db) == initial
    with fresh_db.begin() as c:  # una toma de la demo "ensucia" el estado
        c.execute(text("UPDATE maintenance_request SET status = 'WITHDRAWN' WHERE request_id = 'SR-2043'"))
        c.execute(text("INSERT INTO role_assignment (subject_id, role, scope_type) VALUES ('x', 'SUPERVISOR', "
                       "'GLOBAL')"))
        c.execute(text("UPDATE policy_version SET installed_at = now() WHERE policy_version = 'POL-002'"))
    reset(fresh_db, clock.now())
    assert snapshot(fresh_db) == initial
    reset(fresh_db, clock.now())
    assert snapshot(fresh_db) == initial
    with fresh_db.connect() as c:
        next_ids = [c.execute(text(f"SELECT nextval('{s}')")).scalar_one()
                    for s in ("request_number_seq", "work_order_number_seq")]
    assert next_ids == [2041, 3001]
    evidence.observe("estado_inicial", {k: len(v) for k, v in initial.items()})
    evidence.observe("solicitudes_demo", [f"{r['request_id']} {r['status']}" for r in initial["solicitudes"]])
    evidence.observe("idempotencia", "seed×2 y demo-reset×2 producen el mismo estado; secuencias en SR-2041/WO-3001")
    evidence.limit("Esta prueba cubre seed/demo-reset; el arranque desde cero con `make clean && make up` se "
                   "registra aparte (scripts/t_env_01.sh).")
