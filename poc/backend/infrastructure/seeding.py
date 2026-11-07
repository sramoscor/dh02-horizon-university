"""Datos semilla sintéticos e idempotentes (`make seed`) y reinicio de la demo (`make demo-reset`).

Los datos vienen de `poc/fixtures/*.json` (los mismos que usan los simuladores). Ninguna credencial real.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from backend.domain.enums import Category, Urgency
from backend.domain.policies import SlaPolicy, parse_rules, rules_to_json

FIXTURES = Path(os.environ.get("FIXTURES_DIR", Path(__file__).resolve().parents[2] / "fixtures"))

BUSINESS_TABLES = [
    "overdue_alert", "command_receipt", "outbox_event", "audit_event", "completion_confirmation",
    "observation", "material_usage", "block_event", "visit", "assignment", "work_order", "triage_record",
    "maintenance_request", "user_session",
]
# Datos de referencia que también vuelven a la semilla (roles o técnicos agregados en una toma se eliminan).
REFERENCE_TABLES = ["role_assignment", "technician_profile", "location_projection"]


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _audit(c, entity_type: str, entity_id: str, action: str, after: dict, now: datetime) -> None:
    c.execute(text("""
        INSERT INTO audit_event (entity_type, entity_id, actor_id, actor_role, action, before_state, after_state,
            reason, correlation_id, occurred_at)
        VALUES (:t, :id, 'system:seed', 'SYSTEM', :a, NULL, CAST(:after AS jsonb), 'Dato semilla sintético',
            'corr-seed', :now)
    """), {"t": entity_type, "id": entity_id, "a": action, "after": json.dumps(after), "now": now})


def seed(engine: Engine, now: datetime) -> dict[str, int]:
    """Idempotente: datos de referencia por upsert; datos de demo solo si no existen."""
    directory = load_fixture("directory.json")
    data = load_fixture("seed.json")
    counts = {"locations": 0, "roles": 0, "technicians": 0, "policies": 0, "requests": 0, "workOrders": 0}
    with engine.begin() as c:
        for s in directory["spaces"]:
            c.execute(text("""
                INSERT INTO location_projection (location_id, building_id, campus, display_name, active,
                    restricted_access, building_representative_id, directory_version, source_revision, fetched_at)
                VALUES (:id, :b, :c, :n, :a, :r, :rep, :v, :rev, :now)
                ON CONFLICT (location_id) DO UPDATE SET fetched_at = EXCLUDED.fetched_at
                    WHERE location_projection.source_revision <= EXCLUDED.source_revision
            """), {"id": s["locationId"], "b": s["buildingId"], "c": s["campus"], "n": s["displayName"],
                   "a": s["active"], "r": s["restrictedAccess"], "rep": s["buildingRepresentativeId"],
                   "v": directory["directoryVersion"], "rev": directory["sourceRevision"], "now": now})
            counts["locations"] += 1
        for r in data["roleAssignments"]:
            c.execute(text("""
                INSERT INTO role_assignment (subject_id, role, scope_type, scope_id, active)
                VALUES (:s, :r, :t, :i, :a)
                ON CONFLICT ON CONSTRAINT uq_role_assignment DO UPDATE SET active = EXCLUDED.active
            """), {"s": r["subject"], "r": r["role"], "t": r["scopeType"], "i": r["scopeId"],
                   "a": r.get("active", True)})
            counts["roles"] += 1
        for t in data["technicians"]:
            c.execute(text("""
                INSERT INTO technician_profile (subject_id, specialty, available) VALUES (:s, :sp, :a)
                ON CONFLICT (subject_id) DO UPDATE SET specialty = EXCLUDED.specialty, available = EXCLUDED.available
            """), {"s": t["subject"], "sp": t["specialty"], "a": t["available"]})
            counts["technicians"] += 1
        for p in data["policies"]:
            rules = parse_rules(p["version"], p["rules"])  # una política malformada no se instala (T-POL-01)
            c.execute(text("""
                INSERT INTO policy_version (policy_version, validated_rules, content_hash, installed_at)
                VALUES (:v, CAST(:rules AS jsonb), :hash, :installed)
                ON CONFLICT (policy_version) DO NOTHING
            """), {"v": p["version"], "rules": json.dumps(rules_to_json(rules)), "hash": rules.content_hash(),
                   "installed": now - timedelta(days=30) if p["installed"] else None})
            counts["policies"] += 1
        counts["requests"], counts["workOrders"] = _seed_demo(c, data, now)
    return counts


def _seed_demo(c, data: dict[str, Any], now: datetime) -> tuple[int, int]:
    created_requests = created_orders = 0
    base = now - timedelta(hours=2)
    for i, r in enumerate(data["requests"]):
        created = base + timedelta(minutes=i)
        row = c.execute(text("""
            INSERT INTO maintenance_request (request_id, requester_id, location_id, description, suggested_category,
                suggested_urgency, safety_flag, status, row_version, created_at, submitted_at, updated_at)
            VALUES (:id, :req, :loc, :d, :cat, :urg, false, :status, :ver, :created, :created, :created)
            ON CONFLICT (request_id) DO NOTHING RETURNING request_id
        """), {"id": r["id"], "req": r["requester"], "loc": r["location"], "d": r["description"],
               "cat": r["category"], "urg": r["urgency"], "status": r["status"],
               "ver": {"SUBMITTED": 2, "UNDER_TRIAGE": 3, "APPROVED": 4}.get(r["status"], 1),
               "created": created}).first()
        if row:
            created_requests += 1
            _audit(c, "MaintenanceRequest", r["id"], "SEEDED", {"status": r["status"]}, created)
    for w in data["workOrders"]:
        if c.execute(text("SELECT 1 FROM work_order WHERE work_order_id = :id"), {"id": w["id"]}).first():
            continue
        rules_row = c.execute(text("SELECT policy_version, validated_rules FROM policy_version "
                                   "WHERE installed_at IS NOT NULL ORDER BY installed_at DESC LIMIT 1")).first()
        rules = parse_rules(rules_row[0], rules_row[1])
        created = base + timedelta(minutes=30)
        due = SlaPolicy().due_at(rules, created, Urgency(w["urgency"]), Category(w["category"]))
        c.execute(text("""
            INSERT INTO triage_record (triage_id, request_id, coordinator_id, action, category, suggested_urgency,
                validated_urgency, policy_version, decided_at, correlation_id)
            VALUES (:t, :r, :coord, 'APPROVE_CREATE_WO', :cat, :urg, :urg, :pol, :at, 'corr-seed')
        """), {"t": w["triage"], "r": w["request"], "coord": w["coordinator"], "cat": w["category"],
               "urg": w["urgency"], "pol": rules.version, "at": created})
        c.execute(text("""
            INSERT INTO work_order (work_order_id, request_id, triage_id, location_id, category, urgency, status,
                due_at, policy_version, row_version, created_at)
            VALUES (:id, :r, :t, :loc, :cat, :urg, :status, :due, :pol, 2, :at)
        """), {"id": w["id"], "r": w["request"], "t": w["triage"], "loc": w["location"], "cat": w["category"],
               "urg": w["urgency"], "status": w["status"], "due": due, "pol": rules.version, "at": created})
        c.execute(text("""
            INSERT INTO assignment (work_order_id, assignee_id, assigned_by, is_active, assigned_at)
            VALUES (:id, :a, :coord, true, :at)
        """), {"id": w["id"], "a": w["assignee"], "coord": w["coordinator"], "at": created})
        _audit(c, "WorkOrder", w["id"], "SEEDED", {"status": w["status"], "assigneeId": w["assignee"]}, created)
        created_orders += 1
    return created_requests, created_orders


def reset(engine: Engine, now: datetime) -> dict[str, Any]:
    """Vuelve al estado inicial de la demo. TRUNCATE no dispara el trigger de solo-anexado (fila a fila)."""
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {', '.join(BUSINESS_TABLES + REFERENCE_TABLES)} RESTART IDENTITY CASCADE"))
        c.execute(text("ALTER SEQUENCE request_number_seq RESTART WITH 2041"))
        c.execute(text("ALTER SEQUENCE work_order_number_seq RESTART WITH 3001"))
        c.execute(text("ALTER SEQUENCE triage_number_seq RESTART WITH 9901"))
        c.execute(text("UPDATE policy_version SET installed_at = NULL WHERE policy_version <> 'POL-001'"))
    return {"reset": True, "seeded": seed(engine, now)}


class SqlDemoControl:
    def __init__(self, engine: Engine, clock) -> None:
        self._engine = engine
        self._clock = clock

    def reset(self) -> dict[str, Any]:
        return reset(self._engine, self._clock.now())
