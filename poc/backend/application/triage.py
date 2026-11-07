"""TriageApplicationService — servicio crítico (§5.3; E2-FR-02/03; E2-ADR-001/003/007; QS-01).

Aplica la decisión de triaje de forma autorizada y atómica. Comportamiento prohibido (§5.3): llamar a
notificaciones dentro de la transacción, aceptar roles o propietario desde el cliente, auditar después
del commit.
"""

from dataclasses import dataclass
from typing import Any

from backend.application.authorization import ROLE_FOR_ACTOR, AuthorizationService, Principal
from backend.application.commands import CommandContext, CommandResult, CommandRunner, emit_event, record_audit
from backend.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from backend.application.requests import COORDINATORS, SUPERVISORS, LocationService, _enum, request_dto
from backend.application.work_orders import apply_assignment, work_order_dto
from backend.domain.enums import Category, TriageAction, Urgency, WorkOrderStatus
from backend.domain.errors import ActionForbidden, ResourceNotFound, ValidationFailed
from backend.domain.model import WorkOrder
from backend.domain.policies import AssignmentPolicy, DuplicateCandidatePolicy, SlaPolicy, UrgencyPolicy
from backend.domain.transitions import REQUEST_TRANSITIONS, WORK_ORDER_TRANSITIONS, Actor, plan_transition

ACTION_TO_COMMAND = {
    TriageAction.APPROVE_CREATE_WO: "approve",
    TriageAction.REJECT: "reject",
    TriageAction.REDIRECT: "redirect",
    TriageAction.LINK_TO_EXISTING: "link",
    TriageAction.REQUEST_INFO: "request_info",
}


@dataclass(frozen=True)
class TriageDecision:
    action: TriageAction
    category: Category | None = None
    validated_urgency: Urgency | None = None
    safety_flag: bool = False
    discriminator: str = ""
    assignee_id: str | None = None
    reason: str | None = None
    existing_work_order_id: str | None = None

    @staticmethod
    def parse(data: dict[str, Any]) -> "TriageDecision":
        action = _enum(TriageAction, data.get("action"), "action")
        approve = action == TriageAction.APPROVE_CREATE_WO
        discriminator = (data.get("discriminator") or "").strip()
        if len(discriminator) > 80:
            raise ValidationFailed("discriminator demasiado largo.", fields={"discriminator": "máx. 80"})
        return TriageDecision(
            action=action,
            category=_enum(Category, data.get("category"), "category", required=approve),
            validated_urgency=_enum(Urgency, data.get("validatedUrgency"), "validatedUrgency", required=approve),
            safety_flag=bool(data.get("safetyFlag", False)),
            discriminator=discriminator,
            assignee_id=data.get("assigneeId") or None,
            reason=(data.get("reason") or data.get("comments") or None),
            existing_work_order_id=data.get("existingWorkOrderId") or None,
        )


class TriageApplicationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        runner: CommandRunner,
        clock: Clock,
        authz: AuthorizationService,
        locations: LocationService,
    ) -> None:
        self._uow_factory = uow_factory
        self._runner = runner
        self._clock = clock
        self._authz = authz
        self._locations = locations
        self.urgency_policy = UrgencyPolicy()
        self.sla_policy = SlaPolicy()
        self.assignment_policy = AssignmentPolicy()
        self.duplicate_policy = DuplicateCandidatePolicy()

    def start_review(self, ctx: CommandContext, request_id: str) -> CommandResult:
        """`SUBMITTED → UNDER_TRIAGE` (sin endpoint en §8.1; divergencia D-06)."""

        def handler(uow: UnitOfWork) -> CommandResult:
            now = self._clock.now()
            req = uow.requests.get(request_id, for_update=True)
            if req is None:
                raise ResourceNotFound()
            loc = uow.locations.get(req.location_id)
            rel = self._authz.request_relations(ctx.principal, req, loc)
            transition, actor = plan_transition(REQUEST_TRANSITIONS, "start_review", req.status, rel)
            before, after = req.apply(transition, ctx.expected_version)
            uow.requests.save(req, now)
            record_audit(uow, ctx, now, entity_type="MaintenanceRequest", entity_id=req.request_id,
                         action="TRIAGE_STARTED", actor_role=ROLE_FOR_ACTOR[actor], before=before, after=after)
            return CommandResult(request_dto(req, loc), etag=req.etag())

        return self._runner.run(ctx, "triage.start_review", {"id": request_id}, handler)

    def duplicate_candidates(self, principal: Principal, request_id: str, category: str | None) -> list[dict]:
        """Consulta asesora (E2-ADR-003 punto 3). Puede quedar obsoleta antes de insertar: la BD decide."""
        cat = _enum(Category, category, "category", required=False)
        with self._uow_factory() as uow:
            req = uow.requests.get(request_id)
            if req is None:
                raise ResourceNotFound()
            loc = uow.locations.get(req.location_id)
            if not self._authz.request_relations(principal, req, loc) & {Actor.COORDINATOR, Actor.SUPERVISOR}:
                raise ActionForbidden()
            found = self.duplicate_policy.candidates(
                request_id, cat, uow.work_orders.active_at_location(req.location_id),
                uow.requests.open_at_location(req.location_id),
            )
            return [{"kind": c.kind, "id": c.id, "status": c.status, "category": c.category,
                     "discriminator": c.discriminator} for c in found]

    def decide(self, ctx: CommandContext, request_id: str, data: dict[str, Any]) -> CommandResult:
        decision = TriageDecision.parse(data)

        def handler(uow: UnitOfWork) -> CommandResult:
            now = self._clock.now()
            # V-SEQ-01: iniciar transacción y bloquear la solicitud
            req = uow.requests.get(request_id, for_update=True)
            if req is None:
                raise ResourceNotFound()
            loc = uow.locations.get(req.location_id)
            rel = self._authz.request_relations(ctx.principal, req, loc)
            transition, actor = plan_transition(
                REQUEST_TRANSITIONS, ACTION_TO_COMMAND[decision.action], req.status, rel, decision.reason
            )
            role = ROLE_FOR_ACTOR[actor]
            rules = uow.policies.active()
            approve = decision.action == TriageAction.APPROVE_CREATE_WO
            if approve:
                # Verificar estado, versión y ubicación vigente
                self._locations.ensure_valid(uow, req.location_id, now)
            linked = None
            if decision.action == TriageAction.LINK_TO_EXISTING:
                linked = uow.work_orders.get(decision.existing_work_order_id or "")
                if linked is None or linked.status not in {s.value for s in WorkOrderStatus} - {
                    WorkOrderStatus.CLOSED, WorkOrderStatus.ADMIN_CLOSED, WorkOrderStatus.CANCELLED
                } or linked.location_id != req.location_id:
                    raise ValidationFailed("Debe vincularse a una orden activa en la misma ubicación (§6.6).",
                                           fields={"existingWorkOrderId": "inválida"})

            safety = req.safety_flag or decision.safety_flag
            urgency = (
                self.urgency_policy.decide(rules, decision.validated_urgency, safety) if approve else None
            )
            before, after = req.apply(transition, ctx.expected_version)
            if linked is not None:
                req.linked_work_order_id = linked.work_order_id
            uow.requests.save(req, now)

            triage_id = uow.triage.next_id()
            uow.triage.add({
                "triage_id": triage_id, "request_id": req.request_id, "coordinator_id": ctx.principal.subject_id,
                "action": decision.action.value,
                "category": (decision.category or (linked.category if linked else None)),
                "suggested_urgency": req.suggested_urgency.value,
                "validated_urgency": urgency.value if urgency else None, "safety_flag": safety,
                "reason": decision.reason, "discriminator": decision.discriminator if approve else None,
                "policy_version": rules.version, "decided_at": now, "correlation_id": ctx.correlation_id,
            })

            body: dict[str, Any] = {"triageId": triage_id, "requestId": req.request_id,
                                    "requestStatus": req.status, "policyVersion": rules.version}
            etag = req.etag()
            location = None
            if approve:
                wo = WorkOrder(
                    work_order_id=uow.work_orders.next_id(), request_id=req.request_id, triage_id=triage_id,
                    location_id=req.location_id, category=decision.category, discriminator=decision.discriminator,
                    urgency=urgency, safety_flag=safety, status=WorkOrderStatus.CREATED,
                    due_at=self.sla_policy.due_at(rules, now, urgency, decision.category),
                    policy_version=rules.version, row_version=1, created_at=now,
                )
                # Los índices únicos protegen la solicitud y (ubicación, categoría, discriminador): QS-01.
                uow.work_orders.add(wo)
                uow.faults.checkpoint("after_work_order")
                record_audit(uow, ctx, now, entity_type="WorkOrder", entity_id=wo.work_order_id,
                             action="WORK_ORDER_CREATED", actor_role=role, before=None,
                             after=wo.snapshot() | {"policyVersion": rules.version, "triageId": triage_id})
                if decision.assignee_id:
                    # §8.2: el triaje puede asignar en la misma transacción (CREATED → ASSIGNED).
                    t, _ = plan_transition(WORK_ORDER_TRANSITIONS, "assign", wo.status, {Actor(actor)})
                    wo_before, _ = wo.apply(t, None)
                    chosen, _, recommended = apply_assignment(uow, ctx, now, wo, rules, decision.assignee_id,
                                                              decision.reason, self.assignment_policy)
                    record_audit(uow, ctx, now, entity_type="WorkOrder", entity_id=wo.work_order_id,
                                 action="WORK_ORDER_ASSIGNMENT", actor_role=role, before=wo_before,
                                 after=wo.snapshot() | {"recommendedAssigneeId": recommended})
                    uow.work_orders.save(wo)
                body["workOrder"] = work_order_dto(wo)
                etag, location = wo.etag(), f"/api/v1/work-orders/{wo.work_order_id}"

            record_audit(uow, ctx, now, entity_type="MaintenanceRequest", entity_id=req.request_id,
                         action=f"TRIAGE_{decision.action.value}", actor_role=role, before=before,
                         after=after | {"triageId": triage_id, "linkedWorkOrderId": req.linked_work_order_id},
                         reason=decision.reason)
            uow.faults.checkpoint("after_audit")

            emit_event(uow, ctx, now, event_type="RequestTriaged", aggregate_type="MaintenanceRequest",
                       aggregate_id=req.request_id, aggregate_version=req.row_version, request_id=req.request_id,
                       payload={"status": req.status, "action": decision.action.value},
                       recipients=[req.requester_id])
            if approve and body["workOrder"]["assigneeId"]:
                emit_event(uow, ctx, now, event_type="WorkOrderAssigned", aggregate_type="WorkOrder",
                           aggregate_id=body["workOrder"]["workOrderId"],
                           aggregate_version=body["workOrder"]["rowVersion"], request_id=req.request_id,
                           payload={"status": body["workOrder"]["status"],
                                    "assigneeId": body["workOrder"]["assigneeId"]},
                           recipients=[body["workOrder"]["assigneeId"], req.requester_id])
            if approve and safety:
                # QS-08 / E2-AS-03: urgencia máxima y alerta confirmadas en la misma transacción.
                emit_event(uow, ctx, now, event_type="SafetyFlagRaised", aggregate_type="WorkOrder",
                           aggregate_id=body["workOrder"]["workOrderId"],
                           aggregate_version=body["workOrder"]["rowVersion"], request_id=req.request_id,
                           payload={"urgency": urgency.value}, recipients=[SUPERVISORS, COORDINATORS])
            uow.faults.checkpoint("after_outbox")
            return CommandResult(body, created=approve, etag=etag, location=location)

        payload = {"id": request_id, **{k: data.get(k) for k in (
            "action", "category", "validatedUrgency", "safetyFlag", "discriminator", "assigneeId",
            "reason", "comments", "existingWorkOrderId")}}
        return self._runner.run(ctx, "triage.decide", payload, handler)
