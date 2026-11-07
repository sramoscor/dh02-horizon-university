"""PostgreSQL: Unit of Work y repositorios (§5.3 "Unit of Work y puertos de persistencia"; E2-ADR-003/007).

Consultas parametrizadas (T-SEC-05). Las violaciones de unicidad se traducen a errores de negocio aquí,
en el borde con la base, y nunca llegan como errores técnicos al cliente (E2-ADR-003 punto 4).
"""

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from backend.application.ports import (
    ConcurrentDuplicateCommand,
    FaultInjector,
    Location,
    NoFaults,
    OutboxRow,
    Receipt,
    RoleGrant,
    SessionRecord,
    Space,
)
from backend.domain.enums import ACTIVE_WORK_ORDER_STATUSES, Category, Urgency
from backend.domain.errors import DuplicateCandidateExists
from backend.domain.model import MaintenanceRequest, WorkOrder
from backend.domain.policies import DuplicateCandidate, PolicyRules, TechnicianCandidate, parse_rules

ACTIVE_SQL = "(" + ", ".join(f"'{s.value}'" for s in sorted(ACTIVE_WORK_ORDER_STATUSES)) + ")"
OPEN_REQUEST_SQL = "('SUBMITTED', 'UNDER_TRIAGE', 'NEEDS_INFO')"


def make_engine(url: str, pool_size: int = 10) -> Engine:
    return create_engine(url, pool_size=pool_size, max_overflow=10, pool_pre_ping=True, future=True)


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def _next_id(conn: Connection, sequence: str, prefix: str, table: str, column: str) -> str:
    """IDs legibles (`SR-2041`, `WO-3001`) que saltan los ya sembrados (divergencia D-04)."""
    while True:
        n = conn.execute(text(f"SELECT nextval('{sequence}')")).scalar_one()
        candidate = f"{prefix}-{n}"
        exists = conn.execute(text(f"SELECT 1 FROM {table} WHERE {column} = :id"), {"id": candidate}).first()
        if not exists:
            return candidate


# --------------------------------------------------------------------------- repositorios


class SqlRequests:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def next_id(self) -> str:
        return _next_id(self.c, "request_number_seq", "SR", "maintenance_request", "request_id")

    @staticmethod
    def _map(r) -> MaintenanceRequest:
        return MaintenanceRequest(
            request_id=r["request_id"], requester_id=r["requester_id"], location_id=r["location_id"],
            description=r["description"], suggested_urgency=Urgency(r["suggested_urgency"]),
            suggested_category=Category(r["suggested_category"]) if r["suggested_category"] else None,
            safety_flag=r["safety_flag"], status=r["status"], row_version=r["row_version"],
            created_at=r["created_at"], submitted_at=r["submitted_at"],
            linked_work_order_id=r["linked_work_order_id"], additional_info=r["additional_info"],
        )

    def add(self, req: MaintenanceRequest) -> None:
        self.c.execute(text("""
            INSERT INTO maintenance_request (request_id, requester_id, location_id, description,
                suggested_category, suggested_urgency, safety_flag, status, row_version, created_at, updated_at)
            VALUES (:id, :requester, :loc, :desc, :cat, :urg, :safety, :status, :ver, :now, :now)
        """), {"id": req.request_id, "requester": req.requester_id, "loc": req.location_id,
               "desc": req.description, "cat": req.suggested_category.value if req.suggested_category else None,
               "urg": req.suggested_urgency.value, "safety": req.safety_flag, "status": req.status,
               "ver": req.row_version, "now": req.created_at})

    def get(self, request_id: str, *, for_update: bool = False) -> MaintenanceRequest | None:
        sql = "SELECT * FROM maintenance_request WHERE request_id = :id" + (" FOR UPDATE" if for_update else "")
        row = self.c.execute(text(sql), {"id": request_id}).mappings().first()
        return self._map(row) if row else None

    def save(self, req: MaintenanceRequest, now: datetime) -> None:
        self.c.execute(text("""
            UPDATE maintenance_request SET status = :status, row_version = :ver, submitted_at = :submitted,
                linked_work_order_id = :linked, additional_info = :info, updated_at = :now
            WHERE request_id = :id
        """), {"status": req.status, "ver": req.row_version, "submitted": req.submitted_at,
               "linked": req.linked_work_order_id, "info": req.additional_info, "now": now, "id": req.request_id})

    def list(self, *, requester_id=None, statuses=None, location_ids=None, limit: int = 100):
        clauses, params = ["TRUE"], {"limit": limit}
        if requester_id is not None:
            clauses.append("requester_id = :requester")
            params["requester"] = requester_id
        if statuses:
            clauses.append("status = ANY(:statuses)")
            params["statuses"] = [str(s) for s in statuses]
        if location_ids is not None:
            clauses.append("location_id = ANY(:locs)")
            params["locs"] = list(location_ids)
        rows = self.c.execute(text(
            f"SELECT * FROM maintenance_request WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT :limit"
        ), params).mappings()
        return [self._map(r) for r in rows]

    def open_at_location(self, location_id: str) -> list[DuplicateCandidate]:
        rows = self.c.execute(text(f"""
            SELECT request_id, status, suggested_category FROM maintenance_request
            WHERE location_id = :loc AND status IN {OPEN_REQUEST_SQL} ORDER BY created_at
        """), {"loc": location_id}).mappings()
        return [DuplicateCandidate("REQUEST", r["request_id"], r["status"], r["suggested_category"]) for r in rows]


