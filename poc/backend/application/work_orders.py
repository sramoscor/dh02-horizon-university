"""Órdenes de Trabajo y Asignación (E2-FR-04/05/06; §6.6 tabla de transiciones; E2-AS-01/02/04)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.application.authorization import ROLE_FOR_ACTOR, AuthorizationService, Principal
from backend.application.commands import CommandContext, CommandResult, CommandRunner, emit_event, record_audit
from backend.application.ports import Clock, Location, UnitOfWork, UnitOfWorkFactory
from backend.application.requests import SUPERVISORS
from backend.domain.enums import (
    SLA_PAUSING_BLOCKS,
    BlockType,
    ConfirmationOutcome,
    Role,
    WorkOrderStatus,
)
from backend.domain.errors import ActionForbidden, InvalidStateTransition, ResourceNotFound, ValidationFailed
from backend.domain.model import WorkOrder
from backend.domain.policies import AssignmentPolicy, PolicyRules, SlaPolicy
from backend.domain.transitions import (
    WORK_ORDER_TRANSITIONS,
    WORK_RECORDERS,
    WORK_RECORDING_STATES,
    Actor,
    plan_transition,
)

WS = WorkOrderStatus


@dataclass
class OrderContext:
    """Todo lo necesario para autorizar una operación sobre una orden."""

    wo: WorkOrder
    location: Location
    requester_id: str
    relations: set[Actor]


def work_order_dto(wo: WorkOrder, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "workOrderId": wo.work_order_id,
        "requestId": wo.request_id,
        "triageId": wo.triage_id,
        "locationId": wo.location_id,
        "category": wo.category.value,
        "discriminator": wo.discriminator,
        "urgency": wo.urgency.value,
        "status": wo.status,
        "assigneeId": wo.assignee_id,
        "dueAt": wo.due_at.isoformat(),
        "policyVersion": wo.policy_version,
        "rowVersion": wo.row_version,
        "etag": wo.etag(),
        "createdAt": wo.created_at.isoformat(),
        "closedAt": wo.closed_at.isoformat() if wo.closed_at else None,
        **(extra or {}),
    }


def apply_assignment(
    uow: UnitOfWork,
    ctx: CommandContext,
    now: datetime,
    wo: WorkOrder,
    rules: PolicyRules,
    assignee_id: str | None,
    reason: str | None,
    policy: AssignmentPolicy,
) -> tuple[str, str | None, str | None]:
    """E2-AS-01: especialidad + disponibilidad, menor carga; impuesta exige motivo.

    Devuelve (elegido, anterior, recomendado). Quien llama audita la transición una sola vez.
    """
    eligible = policy.eligible(rules, wo.category, uow.work_log.technician_candidates())
    recommended = eligible[0].subject_id if eligible else None
    chosen = assignee_id or recommended
    if chosen is None:
        raise ValidationFailed(
            "Ningún técnico cumple especialidad y disponibilidad; la orden queda sin asignar y visible al supervisor.",
            fields={"assigneeId": "sin candidatos"},
        )
    if chosen not in {c.subject_id for c in eligible}:
        raise ValidationFailed("El técnico no tiene la especialidad o no está disponible.",
                               fields={"assigneeId": "no elegible"})
    previous = uow.work_log.active_assignee(wo.work_order_id)
    if previous == chosen:
        raise ValidationFailed("La orden ya está asignada a ese técnico.", fields={"assigneeId": "sin cambio"})
    if chosen != recommended and not (reason and reason.strip()):
        raise ValidationFailed("Una asignación distinta a la sugerida por la política exige motivo.",
                               fields={"reason": "obligatorio"})
    uow.work_log.end_active_assignments(wo.work_order_id, now)
    uow.work_log.add_assignment(wo.work_order_id, chosen, ctx.principal.subject_id, reason, now)
    wo.assignee_id = chosen
    return chosen, previous, recommended


class WorkOrderService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        runner: CommandRunner,
        clock: Clock,
        authz: AuthorizationService,
    ) -> None:
        self._uow_factory = uow_factory
        self._runner = runner
        self._clock = clock
        self._authz = authz
        self.assignment_policy = AssignmentPolicy()
        self.sla_policy = SlaPolicy()

    # ------------------------------------------------------------------ contexto y autorización

    def _context(self, uow: UnitOfWork, principal: Principal, work_order_id: str, for_update: bool) -> OrderContext:
        wo = uow.work_orders.get(work_order_id, for_update=for_update)
        if wo is None:
            raise ResourceNotFound()
        loc = uow.locations.get(wo.location_id)
        req = uow.requests.get(wo.request_id)
        rel = self._authz.work_order_relations(principal, wo, loc, req.requester_id, wo.assignee_id)
        return OrderContext(wo, loc, req.requester_id, rel)

    def _transition(
        self,
        ctx: CommandContext,
        operation: str,
        work_order_id: str,
        command: str | Callable[[WorkOrder], str],
        payload: dict[str, Any],
        effects: Callable[[UnitOfWork, OrderContext, datetime, str], dict[str, Any] | None],
        reason: str | None = None,
        action: str | None = None,
    ) -> CommandResult:
        """Plantilla de E2-ADR-007: autorizar → guarda de estado/versión → efectos → auditoría + outbox."""

        def handler(uow: UnitOfWork) -> CommandResult:
            now = self._clock.now()
            oc = self._context(uow, ctx.principal, work_order_id, for_update=True)
            cmd = command(oc.wo) if callable(command) else command
            transition, actor = plan_transition(WORK_ORDER_TRANSITIONS, cmd, oc.wo.status, oc.relations, reason)
            role = ROLE_FOR_ACTOR[actor]
            before, _ = oc.wo.apply(transition, ctx.expected_version)
            extra = effects(uow, oc, now, role) or {}
            uow.work_orders.save(oc.wo)
            record_audit(uow, ctx, now, entity_type="WorkOrder", entity_id=oc.wo.work_order_id,
                         action=action or f"WORK_ORDER_{cmd.upper()}", actor_role=role, before=before,
                         after=oc.wo.snapshot() | extra.get("audit", {}), reason=reason)
            return CommandResult(work_order_dto(oc.wo, extra.get("body")), etag=oc.wo.etag())

        return self._runner.run(ctx, operation, {"id": work_order_id, **payload}, handler)

    def _emit(self, uow, ctx, now, wo: WorkOrder, event_type: str, payload: dict, recipients: list[str]) -> None:
        emit_event(uow, ctx, now, event_type=event_type, aggregate_type="WorkOrder",
                   aggregate_id=wo.work_order_id, aggregate_version=wo.row_version, request_id=wo.request_id,
                   payload={"status": wo.status, **payload}, recipients=recipients)

    # ------------------------------------------------------------------ comandos

    def assign(self, ctx: CommandContext, work_order_id: str, assignee_id: str | None, reason: str | None):
        def command(wo: WorkOrder) -> str:
            if wo.status in (WS.CREATED, WS.REOPENED):
                return "assign"
            if wo.status == WS.ASSIGNED:
                return "reassign"
            return "supervisor_reassign"

        def effects(uow, oc: OrderContext, now, role):
            rules = uow.policies.get(oc.wo.policy_version)  # la orden conserva su versión (E2-ADR-005)
            chosen, previous, recommended = apply_assignment(uow, ctx, now, oc.wo, rules, assignee_id, reason,
                                                             self.assignment_policy)
            self._emit(uow, ctx, now, oc.wo, "WorkOrderAssigned", {"assigneeId": chosen},
                       [chosen, oc.requester_id])
            return {"audit": {"previousAssigneeId": previous, "recommendedAssigneeId": recommended}}

        # La reasignación exige motivo (tabla §6.6); la primera asignación solo si se impone otro técnico.
        return self._transition(ctx, "work_order.assign", work_order_id, command,
                                {"assigneeId": assignee_id, "reason": reason}, effects, reason=reason,
                                action="WORK_ORDER_ASSIGNMENT")

    def start(self, ctx: CommandContext, work_order_id: str) -> CommandResult:
        return self._transition(ctx, "work_order.start", work_order_id, "start", {}, lambda *a: None)

    def block(self, ctx: CommandContext, work_order_id: str, block_type: str, detail: str) -> CommandResult:
        try:
            btype = BlockType(block_type)
        except ValueError as exc:
            raise ValidationFailed("Tipo de bloqueo obligatorio y válido.", fields={"blockType": "inválido"}) from exc
        if not (detail and detail.strip()):
            raise ValidationFailed("El detalle del bloqueo es obligatorio.", fields={"detail": "obligatorio"})

        def effects(uow, oc: OrderContext, now, role):
            block_id = uow.work_log.add_block(oc.wo.work_order_id, btype, detail.strip()[:1000],
                                              ctx.principal.subject_id, now)
            # BR-06: bloquear notifica al supervisor.
            self._emit(uow, ctx, now, oc.wo, "WorkOrderBlocked", {"blockType": btype.value}, [SUPERVISORS])
            paused = btype in SLA_PAUSING_BLOCKS
            return {"audit": {"blockType": btype.value, "blockId": block_id, "slaPaused": paused},
                    "body": {"blockId": block_id, "slaPaused": paused}}

        return self._transition(ctx, "work_order.block", work_order_id, "block",
                                {"blockType": block_type, "detail": detail}, effects)

    def resolve_block(self, ctx: CommandContext, work_order_id: str, block_id: str) -> CommandResult:
        def effects(uow, oc: OrderContext, now, role):
            block = uow.work_log.open_block(oc.wo.work_order_id)
            if block is None or block["block_id"] != block_id:
                raise InvalidStateTransition("El bloqueo indicado no está abierto en esta orden.")
            uow.work_log.close_block(block_id, ctx.principal.subject_id, now)
            shifted = None
            if BlockType(block["block_type"]) in SLA_PAUSING_BLOCKS:
                # E2-AS-04: el SLA se pausa; due_at se desplaza por la duración del bloqueo.
                previous_due = oc.wo.due_at
                oc.wo.due_at = self.sla_policy.shift_for_pause(oc.wo.due_at, block["started_at"], now)
                shifted = {"previousDueAt": previous_due.isoformat(), "dueAt": oc.wo.due_at.isoformat()}
            return {"audit": {"blockId": block_id, "slaShift": shifted}, "body": {"slaShift": shifted}}

        return self._transition(ctx, "work_order.resolve_block", work_order_id, "resolve_block",
                                {"blockId": block_id}, effects)

    def complete(self, ctx: CommandContext, work_order_id: str) -> CommandResult:
        def effects(uow, oc: OrderContext, now, role):
            if uow.work_log.observation_count(oc.wo.work_order_id) == 0:
                raise ValidationFailed("Registre al menos una observación antes de finalizar (§6.6).",
                                       fields={"observations": "obligatorio"})
            self._emit(uow, ctx, now, oc.wo, "WorkOrderPendingConfirmation", {},
                       [oc.requester_id, oc.location.building_representative_id])
            return None

        return self._transition(ctx, "work_order.complete", work_order_id, "complete", {}, effects)

    def confirm(self, ctx: CommandContext, work_order_id: str, outcome: str, reason: str | None):
        if outcome not in ("CONFIRMED", "REOPEN"):
            raise ValidationFailed("outcome debe ser CONFIRMED o REOPEN.", fields={"outcome": "inválido"})
        reopening = outcome == "REOPEN"

        def effects(uow, oc: OrderContext, now, role):
            if reopening:
                uow.work_log.add_confirmation(oc.wo.work_order_id, ctx.principal.subject_id,
                                              ConfirmationOutcome.REJECTED_REOPEN, reason, now)
                previous = uow.work_log.active_assignee(oc.wo.work_order_id)
                uow.work_log.end_active_assignments(oc.wo.work_order_id, now)
                oc.wo.assignee_id = None
                self._emit(uow, ctx, now, oc.wo, "WorkOrderReopened", {}, [SUPERVISORS, previous])
            else:
                uow.work_log.add_confirmation(oc.wo.work_order_id, ctx.principal.subject_id,
                                              ConfirmationOutcome.CONFIRMED, reason, now)
                oc.wo.closed_at = now
                uow.work_log.end_active_assignments(oc.wo.work_order_id, now)
                self._emit(uow, ctx, now, oc.wo, "WorkOrderClosed", {}, [oc.requester_id])
            return None

        return self._transition(ctx, "work_order.confirm", work_order_id, "reopen" if reopening else "confirm",
                                {"outcome": outcome, "reason": reason}, effects, reason=reason,
                                action="WORK_ORDER_REOPENED" if reopening else "WORK_ORDER_CONFIRMED")

    def admin_close(self, ctx: CommandContext, work_order_id: str, reason: str | None) -> CommandResult:
        """Cierre administrativo: actor de sistema al vencer el plazo, o supervisor con motivo (E2-AS-02)."""
        is_system = ctx.principal.subject_id.startswith("system:")
        if not is_system and not (reason and reason.strip()):
            raise ValidationFailed("El supervisor debe indicar el motivo.", fields={"reason": "obligatorio"})

        def effects(uow, oc: OrderContext, now, role):
            uow.work_log.add_confirmation(oc.wo.work_order_id, ctx.principal.subject_id,
                                          ConfirmationOutcome.ADMIN_CLOSED, reason, now)
            oc.wo.closed_at = now
            uow.work_log.end_active_assignments(oc.wo.work_order_id, now)
            self._emit(uow, ctx, now, oc.wo, "WorkOrderAdminClosed", {}, [oc.requester_id])
            return None

        return self._transition(ctx, "work_order.admin_close", work_order_id, "admin_close",
                                {"reason": reason}, effects, reason=reason)

    def cancel(self, ctx: CommandContext, work_order_id: str, reason: str | None) -> CommandResult:
        def effects(uow, oc: OrderContext, now, role):
            oc.wo.closed_at = now
            uow.work_log.end_active_assignments(oc.wo.work_order_id, now)
            self._emit(uow, ctx, now, oc.wo, "WorkOrderCancelled", {}, [oc.requester_id])
            return None

        return self._transition(ctx, "work_order.cancel", work_order_id, "cancel", {"reason": reason},
                                effects, reason=reason)

    def record_work(self, ctx: CommandContext, work_order_id: str, kind: str, data: dict[str, Any]):
        """Materiales y observaciones (E2-FR-05; BR-13: coordinador/supervisor registran por contratistas)."""

        def handler(uow: UnitOfWork) -> CommandResult:
            now = self._clock.now()
            oc = self._context(uow, ctx.principal, work_order_id, for_update=True)
            granted = sorted(oc.relations & WORK_RECORDERS, key=list(Actor).index)
            if not granted:
                raise ActionForbidden()
            if oc.wo.status not in WORK_RECORDING_STATES:
                raise InvalidStateTransition(f"No se registra trabajo en estado {oc.wo.status}.")
            role = ROLE_FOR_ACTOR[granted[0]]
            if kind == "material":
                name = (data.get("materialName") or "").strip()
                qty = data.get("quantity")
                if not name or not isinstance(qty, int) or qty <= 0:
                    raise ValidationFailed("Material y cantidad positiva obligatorios.",
                                           fields={"materialName": "obligatorio", "quantity": "> 0"})
                item_id = uow.work_log.add_material(oc.wo.work_order_id, name[:200], qty, ctx.principal.subject_id, now)
                after = {"materialName": name, "quantity": qty}
                action = "MATERIAL_RECORDED"
            else:
                note = (data.get("note") or "").strip()
                if not note:
                    raise ValidationFailed("La observación no puede estar vacía.", fields={"note": "obligatorio"})
                ref = data.get("attachmentRef")  # E2-AS-08: referencia simulada, sin archivo real
                item_id = uow.work_log.add_observation(oc.wo.work_order_id, ctx.principal.subject_id,
                                                       note[:2000], ref, now)
                after = {"observationId": item_id, "attachmentRef": ref}
                action = "OBSERVATION_RECORDED"
            record_audit(uow, ctx, now, entity_type="WorkOrder", entity_id=oc.wo.work_order_id, action=action,
                         actor_role=role, before=None, after=after)
            return CommandResult({"id": item_id, "workOrderId": oc.wo.work_order_id, **after}, created=True)

        return self._runner.run(ctx, f"work_order.{kind}", {"id": work_order_id, **data}, handler)

    # ------------------------------------------------------------------ lecturas

    def get(self, principal: Principal, work_order_id: str) -> dict[str, Any]:
        with self._uow_factory() as uow:
            oc = self._context(uow, principal, work_order_id, for_update=False)
            if not oc.relations:
                raise ActionForbidden()
            details = uow.work_log.details(work_order_id)
            internal = bool(oc.relations & {Actor.COORDINATOR, Actor.SUPERVISOR, Actor.ASSIGNED_TECHNICIAN})
            if not internal:
                # §9.1: solicitante y representante no ven notas internas.
                details = {k: v for k, v in details.items() if k in ("blocks", "confirmations")}
            open_block = next((b for b in details.get("blocks", []) if b["endedAt"] is None), None)
            sla = "PAUSED" if open_block and open_block["blockType"] in {b.value for b in SLA_PAUSING_BLOCKS} else (
                "STOPPED" if oc.wo.status in (WS.CLOSED, WS.ADMIN_CLOSED, WS.CANCELLED)
                else ("OVERDUE" if oc.wo.due_at < self._clock.now() else "RUNNING"))
            extra = {
                "location": {"locationId": oc.location.location_id, "displayName": oc.location.display_name,
                             "buildingId": oc.location.building_id, "campus": oc.location.campus},
                "slaState": sla,
                "yourRelations": sorted(r.value for r in oc.relations),
                **details,
            }
            if internal:
                extra["safetyFlag"] = oc.wo.safety_flag
            return work_order_dto(oc.wo, extra)

    def history(self, principal: Principal, work_order_id: str) -> list[dict[str, Any]]:
        """QS-03 / E2-FR-09: autor, rol, momento, estado anterior/posterior, motivo y correlación."""
        with self._uow_factory() as uow:
            oc = self._context(uow, principal, work_order_id, for_update=False)
            self._authz.ensure_can_read_history(principal, oc.wo, oc.location, oc.requester_id, oc.wo.assignee_id)
            return uow.audit.for_entities([oc.wo.work_order_id, oc.wo.request_id])

    def search(self, principal: Principal, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """T-FUN-05: resultados acotados por rol y alcance."""
        with self._uow_factory() as uow:
            orders = uow.work_orders.search(filters, limit=int(filters.get("limit") or 100))
            locs = {loc.location_id: loc for loc in uow.locations.all()}
            visible = []
            for wo in orders:
                loc = locs[wo.location_id]
                if principal.has_role(Role.DIRECTOR) and not filters.get("mine"):
                    visible.append(wo)
                    continue
                req = uow.requests.get(wo.request_id)
                if self._authz.work_order_relations(principal, wo, loc, req.requester_id, wo.assignee_id):
                    visible.append(wo)
            now = self._clock.now()
            return [work_order_dto(wo, {"overdue": wo.due_at < now and wo.status in
                                        {s.value for s in WS if s not in (WS.CLOSED, WS.ADMIN_CLOSED, WS.CANCELLED)},
                                        "locationName": locs[wo.location_id].display_name})
                    for wo in visible]

    def workload(self, principal: Principal) -> list[dict[str, Any]]:
        self._authz.ensure_role(principal, Role.SUPERVISOR, Role.COORDINATOR)
        with self._uow_factory() as uow:
            return uow.work_log.workload()

    def assignment_candidates(self, principal: Principal, work_order_id: str) -> list[dict[str, Any]]:
        with self._uow_factory() as uow:
            oc = self._context(uow, principal, work_order_id, for_update=False)
            if not oc.relations & {Actor.COORDINATOR, Actor.SUPERVISOR}:
                raise ActionForbidden()
            rules = uow.policies.get(oc.wo.policy_version)
            ranked = self.assignment_policy.eligible(rules, oc.wo.category, uow.work_log.technician_candidates())
            return [{"subjectId": c.subject_id, "specialty": c.specialty, "activeLoad": c.active_load,
                     "recommended": i == 0} for i, c in enumerate(ranked)]
