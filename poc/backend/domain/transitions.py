"""Tablas de transiciones explícitas (E2-ADR-007; §6.5, §6.6).

Una sola fuente de verdad sobre qué cambios de estado son legales y qué relación del actor con el
recurso los habilita. La autorización (application/authorization.py) calcula las relaciones del actor
(rol + alcance + propiedad + separación de funciones) y esta tabla decide si alcanzan.
"""

from dataclasses import dataclass
from enum import StrEnum

from backend.domain.enums import ACTIVE_WORK_ORDER_STATUSES, RequestStatus, WorkOrderStatus
from backend.domain.errors import ActionForbidden, InvalidStateTransition, ValidationFailed


class Actor(StrEnum):
    """Relación del actor con el recurso (no un rol enviado por el cliente)."""

    REQUEST_OWNER = "REQUEST_OWNER"
    COORDINATOR = "COORDINATOR"
    SUPERVISOR = "SUPERVISOR"
    ASSIGNED_TECHNICIAN = "ASSIGNED_TECHNICIAN"
    BUILDING_REP = "BUILDING_REP"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True)
class Transition:
    command: str
    sources: frozenset[str]
    target: str | None  # None = conserva el estado (p. ej. reasignación del supervisor en IN_PROGRESS)
    actors: frozenset[Actor]
    requires_reason: bool = False

    def target_for(self, current: str) -> str:
        return self.target if self.target is not None else current


def _t(command: str, sources, target, actors, requires_reason: bool = False) -> Transition:
    return Transition(command, frozenset(sources), target, frozenset(actors), requires_reason)


RS, WS, A = RequestStatus, WorkOrderStatus, Actor
TRIAGERS = {A.COORDINATOR, A.SUPERVISOR}  # §9.1: el supervisor triagea en suplencia

# §6.5 — ciclo de vida de la solicitud
REQUEST_TRANSITIONS: dict[str, Transition] = {
    t.command: t
    for t in [
        _t("submit", {RS.DRAFT, RS.NEEDS_INFO}, RS.SUBMITTED, {A.REQUEST_OWNER}),
        _t("withdraw", {RS.DRAFT, RS.SUBMITTED}, RS.WITHDRAWN, {A.REQUEST_OWNER}),
        _t("start_review", {RS.SUBMITTED}, RS.UNDER_TRIAGE, TRIAGERS),
        _t("approve", {RS.UNDER_TRIAGE}, RS.APPROVED, TRIAGERS),
        _t("reject", {RS.UNDER_TRIAGE}, RS.REJECTED, TRIAGERS, requires_reason=True),  # BR-12
        _t("redirect", {RS.UNDER_TRIAGE}, RS.REDIRECTED, TRIAGERS, requires_reason=True),  # BR-12
        _t("link", {RS.UNDER_TRIAGE}, RS.DUPLICATE_LINKED, TRIAGERS),
        _t("request_info", {RS.UNDER_TRIAGE}, RS.NEEDS_INFO, TRIAGERS, requires_reason=True),
    ]
}

COORD_OR_SUP = {A.COORDINATOR, A.SUPERVISOR}
CONFIRMERS = {A.REQUEST_OWNER, A.BUILDING_REP}

# §6.6 — ciclo de vida de la orden de trabajo (tabla de transiciones)
WORK_ORDER_TRANSITIONS: dict[str, Transition] = {
    t.command: t
    for t in [
        _t("assign", {WS.CREATED, WS.REOPENED}, WS.ASSIGNED, COORD_OR_SUP),
        _t("reassign", {WS.ASSIGNED}, WS.ASSIGNED, COORD_OR_SUP, requires_reason=True),
        # BR-11: el supervisor puede reasignar en cualquier estado activo; el estado se conserva.
        _t(
            "supervisor_reassign",
            {WS.IN_PROGRESS, WS.BLOCKED, WS.PENDING_CONFIRMATION},
            None,
            {A.SUPERVISOR},
            requires_reason=True,
        ),
        _t("start", {WS.ASSIGNED}, WS.IN_PROGRESS, {A.ASSIGNED_TECHNICIAN}),
        _t("block", {WS.IN_PROGRESS}, WS.BLOCKED, {A.ASSIGNED_TECHNICIAN}),  # BR-06
        _t("resolve_block", {WS.BLOCKED}, WS.IN_PROGRESS, {A.ASSIGNED_TECHNICIAN, *COORD_OR_SUP}),
        _t("complete", {WS.IN_PROGRESS}, WS.PENDING_CONFIRMATION, {A.ASSIGNED_TECHNICIAN}),
        _t("confirm", {WS.PENDING_CONFIRMATION}, WS.CLOSED, CONFIRMERS),
        # E2-AS-02: ADMIN_CLOSED se trata como cierre reabrible.
        _t("reopen", {WS.PENDING_CONFIRMATION, WS.ADMIN_CLOSED}, WS.REOPENED, CONFIRMERS, requires_reason=True),
        # E2-AS-02: el worker cierra al vencer el plazo; el supervisor conserva el cierre manual con motivo.
        _t("admin_close", {WS.PENDING_CONFIRMATION}, WS.ADMIN_CLOSED, {A.SUPERVISOR, A.SYSTEM}),
        _t("cancel", ACTIVE_WORK_ORDER_STATUSES, WS.CANCELLED, COORD_OR_SUP, requires_reason=True),
    ]
}

# Registro de trabajo (no cambia estado, pero exige relación y estado válido): E2-FR-05, BR-13.
WORK_RECORDING_STATES = frozenset({WS.ASSIGNED, WS.IN_PROGRESS, WS.BLOCKED})
WORK_RECORDERS = frozenset({A.ASSIGNED_TECHNICIAN, A.COORDINATOR, A.SUPERVISOR})


def plan_transition(
    table: dict[str, Transition],
    command: str,
    current_state: str,
    relations: set[Actor],
    reason: str | None = None,
) -> tuple[Transition, Actor]:
    """Valida actor → estado → motivo y devuelve la transición y la relación que la habilitó.

    El actor se comprueba antes que el estado para no revelar información a quien no está autorizado
    (QS-04).
    """
    transition = table.get(command)
    if transition is None:
        raise InvalidStateTransition(f"Comando desconocido: {command}")
    granted = [a for a in transition.actors if a in relations]
    if not granted:
        raise ActionForbidden()
    if current_state not in transition.sources:
        raise InvalidStateTransition(
            f"No se puede ejecutar '{command}' desde {current_state}.", currentState=current_state
        )
    if transition.requires_reason and not (reason and reason.strip()):
        raise ValidationFailed("El motivo es obligatorio para esta acción.", fields={"reason": "obligatorio"})
    # Prioridad estable para auditar con qué relación actuó (la más específica primero).
    order = list(Actor)
    return transition, sorted(granted, key=order.index)[0]