WO_SELECT = """
    SELECT w.*, (SELECT a.assignee_id FROM assignment a WHERE a.work_order_id = w.work_order_id AND a.is_active
                 ORDER BY a.assigned_at DESC LIMIT 1) AS assignee_id
    FROM work_order w
"""


class SqlWorkOrders:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def next_id(self) -> str:
        return _next_id(self.c, "work_order_number_seq", "WO", "work_order", "work_order_id")

    @staticmethod
    def _map(r) -> WorkOrder:
        return WorkOrder(
            work_order_id=r["work_order_id"], request_id=r["request_id"], triage_id=r["triage_id"],
            location_id=r["location_id"], category=Category(r["category"]), discriminator=r["discriminator"],
            urgency=Urgency(r["urgency"]), safety_flag=r["safety_flag"], status=r["status"], due_at=r["due_at"],
            policy_version=r["policy_version"], row_version=r["row_version"], created_at=r["created_at"],
            closed_at=r["closed_at"], assignee_id=r["assignee_id"],
        )

    def add(self, wo: WorkOrder) -> None:
        params = {"id": wo.work_order_id, "req": wo.request_id, "trg": wo.triage_id, "loc": wo.location_id,
                  "cat": wo.category.value, "disc": wo.discriminator, "urg": wo.urgency.value,
                  "safety": wo.safety_flag, "status": wo.status, "due": wo.due_at, "pol": wo.policy_version,
                  "ver": wo.row_version, "now": wo.created_at}
        try:
            # SAVEPOINT: si la restricción falla, todavía podemos leer la orden ganadora en esta transacción.
            with self.c.begin_nested():
                self.c.execute(text("""
                    INSERT INTO work_order (work_order_id, request_id, triage_id, location_id, category,
                        discriminator, urgency, safety_flag, status, due_at, policy_version, row_version, created_at)
                    VALUES (:id, :req, :trg, :loc, :cat, :disc, :urg, :safety, :status, :due, :pol, :ver, :now)
                """), params)
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_work_order_active_slot":  # E2-ADR-003, QS-01
                existing = self.c.execute(text(f"""
                    SELECT work_order_id FROM work_order WHERE location_id = :loc AND category = :cat
                    AND discriminator = :disc AND status IN {ACTIVE_SQL} LIMIT 1
                """), params).scalar()
                raise DuplicateCandidateExists(existing) from None
            if constraint == "uq_work_order_request":  # BR-02
                existing = self.c.execute(
                    text("SELECT work_order_id FROM work_order WHERE request_id = :req"), params).scalar()
                raise DuplicateCandidateExists(existing) from None
            raise

    def get(self, work_order_id: str, *, for_update: bool = False) -> WorkOrder | None:
        sql = WO_SELECT + " WHERE w.work_order_id = :id" + (" FOR UPDATE OF w" if for_update else "")
        row = self.c.execute(text(sql), {"id": work_order_id}).mappings().first()
        return self._map(row) if row else None

    def by_request(self, request_id: str) -> WorkOrder | None:
        row = self.c.execute(text(WO_SELECT + " WHERE w.request_id = :id"), {"id": request_id}).mappings().first()
        return self._map(row) if row else None

    def save(self, wo: WorkOrder) -> None:
        self.c.execute(text("""
            UPDATE work_order SET status = :status, row_version = :ver, due_at = :due, urgency = :urg,
                closed_at = :closed WHERE work_order_id = :id
        """), {"status": wo.status, "ver": wo.row_version, "due": wo.due_at, "urg": wo.urgency.value,
               "closed": wo.closed_at, "id": wo.work_order_id})

    def active_at_location(self, location_id: str) -> list[DuplicateCandidate]:
        rows = self.c.execute(text(f"""
            SELECT work_order_id, status, category, discriminator FROM work_order
            WHERE location_id = :loc AND status IN {ACTIVE_SQL} ORDER BY created_at
        """), {"loc": location_id}).mappings()
        return [DuplicateCandidate("WORK_ORDER", r["work_order_id"], r["status"], r["category"], r["discriminator"])
                for r in rows]

    def search(self, filters: dict[str, Any], limit: int = 100) -> list[WorkOrder]:
        clauses, params = ["TRUE"], {"limit": min(limit, 500)}
        for key, column in (("status", "w.status"), ("category", "w.category"), ("locationId", "w.location_id")):
            if filters.get(key):
                clauses.append(f"{column} = ANY(:{key})")
                params[key] = [v for v in str(filters[key]).split(",") if v]
        if filters.get("assigneeId"):
            clauses.append("EXISTS (SELECT 1 FROM assignment a WHERE a.work_order_id = w.work_order_id "
                           "AND a.is_active AND a.assignee_id = :assignee)")
            params["assignee"] = filters["assigneeId"]
        if filters.get("overdueAt"):
            clauses.append(f"w.due_at < :overdue AND w.status IN {ACTIVE_SQL}")
            params["overdue"] = filters["overdueAt"]
        rows = self.c.execute(text(
            WO_SELECT + f" WHERE {' AND '.join(clauses)} ORDER BY w.created_at DESC LIMIT :limit"), params).mappings()
        return [self._map(r) for r in rows]

    def pending_confirmation_before(self, cutoff: datetime) -> list[str]:
        """E2-AS-02: órdenes en PENDING_CONFIRMATION cuyo último cambio es anterior al corte."""
        rows = self.c.execute(text("""
            SELECT w.work_order_id FROM work_order w
            WHERE w.status = 'PENDING_CONFIRMATION' AND (
                SELECT max(e.occurred_at) FROM audit_event e
                WHERE e.entity_id = w.work_order_id AND e.after_state->>'status' = 'PENDING_CONFIRMATION'
            ) <= :cutoff
        """), {"cutoff": cutoff}).scalars()
        return list(rows)

    def overdue_candidates(self, now: datetime) -> list[WorkOrder]:
        """V-SEQ-04: no terminales, no pausadas (bloqueo MATERIALS/ACCESS abierto) y con due_at vencido."""
        rows = self.c.execute(text(WO_SELECT + f"""
            WHERE w.status IN {ACTIVE_SQL} AND w.status <> 'PENDING_CONFIRMATION' AND w.due_at < :now
            AND NOT EXISTS (SELECT 1 FROM block_event b WHERE b.work_order_id = w.work_order_id
                            AND b.ended_at IS NULL AND b.block_type IN ('MATERIALS', 'ACCESS'))
        """), {"now": now}).mappings()
        return [self._map(r) for r in rows]


