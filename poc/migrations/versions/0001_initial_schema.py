"""Esquema inicial de DH-02 (§6.2 modelo relacional, §6.3 diccionario, §6.4 invariantes).

Revision ID: 0001
Revises:
Create Date: 2026-11-07
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

REQUEST_STATUSES = (
    "DRAFT", "SUBMITTED", "UNDER_TRIAGE", "NEEDS_INFO", "APPROVED",
    "REJECTED", "REDIRECTED", "DUPLICATE_LINKED", "WITHDRAWN",
)
WORK_ORDER_STATUSES = (
    "CREATED", "ASSIGNED", "IN_PROGRESS", "BLOCKED", "PENDING_CONFIRMATION",
    "REOPENED", "CLOSED", "ADMIN_CLOSED", "CANCELLED",
)
# E2-ADR-003: estados activos del índice único parcial (backend/domain/enums.py ACTIVE_WORK_ORDER_STATUSES)
ACTIVE = ("CREATED", "ASSIGNED", "IN_PROGRESS", "BLOCKED", "PENDING_CONFIRMATION", "REOPENED")
URGENCIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
CATEGORIES = ("PLUMBING", "ELECTRICAL", "HVAC", "GENERAL")
ROLES = ("REQUESTER", "COORDINATOR", "TECHNICIAN", "SUPERVISOR", "BUILDING_REP", "DIRECTOR", "OPERATIONS")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE location_projection (
        location_id text PRIMARY KEY,
        building_id text NOT NULL,
        campus text NOT NULL,
        display_name text NOT NULL,
        active boolean NOT NULL,
        restricted_access boolean NOT NULL DEFAULT false,
        building_representative_id text,
        directory_version text NOT NULL,
        source_revision integer NOT NULL,
        fetched_at timestamptz NOT NULL
    );

    CREATE TABLE policy_version (
        policy_version text PRIMARY KEY,
        validated_rules jsonb NOT NULL,
        content_hash text NOT NULL,
        installed_at timestamptz
    );

    CREATE TABLE role_assignment (
        role_assignment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        subject_id text NOT NULL,
        role text NOT NULL CHECK (role IN {_in(ROLES)}),
        scope_type text NOT NULL CHECK (scope_type IN ('GLOBAL', 'CAMPUS', 'BUILDING', 'OWN')),
        scope_id text,
        active boolean NOT NULL DEFAULT true,
        -- Hallazgo H-05: scope_id es NULL en alcances GLOBAL/OWN; sin NULLS NOT DISTINCT la semilla duplicaba.
        CONSTRAINT uq_role_assignment UNIQUE NULLS NOT DISTINCT (subject_id, role, scope_type, scope_id)
    );

    CREATE TABLE technician_profile (
        subject_id text PRIMARY KEY,
        specialty text NOT NULL,
        available boolean NOT NULL DEFAULT true
    );

    CREATE TABLE user_session (
        session_hash text PRIMARY KEY,
        subject_id text NOT NULL,
        display_name text NOT NULL,
        csrf_token text NOT NULL,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL
    );

    CREATE SEQUENCE request_number_seq START 2041;
    CREATE SEQUENCE work_order_number_seq START 3001;
    CREATE SEQUENCE triage_number_seq START 9901;

    CREATE TABLE maintenance_request (
        request_id text PRIMARY KEY,
        requester_id text NOT NULL,
        location_id text NOT NULL REFERENCES location_projection (location_id),
        description text NOT NULL CHECK (char_length(description) BETWEEN 1 AND 2000),
        suggested_category text CHECK (suggested_category IN {_in(CATEGORIES)}),
        suggested_urgency text NOT NULL CHECK (suggested_urgency IN {_in(URGENCIES)}),
        safety_flag boolean NOT NULL DEFAULT false,
        status text NOT NULL CHECK (status IN {_in(REQUEST_STATUSES)}),
        linked_work_order_id text,
        additional_info text CHECK (char_length(additional_info) <= 2000),
        row_version integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL,
        submitted_at timestamptz,
        updated_at timestamptz NOT NULL
    );
    CREATE INDEX ix_request_requester ON maintenance_request (requester_id);
    CREATE INDEX ix_request_status ON maintenance_request (status);

    CREATE TABLE triage_record (
        triage_id text PRIMARY KEY,
        request_id text NOT NULL REFERENCES maintenance_request (request_id),
        coordinator_id text NOT NULL,
        action text NOT NULL CHECK (action IN
            ('APPROVE_CREATE_WO', 'REJECT', 'REDIRECT', 'LINK_TO_EXISTING', 'REQUEST_INFO')),
        category text CHECK (category IN {_in(CATEGORIES)}),
        suggested_urgency text,
        validated_urgency text CHECK (validated_urgency IN {_in(URGENCIES)}),
        safety_flag boolean NOT NULL DEFAULT false,
        reason text CHECK (char_length(reason) <= 2000),
        discriminator text,
        policy_version text NOT NULL REFERENCES policy_version (policy_version),
        decided_at timestamptz NOT NULL,
        correlation_id text
    );

    CREATE TABLE work_order (
        work_order_id text PRIMARY KEY,
        request_id text NOT NULL REFERENCES maintenance_request (request_id),
        triage_id text NOT NULL REFERENCES triage_record (triage_id),
        location_id text NOT NULL REFERENCES location_projection (location_id),
        category text NOT NULL CHECK (category IN {_in(CATEGORIES)}),
        discriminator text NOT NULL DEFAULT '' CHECK (char_length(discriminator) <= 80),
        urgency text NOT NULL CHECK (urgency IN {_in(URGENCIES)}),
        safety_flag boolean NOT NULL DEFAULT false,
        status text NOT NULL CHECK (status IN {_in(WORK_ORDER_STATUSES)}),
        due_at timestamptz NOT NULL,
        policy_version text NOT NULL REFERENCES policy_version (policy_version),
        row_version integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL,
        closed_at timestamptz,
        -- BR-02 / E2-ADR-003 punto 1: una solicitud produce a lo sumo una orden
        CONSTRAINT uq_work_order_request UNIQUE (request_id)
    );
    -- BR-03 / E2-ADR-003 punto 2 / QS-01: índice único PARCIAL sobre órdenes activas
    CREATE UNIQUE INDEX uq_work_order_active_slot
        ON work_order (location_id, category, discriminator)
        WHERE status IN {_in(ACTIVE)};
    CREATE INDEX ix_work_order_status ON work_order (status);

    ALTER TABLE maintenance_request
        ADD CONSTRAINT fk_request_linked_work_order
        FOREIGN KEY (linked_work_order_id) REFERENCES work_order (work_order_id);

    CREATE TABLE assignment (
        assignment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        work_order_id text NOT NULL REFERENCES work_order (work_order_id),
        assignee_id text NOT NULL,
        assigned_by text NOT NULL,
        reason text,
        is_active boolean NOT NULL,
        assigned_at timestamptz NOT NULL,
        ended_at timestamptz
    );
    -- §6.3: una sola asignación activa por técnico y orden
    CREATE UNIQUE INDEX uq_assignment_active ON assignment (work_order_id, assignee_id) WHERE is_active;

    CREATE TABLE visit (
        visit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        work_order_id text NOT NULL REFERENCES work_order (work_order_id),
        scheduled_for timestamptz,
        performed_at timestamptz,
        outcome text
    );

    CREATE TABLE block_event (
        block_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        work_order_id text NOT NULL REFERENCES work_order (work_order_id),
        block_type text NOT NULL CHECK (block_type IN ('MATERIALS', 'ACCESS', 'CONTRACTOR', 'APPROVAL')),
        detail text NOT NULL CHECK (char_length(detail) BETWEEN 1 AND 1000),
        started_by text NOT NULL,
        started_at timestamptz NOT NULL,
        resolved_by text,
        ended_at timestamptz
    );
    CREATE UNIQUE INDEX uq_block_open ON block_event (work_order_id) WHERE ended_at IS NULL;

    CREATE TABLE material_usage (
        usage_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        work_order_id text NOT NULL REFERENCES work_order (work_order_id),
        material_name text NOT NULL CHECK (char_length(material_name) BETWEEN 1 AND 200),
        quantity integer NOT NULL CHECK (quantity > 0),
        recorded_by text NOT NULL,
        recorded_at timestamptz NOT NULL
    );

    CREATE TABLE observation (
        observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        work_order_id text NOT NULL REFERENCES work_order (work_order_id),
        author_id text NOT NULL,
        note text NOT NULL CHECK (char_length(note) BETWEEN 1 AND 2000),
        attachment_ref text,
        recorded_at timestamptz NOT NULL
    );

    CREATE TABLE completion_confirmation (
        confirmation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        work_order_id text NOT NULL REFERENCES work_order (work_order_id),
        confirmed_by text NOT NULL,
        outcome text NOT NULL CHECK (outcome IN ('CONFIRMED', 'REJECTED_REOPEN', 'ADMIN_CLOSED')),
        reason text,
        decided_at timestamptz NOT NULL
    );

    CREATE TABLE audit_event (
        audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        audit_seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
        entity_type text NOT NULL,
        entity_id text NOT NULL,
        actor_id text NOT NULL,
        actor_role text NOT NULL,
        action text NOT NULL,
        before_state jsonb,
        after_state jsonb,
        reason text,
        correlation_id text,
        occurred_at timestamptz NOT NULL
    );
    CREATE INDEX ix_audit_entity ON audit_event (entity_type, entity_id, audit_seq);

    -- §6.3: AuditEvent es solo anexado para los roles de la aplicación (QS-03; riesgo residual R-014)
    CREATE FUNCTION audit_event_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_event es de solo anexado';
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_audit_event_append_only
        BEFORE UPDATE OR DELETE ON audit_event
        FOR EACH ROW EXECUTE FUNCTION audit_event_append_only();

    CREATE TABLE outbox_event (
        event_id uuid PRIMARY KEY,
        event_type text NOT NULL,
        schema_version integer NOT NULL,
        aggregate_type text NOT NULL,
        aggregate_id text NOT NULL,
        aggregate_version integer NOT NULL,
        request_id text,
        payload jsonb NOT NULL,
        recipient_ref text NOT NULL,
        channel text NOT NULL,
        delivery_state text NOT NULL
            CHECK (delivery_state IN ('PENDING', 'LEASED', 'RETRY_WAIT', 'DELIVERED', 'DEAD_LETTER')),
        attempt_count integer NOT NULL DEFAULT 0,
        next_attempt_at timestamptz NOT NULL,
        lease_token uuid,
        lease_expires_at timestamptz,
        last_error text,
        delivered_at timestamptz,
        correlation_id text,
        created_at timestamptz NOT NULL
    );
    CREATE INDEX ix_outbox_claim ON outbox_event (delivery_state, next_attempt_at);
    CREATE INDEX ix_outbox_aggregate ON outbox_event (aggregate_id);
    CREATE INDEX ix_outbox_request ON outbox_event (request_id);

    CREATE TABLE command_receipt (
        idempotency_key text PRIMARY KEY,
        actor_id text NOT NULL,
        operation text NOT NULL,
        fingerprint text NOT NULL,
        response jsonb NOT NULL,
        created_at timestamptz NOT NULL
    );

    -- V-SEQ-04: una restricción por orden y umbral evita alertas duplicadas
    CREATE TABLE overdue_alert (
        work_order_id text NOT NULL REFERENCES work_order (work_order_id),
        threshold text NOT NULL,
        detected_at timestamptz NOT NULL,
        PRIMARY KEY (work_order_id, threshold)
    );
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS overdue_alert, command_receipt, outbox_event, audit_event,
        completion_confirmation, observation, material_usage, block_event, visit, assignment CASCADE;
    ALTER TABLE IF EXISTS maintenance_request DROP CONSTRAINT IF EXISTS fk_request_linked_work_order;
    DROP TABLE IF EXISTS work_order, triage_record, maintenance_request, user_session,
        technician_profile, role_assignment, policy_version, location_projection CASCADE;
    DROP FUNCTION IF EXISTS audit_event_append_only();
    DROP SEQUENCE IF EXISTS request_number_seq, work_order_number_seq, triage_number_seq;
    """)
