"""Módulo Solicitudes y Ubicaciones (E2-FR-01; §8.1 `POST /requests`, `/submissions`, `/status`)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from backend.application.authorization import ROLE_FOR_ACTOR, AuthorizationService, Principal
from backend.application.commands import CommandContext, CommandResult, CommandRunner, emit_event, record_audit
from backend.application.ports import Clock, Location, UnitOfWork, UnitOfWorkFactory
from backend.domain.enums import Category, RequestStatus, Role, Urgency
from backend.domain.errors import (
    ActionForbidden,
    LocationDirectoryUnavailable,
    ResourceNotFound,
    ValidationFailed,
)
from backend.domain.model import MaintenanceRequest
from backend.domain.transitions import REQUEST_TRANSITIONS, plan_transition

COORDINATORS = "role:COORDINATOR"
SUPERVISORS = "role:SUPERVISOR"


class LocationService:
    """Valida ubicaciones contra la proyección local del directorio (E2-ADR-008, E2-AS-05, QS-09)."""

    def __init__(self, max_age: timedelta) -> None:
        self.max_age = max_age

    def ensure_valid(self, uow: UnitOfWork, location_id: str, now: datetime) -> Location:
        loc = uow.locations.get(location_id)
        if loc is None:
            raise ValidationFailed("Ubicación desconocida en el directorio.", fields={"locationId": "desconocida"})
        if not loc.active:
            raise ValidationFailed("La ubicación no está activa.", fields={"locationId": "inactiva"})
        if now - loc.fetched_at > self.max_age:
            raise LocationDirectoryUnavailable(
                "La proyección del directorio supera la antigüedad máxima aceptada (24 h).",
                fetchedAt=loc.fetched_at.isoformat(),
            )
        return loc

    def describe(self, loc: Location, now: datetime) -> dict[str, Any]:
        age = (now - loc.fetched_at).total_seconds()
        return {
            "locationId": loc.location_id,
            "buildingId": loc.building_id,
            "campus": loc.campus,
            "displayName": loc.display_name,
            "active": loc.active,
            "restrictedAccess": loc.restricted_access,
            "directoryVersion": loc.directory_version,
            "fetchedAt": loc.fetched_at.isoformat(),
            "projectionAgeSeconds": int(age),
            "stale": age > self.max_age.total_seconds(),
        }


def request_dto(req: MaintenanceRequest, loc: Location | None = None) -> dict[str, Any]:
    return {
        "requestId": req.request_id,
        "requesterId": req.requester_id,
        "locationId": req.location_id,
        "locationName": loc.display_name if loc else None,
        "description": req.description,
        "suggestedUrgency": req.suggested_urgency.value,
        "suggestedCategory": req.suggested_category.value if req.suggested_category else None,
        "safetyFlag": req.safety_flag,
        "status": req.status,
        "rowVersion": req.row_version,
        "etag": req.etag(),
        "linkedWorkOrderId": req.linked_work_order_id,
        "additionalInfo": req.additional_info,
        "createdAt": req.created_at.isoformat(),
        "submittedAt": req.submitted_at.isoformat() if req.submitted_at else None,
    }


def _enum(enum_cls, value, field_name: str, required: bool = True):
    if value in (None, ""):
        if required:
            raise ValidationFailed(f"{field_name} es obligatorio.", fields={field_name: "obligatorio"})
        return None
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise ValidationFailed(f"{field_name} inválido.", fields={field_name: "valor no permitido"}) from exc


class RequestService:
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

    # ------------------------------------------------------------------ comandos

    def create_draft(self, ctx: CommandContext, data: dict[str, Any]) -> CommandResult:
        """El propietario se deriva de la sesión, nunca del cuerpo (T-SEC-03)."""
        self._authz.ensure_role(ctx.principal, Role.REQUESTER)
        description = (data.get("description") or "").strip()
        if not description or len(description) > 2000:
            raise ValidationFailed("La descripción es obligatoria (máx. 2000).", fields={"description": "inválida"})
        urgency = _enum(Urgency, data.get("suggestedUrgency"), "suggestedUrgency")
        category = _enum(Category, data.get("suggestedCategory"), "suggestedCategory", required=False)
        location_id = data.get("locationId") or ""

        def handler(uow: UnitOfWork) -> CommandResult:
            now = self._clock.now()
            loc = uow.locations.get(location_id)
            if loc is None:
                raise ValidationFailed("Ubicación desconocida.", fields={"locationId": "desconocida"})
            req = MaintenanceRequest(
                request_id=uow.requests.next_id(),
                requester_id=ctx.principal.subject_id,
                location_id=location_id,
                description=description,
                suggested_urgency=urgency,
                suggested_category=category,
                safety_flag=bool(data.get("safetyFlag", False)),
                status=RequestStatus.DRAFT,
                row_version=1,
                created_at=now,
            )
            uow.requests.add(req)
            record_audit(uow, ctx, now, entity_type="MaintenanceRequest", entity_id=req.request_id,
                         action="REQUEST_DRAFTED", actor_role=Role.REQUESTER, before=None, after=req.snapshot())
            return CommandResult(request_dto(req, loc), created=True, etag=req.etag(),
                                 location=f"/api/v1/requests/{req.request_id}")

        payload = {k: data.get(k) for k in ("locationId", "description", "suggestedUrgency",
                                             "suggestedCategory", "safetyFlag")}
        return self._runner.run(ctx, "request.create", payload, handler)

    def submit(self, ctx: CommandContext, request_id: str, additional_info: str | None = None) -> CommandResult:
        """Congela el contenido, valida la ubicación y es idempotente (§8.1)."""

        def handler(uow: UnitOfWork) -> CommandResult:
            now = self._clock.now()
            req = uow.requests.get(request_id, for_update=True)
            if req is None:
                raise ResourceNotFound()
            loc = uow.locations.get(req.location_id)
            rel = self._authz.request_relations(ctx.principal, req, loc)
            transition, actor = plan_transition(REQUEST_TRANSITIONS, "submit", req.status, rel)
            self._locations.ensure_valid(uow, req.location_id, now)
            if req.status == RequestStatus.NEEDS_INFO:
                if not (additional_info and additional_info.strip()):
                    raise ValidationFailed("Agregue la información solicitada.",
                                           fields={"additionalInfo": "obligatorio"})
                req.additional_info = additional_info.strip()[:2000]
            before, after = req.apply(transition, ctx.expected_version)
            req.submitted_at = now
            uow.requests.save(req, now)
            record_audit(uow, ctx, now, entity_type="MaintenanceRequest", entity_id=req.request_id,
                         action="REQUEST_SUBMITTED", actor_role=ROLE_FOR_ACTOR[actor], before=before, after=after)
            emit_event(uow, ctx, now, event_type="RequestSubmitted", aggregate_type="MaintenanceRequest",
                       aggregate_id=req.request_id, aggregate_version=req.row_version, request_id=req.request_id,
                       payload={"status": req.status}, recipients=[req.requester_id, COORDINATORS])
            return CommandResult(request_dto(req, loc), etag=req.etag())

        return self._runner.run(ctx, "request.submit", {"id": request_id, "info": additional_info}, handler)

    def withdraw(self, ctx: CommandContext, request_id: str) -> CommandResult:
        def handler(uow: UnitOfWork) -> CommandResult:
            now = self._clock.now()
            req = uow.requests.get(request_id, for_update=True)
            if req is None:
                raise ResourceNotFound()
            loc = uow.locations.get(req.location_id)
            rel = self._authz.request_relations(ctx.principal, req, loc)
            transition, actor = plan_transition(REQUEST_TRANSITIONS, "withdraw", req.status, rel)
            before, after = req.apply(transition, ctx.expected_version)
            uow.requests.save(req, now)
            record_audit(uow, ctx, now, entity_type="MaintenanceRequest", entity_id=req.request_id,
                         action="REQUEST_WITHDRAWN", actor_role=ROLE_FOR_ACTOR[actor], before=before, after=after)
            return CommandResult(request_dto(req, loc), etag=req.etag())

        return self._runner.run(ctx, "request.withdraw", {"id": request_id}, handler)

    # ------------------------------------------------------------------ lecturas

    def get(self, principal: Principal, request_id: str) -> dict[str, Any]:
        with self._uow_factory() as uow:
            req = uow.requests.get(request_id)
            if req is None:
                raise ResourceNotFound()
            loc = uow.locations.get(req.location_id)
            self._authz.ensure_can_read_request(principal, req, loc)
            return request_dto(req, loc)

    def list(self, principal: Principal, view: str = "mine") -> list[dict[str, Any]]:
        """`mine`: solicitudes propias. `triage`: cola SUBMITTED/UNDER_TRIAGE/NEEDS_INFO en el alcance."""
        with self._uow_factory() as uow:
            if view == "triage":
                if not (principal.has_role(Role.COORDINATOR) or principal.has_role(Role.SUPERVISOR)):
                    raise ActionForbidden()
                locs = [
                    loc for loc in uow.locations.all()
                    if principal.covers(Role.COORDINATOR, loc) or principal.covers(Role.SUPERVISOR, loc)
                ]
                by_id = {loc.location_id: loc for loc in locs}
                reqs = uow.requests.list(
                    statuses=[RequestStatus.SUBMITTED, RequestStatus.UNDER_TRIAGE, RequestStatus.NEEDS_INFO],
                    location_ids=list(by_id),
                )
                return [request_dto(r, by_id[r.location_id]) | {"ownRequest": r.requester_id == principal.subject_id}
                        for r in reqs]
            reqs = uow.requests.list(requester_id=principal.subject_id)
            locs = {loc.location_id: loc for loc in uow.locations.all()}
            return [request_dto(r, locs.get(r.location_id)) for r in reqs]

    def combined_status(self, principal: Principal, request_id: str) -> dict[str, Any]:
        """§6.8: tres dimensiones independientes (solicitud · orden · notificación)."""
        with self._uow_factory() as uow:
            req = uow.requests.get(request_id)
            if req is None:
                raise ResourceNotFound()
            loc = uow.locations.get(req.location_id)
            self._authz.ensure_can_read_request(principal, req, loc)
            wo = uow.work_orders.by_request(request_id)
            if wo is None and req.linked_work_order_id:
                wo = uow.work_orders.get(req.linked_work_order_id)
            deliveries = uow.outbox.for_request(request_id)
            mine = [d for d in deliveries if d["recipient_ref"] == req.requester_id]
            latest = mine[-1] if mine else None
            return {
                "requestId": req.request_id,
                "request": {"status": req.status, "rowVersion": req.row_version},
                "workOrder": (
                    {"workOrderId": wo.work_order_id, "status": wo.status, "dueAt": wo.due_at.isoformat(),
                     "linked": wo.request_id != request_id}
                    if wo else None
                ),
                "notification": (
                    {"eventType": latest["event_type"], "state": latest["delivery_state"],
                     "attempts": latest["attempt_count"], "updatedAt": latest["created_at"]}
                    if latest else None
                ),
            }

    def locations(self) -> list[dict[str, Any]]:
        with self._uow_factory() as uow:
            now = self._clock.now()
            return [self._locations.describe(loc, now) for loc in uow.locations.all()]