class SqlTriage:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def next_id(self) -> str:
        return _next_id(self.c, "triage_number_seq", "TRG", "triage_record", "triage_id")

    def add(self, record: dict[str, Any]) -> None:
        self.c.execute(text("""
            INSERT INTO triage_record (triage_id, request_id, coordinator_id, action, category, suggested_urgency,
                validated_urgency, safety_flag, reason, discriminator, policy_version, decided_at, correlation_id)
            VALUES (:triage_id, :request_id, :coordinator_id, :action, :category, :suggested_urgency,
                :validated_urgency, :safety_flag, :reason, :discriminator, :policy_version, :decided_at,
                :correlation_id)
        """), record)

    def latest_for(self, request_id: str) -> dict[str, Any] | None:
        row = self.c.execute(text(
            "SELECT * FROM triage_record WHERE request_id = :id ORDER BY decided_at DESC LIMIT 1"
        ), {"id": request_id}).mappings().first()
        return dict(row) if row else None


class SqlWorkLog:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def active_assignee(self, work_order_id: str) -> str | None:
        return self.c.execute(text(
            "SELECT assignee_id FROM assignment WHERE work_order_id = :id AND is_active "
            "ORDER BY assigned_at DESC LIMIT 1"), {"id": work_order_id}).scalar()

    def end_active_assignments(self, work_order_id: str, now: datetime) -> None:
        self.c.execute(text("UPDATE assignment SET is_active = false, ended_at = :now "
                            "WHERE work_order_id = :id AND is_active"), {"id": work_order_id, "now": now})

    def add_assignment(self, work_order_id, assignee_id, by, reason, now) -> None:
        self.c.execute(text("""
            INSERT INTO assignment (work_order_id, assignee_id, assigned_by, reason, is_active, assigned_at)
            VALUES (:id, :assignee, :by, :reason, true, :now)
        """), {"id": work_order_id, "assignee": assignee_id, "by": by, "reason": reason, "now": now})

    def open_block(self, work_order_id: str) -> dict[str, Any] | None:
        row = self.c.execute(text("SELECT block_id::text AS block_id, block_type, started_at FROM block_event "
                                  "WHERE work_order_id = :id AND ended_at IS NULL"), {"id": work_order_id})
        row = row.mappings().first()
        return dict(row) if row else None

    def add_block(self, work_order_id, block_type, detail, by, now) -> str:
        return str(self.c.execute(text("""
            INSERT INTO block_event (work_order_id, block_type, detail, started_by, started_at)
            VALUES (:id, :type, :detail, :by, :now) RETURNING block_id
        """), {"id": work_order_id, "type": str(block_type), "detail": detail, "by": by, "now": now}).scalar_one())

    def close_block(self, block_id: str, by: str, now: datetime) -> None:
        self.c.execute(text("UPDATE block_event SET ended_at = :now, resolved_by = :by WHERE block_id = :id"),
                       {"id": block_id, "by": by, "now": now})

    def add_material(self, work_order_id, name, quantity, by, now) -> str:
        return str(self.c.execute(text("""
            INSERT INTO material_usage (work_order_id, material_name, quantity, recorded_by, recorded_at)
            VALUES (:id, :name, :qty, :by, :now) RETURNING usage_id
        """), {"id": work_order_id, "name": name, "qty": quantity, "by": by, "now": now}).scalar_one())

    def add_observation(self, work_order_id, author, note, attachment_ref, now) -> str:
        return str(self.c.execute(text("""
            INSERT INTO observation (work_order_id, author_id, note, attachment_ref, recorded_at)
            VALUES (:id, :author, :note, :ref, :now) RETURNING observation_id
        """), {"id": work_order_id, "author": author, "note": note, "ref": attachment_ref, "now": now}).scalar_one())

    def observation_count(self, work_order_id: str) -> int:
        return self.c.execute(text("SELECT count(*) FROM observation WHERE work_order_id = :id"),
                              {"id": work_order_id}).scalar_one()

    def add_confirmation(self, work_order_id, by, outcome, reason, now) -> None:
        self.c.execute(text("""
            INSERT INTO completion_confirmation (work_order_id, confirmed_by, outcome, reason, decided_at)
            VALUES (:id, :by, :outcome, :reason, :now)
        """), {"id": work_order_id, "by": by, "outcome": str(outcome), "reason": reason, "now": now})

    def details(self, work_order_id: str) -> dict[str, Any]:
        def rows(sql: str) -> list[dict[str, Any]]:
            result = self.c.execute(text(sql), {"id": work_order_id}).mappings()
            return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in result]

        return {
            "assignments": rows("SELECT assignee_id AS \"assigneeId\", assigned_by AS \"assignedBy\", reason, "
                                "is_active AS \"isActive\", assigned_at AS \"assignedAt\", ended_at AS \"endedAt\" "
                                "FROM assignment WHERE work_order_id = :id ORDER BY assigned_at"),
            "blocks": rows("SELECT block_id::text AS \"blockId\", block_type AS \"blockType\", detail, "
                           "started_by AS \"startedBy\", started_at AS \"startedAt\", resolved_by AS \"resolvedBy\", "
                           "ended_at AS \"endedAt\" FROM block_event WHERE work_order_id = :id ORDER BY started_at"),
            "materials": rows("SELECT material_name AS \"materialName\", quantity, recorded_by AS \"recordedBy\", "
                              "recorded_at AS \"recordedAt\" FROM material_usage WHERE work_order_id = :id "
                              "ORDER BY recorded_at"),
            "observations": rows("SELECT author_id AS \"authorId\", note, attachment_ref AS \"attachmentRef\", "
                                 "recorded_at AS \"recordedAt\" FROM observation WHERE work_order_id = :id "
                                 "ORDER BY recorded_at"),
            "confirmations": rows("SELECT confirmed_by AS \"confirmedBy\", outcome, reason, "
                                  "decided_at AS \"decidedAt\" FROM completion_confirmation "
                                  "WHERE work_order_id = :id ORDER BY decided_at"),
        }

    def technician_candidates(self) -> list[TechnicianCandidate]:
        """E2-AS-01: la carga son órdenes activas (no terminales) por técnico."""
        rows = self.c.execute(text(f"""
            SELECT t.subject_id, t.specialty, t.available,
                   (SELECT count(*) FROM assignment a JOIN work_order w ON w.work_order_id = a.work_order_id
                    WHERE a.assignee_id = t.subject_id AND a.is_active AND w.status IN {ACTIVE_SQL}) AS load
            FROM technician_profile t
            WHERE EXISTS (SELECT 1 FROM role_assignment r WHERE r.subject_id = t.subject_id
                          AND r.role = 'TECHNICIAN' AND r.active)
        """)).mappings()
        return [TechnicianCandidate(r["subject_id"], r["specialty"], r["available"], r["load"]) for r in rows]

    def workload(self) -> list[dict[str, Any]]:
        rows = self.c.execute(text(f"""
            SELECT t.subject_id, t.specialty, t.available,
                   coalesce(array_agg(w.work_order_id ORDER BY w.work_order_id)
                            FILTER (WHERE w.work_order_id IS NOT NULL), '{{}}') AS orders
            FROM technician_profile t
            LEFT JOIN assignment a ON a.assignee_id = t.subject_id AND a.is_active
            LEFT JOIN work_order w ON w.work_order_id = a.work_order_id AND w.status IN {ACTIVE_SQL}
            GROUP BY t.subject_id, t.specialty, t.available ORDER BY t.subject_id
        """)).mappings()
        return [{"technicianId": r["subject_id"], "specialty": r["specialty"], "available": r["available"],
                 "activeLoad": len(r["orders"]), "workOrders": list(r["orders"])} for r in rows]


class SqlAudit:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def append(self, event: dict[str, Any]) -> None:
        self.c.execute(text("""
            INSERT INTO audit_event (entity_type, entity_id, actor_id, actor_role, action, before_state,
                after_state, reason, correlation_id, occurred_at)
            VALUES (:entity_type, :entity_id, :actor_id, :actor_role, :action, CAST(:before AS jsonb),
                CAST(:after AS jsonb), :reason, :correlation_id, :occurred_at)
        """), {**event, "before": _json(event["before_state"]), "after": _json(event["after_state"])})

    def for_entities(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        rows = self.c.execute(text("""
            SELECT audit_seq, entity_type, entity_id, actor_id, actor_role, action, before_state, after_state,
                   reason, correlation_id, occurred_at
            FROM audit_event WHERE entity_id = ANY(:ids) ORDER BY audit_seq
        """), {"ids": entity_ids}).mappings()
        return [{"seq": r["audit_seq"], "entityType": r["entity_type"], "entityId": r["entity_id"],
                 "actorId": r["actor_id"], "actorRole": r["actor_role"], "action": r["action"],
                 "before": r["before_state"], "after": r["after_state"], "reason": r["reason"],
                 "correlationId": r["correlation_id"], "occurredAt": r["occurred_at"].isoformat()} for r in rows]


class SqlOutbox:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def add(self, event: dict[str, Any]) -> None:
        self.c.execute(text("""
            INSERT INTO outbox_event (event_id, event_type, schema_version, aggregate_type, aggregate_id,
                aggregate_version, request_id, payload, recipient_ref, channel, delivery_state, attempt_count,
                next_attempt_at, correlation_id, created_at)
            VALUES (:event_id, :event_type, :schema_version, :aggregate_type, :aggregate_id, :aggregate_version,
                :request_id, CAST(:payload AS jsonb), :recipient_ref, :channel, 'PENDING', 0, :created_at,
                :correlation_id, :created_at)
        """), {**event, "payload": _json(event["payload"])})

    @staticmethod
    def _row(r) -> dict[str, Any]:
        return {k: (v.isoformat() if isinstance(v, datetime) else (str(v) if k in ("event_id", "lease_token") and v
                                                                    else v)) for k, v in r.items()}

    def for_request(self, request_id: str) -> list[dict[str, Any]]:
        rows = self.c.execute(text("SELECT * FROM outbox_event WHERE request_id = :id ORDER BY created_at"),
                              {"id": request_id}).mappings()
        return [self._row(r) for r in rows]

    def list(self, aggregate_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM outbox_event"
        params: dict[str, Any] = {"limit": limit}
        if aggregate_id:
            sql += " WHERE aggregate_id = :agg OR request_id = :agg"
            params["agg"] = aggregate_id
        rows = self.c.execute(text(sql + " ORDER BY created_at DESC LIMIT :limit"), params).mappings()
        return [self._row(r) for r in rows]


class SqlReceipts:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def lock(self, key: str) -> None:
        self.c.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key})

    def get(self, key: str) -> Receipt | None:
        row = self.c.execute(text("SELECT * FROM command_receipt WHERE idempotency_key = :k"),
                             {"k": key}).mappings().first()
        return Receipt(row["actor_id"], row["operation"], row["fingerprint"], row["response"]) if row else None

    def add(self, key: str, receipt: Receipt, now: datetime) -> None:
        inserted = self.c.execute(text("""
            INSERT INTO command_receipt (idempotency_key, actor_id, operation, fingerprint, response, created_at)
            VALUES (:k, :actor, :op, :fp, CAST(:resp AS jsonb), :now)
            ON CONFLICT (idempotency_key) DO NOTHING RETURNING idempotency_key
        """), {"k": key, "actor": receipt.actor_id, "op": receipt.operation, "fp": receipt.fingerprint,
               "resp": _json(receipt.response), "now": now}).first()
        if inserted is None:
            raise ConcurrentDuplicateCommand(key)


class SqlLocations:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    @staticmethod
    def _map(r) -> Location:
        return Location(r["location_id"], r["building_id"], r["campus"], r["display_name"], r["active"],
                        r["restricted_access"], r["building_representative_id"], r["directory_version"],
                        r["source_revision"], r["fetched_at"])

    def get(self, location_id: str) -> Location | None:
        row = self.c.execute(text("SELECT * FROM location_projection WHERE location_id = :id"),
                             {"id": location_id}).mappings().first()
        return self._map(row) if row else None

    def all(self) -> list[Location]:
        rows = self.c.execute(text("SELECT * FROM location_projection ORDER BY location_id")).mappings()
        return [self._map(r) for r in rows]

    def upsert(self, s: Space, now: datetime) -> bool:
        """§8.5: `sourceRevision` monótono; se rechazan proyecciones que retroceden."""
        row = self.c.execute(text("""
            INSERT INTO location_projection AS p (location_id, building_id, campus, display_name, active,
                restricted_access, building_representative_id, directory_version, source_revision, fetched_at)
            VALUES (:id, :b, :c, :n, :a, :r, :rep, :v, :rev, :now)
            ON CONFLICT (location_id) DO UPDATE SET building_id = EXCLUDED.building_id, campus = EXCLUDED.campus,
                display_name = EXCLUDED.display_name, active = EXCLUDED.active,
                restricted_access = EXCLUDED.restricted_access,
                building_representative_id = EXCLUDED.building_representative_id,
                directory_version = EXCLUDED.directory_version, source_revision = EXCLUDED.source_revision,
                fetched_at = EXCLUDED.fetched_at
            WHERE p.source_revision <= EXCLUDED.source_revision
            RETURNING location_id
        """), {"id": s.location_id, "b": s.building_id, "c": s.campus, "n": s.display_name, "a": s.active,
               "r": s.restricted_access, "rep": s.building_representative_id, "v": s.directory_version,
               "rev": s.source_revision, "now": now}).first()
        return row is not None


class SqlAccess:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def active_grants(self, subject_id: str) -> list[RoleGrant]:
        rows = self.c.execute(text(
            "SELECT role, scope_type, scope_id FROM role_assignment WHERE subject_id = :s AND active "
            "ORDER BY role, scope_type, scope_id"), {"s": subject_id}).mappings()
        return [RoleGrant(r["role"], r["scope_type"], r["scope_id"]) for r in rows]

    def create_session(self, session_hash: str, record: SessionRecord, now: datetime) -> None:
        self.c.execute(text("""
            INSERT INTO user_session (session_hash, subject_id, display_name, csrf_token, created_at, expires_at)
            VALUES (:h, :s, :n, :csrf, :now, :exp)
        """), {"h": session_hash, "s": record.subject_id, "n": record.display_name, "csrf": record.csrf_token,
               "now": now, "exp": record.expires_at})

    def get_session(self, session_hash: str) -> SessionRecord | None:
        row = self.c.execute(text("SELECT * FROM user_session WHERE session_hash = :h"),
                             {"h": session_hash}).mappings().first()
        return SessionRecord(row["subject_id"], row["display_name"], row["csrf_token"], row["expires_at"]) \
            if row else None

    def delete_session(self, session_hash: str) -> None:
        self.c.execute(text("DELETE FROM user_session WHERE session_hash = :h"), {"h": session_hash})


class SqlPolicies:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def active(self) -> PolicyRules:
        row = self.c.execute(text("SELECT policy_version, validated_rules FROM policy_version "
                                  "WHERE installed_at IS NOT NULL ORDER BY installed_at DESC LIMIT 1")).first()
        if row is None:
            raise RuntimeError("No hay una versión de política instalada (ejecute make seed).")
        return parse_rules(row[0], row[1])

    def get(self, version: str) -> PolicyRules | None:
        row = self.c.execute(text("SELECT policy_version, validated_rules FROM policy_version "
                                  "WHERE policy_version = :v"), {"v": version}).first()
        return parse_rules(row[0], row[1]) if row else None

    def install(self, version: str, now: datetime) -> None:
        self.c.execute(text("UPDATE policy_version SET installed_at = :now WHERE policy_version = :v"),
                       {"v": version, "now": now})

    def list(self) -> list[dict[str, Any]]:
        rows = self.c.execute(text("SELECT policy_version, content_hash, installed_at, validated_rules "
                                   "FROM policy_version ORDER BY policy_version")).mappings()
        return [{"policyVersion": r["policy_version"], "contentHash": r["content_hash"],
                 "installedAt": r["installed_at"].isoformat() if r["installed_at"] else None,
                 "rules": r["validated_rules"]} for r in rows]


class SqlOverdue:
    def __init__(self, conn: Connection) -> None:
        self.c = conn

    def record(self, work_order_id: str, threshold: str, now: datetime) -> bool:
        row = self.c.execute(text("""
            INSERT INTO overdue_alert (work_order_id, threshold, detected_at) VALUES (:id, :t, :now)
            ON CONFLICT DO NOTHING RETURNING work_order_id
        """), {"id": work_order_id, "t": threshold, "now": now}).first()
        return row is not None


# --------------------------------------------------------------------------- Unit of Work


class SqlUnitOfWork:
    """Una conexión y una transacción por caso de uso; sin commit explícito, todo se revierte."""

    def __init__(self, engine: Engine, faults: FaultInjector | None = None) -> None:
        self._engine = engine
        self.faults = faults or NoFaults()

    def __enter__(self) -> "SqlUnitOfWork":
        self._conn = self._engine.connect()
        self._tx = self._conn.begin()
        c = self._conn
        self.requests, self.work_orders, self.triage = SqlRequests(c), SqlWorkOrders(c), SqlTriage(c)
        self.work_log, self.audit, self.outbox = SqlWorkLog(c), SqlAudit(c), SqlOutbox(c)
        self.receipts, self.locations, self.access = SqlReceipts(c), SqlLocations(c), SqlAccess(c)
        self.policies, self.overdue = SqlPolicies(c), SqlOverdue(c)
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if self._tx.is_active:
                self._tx.rollback()
        finally:
            self._conn.close()

    def commit(self) -> None:
        self._tx.commit()


class SqlUnitOfWorkFactory:
    def __init__(self, engine: Engine, faults: FaultInjector | None = None) -> None:
        self.engine = engine
        self.faults = faults

    def __call__(self) -> SqlUnitOfWork:
        return SqlUnitOfWork(self.engine, self.faults)


# --------------------------------------------------------------------------- outbox del worker


class SqlDeliveryStore:
    """Reclamo con `FOR UPDATE SKIP LOCKED` y lease (E2-ADR-004; §6.7). Transacciones cortas."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim(self, now: datetime, lease_seconds: float, batch: int) -> list[OutboxRow]:
        with self._engine.begin() as c:
            rows = c.execute(text("""
                WITH due AS (
                    SELECT event_id FROM outbox_event
                    WHERE (delivery_state IN ('PENDING', 'RETRY_WAIT') AND next_attempt_at <= :now)
                       OR (delivery_state = 'LEASED' AND lease_expires_at <= :now)
                    ORDER BY created_at
                    LIMIT :batch
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE outbox_event o
                SET delivery_state = 'LEASED', lease_token = gen_random_uuid(),
                    lease_expires_at = :expires, attempt_count = o.attempt_count + 1
                FROM due WHERE o.event_id = due.event_id
                RETURNING o.*
            """), {"now": now, "batch": batch, "expires": now + timedelta(seconds=lease_seconds)}).mappings()
            return [OutboxRow(str(r["event_id"]), r["event_type"], r["aggregate_type"], r["aggregate_id"],
                              r["aggregate_version"], r["payload"], r["recipient_ref"], r["channel"],
                              r["attempt_count"], str(r["lease_token"]), r["correlation_id"], r["created_at"])
                    for r in rows]

    def _finish(self, sql: str, params: dict[str, Any]) -> bool:
        with self._engine.begin() as c:
            return c.execute(text(sql + " WHERE event_id = :id AND lease_token = :tok "
                                        "AND delivery_state = 'LEASED'"), params).rowcount == 1

    def mark_delivered(self, event_id: str, lease_token: str, now: datetime) -> bool:
        return self._finish("UPDATE outbox_event SET delivery_state = 'DELIVERED', delivered_at = :now, "
                            "lease_token = NULL, lease_expires_at = NULL, last_error = NULL",
                            {"id": event_id, "tok": lease_token, "now": now})

    def mark_retry(self, event_id: str, lease_token: str, next_attempt_at: datetime, error: str) -> bool:
        return self._finish("UPDATE outbox_event SET delivery_state = 'RETRY_WAIT', next_attempt_at = :next, "
                            "lease_token = NULL, lease_expires_at = NULL, last_error = :err",
                            {"id": event_id, "tok": lease_token, "next": next_attempt_at, "err": error})

    def mark_dead(self, event_id: str, lease_token: str, error: str) -> bool:
        return self._finish("UPDATE outbox_event SET delivery_state = 'DEAD_LETTER', lease_token = NULL, "
                            "lease_expires_at = NULL, last_error = :err",
                            {"id": event_id, "tok": lease_token, "err": error})

