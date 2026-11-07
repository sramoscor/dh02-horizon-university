"""AuthorizationService — identidad no es autorización (E2-ADR-006; QS-04; §9.1).

Evalúa rol + alcance (edificio/campus) + propiedad + estado + separación de funciones en **cada**
operación. Se niega por defecto. Ningún rol enviado por el cliente se acepta: las concesiones salen de
ROLE_ASSIGNMENT activas, resueltas en el servidor al validar la sesión.
"""

from dataclasses import dataclass

from backend.application.ports import Location, RoleGrant
from backend.domain.enums import Role, ScopeType
from backend.domain.errors import ActionForbidden
from backend.domain.model import MaintenanceRequest, WorkOrder
from backend.domain.transitions import Actor


@dataclass(frozen=True)
class Principal:
    subject_id: str
    display_name: str
    grants: tuple[RoleGrant, ...]

    def has_role(self, role: Role) -> bool:
        return any(g.role == role for g in self.grants)

    def covers(self, role: Role, location: Location) -> bool:
        """¿Tiene `role` con un alcance que incluye la ubicación?"""
        for g in self.grants:
            if g.role != role:
                continue
            if g.scope_type == ScopeType.GLOBAL:
                return True
            if g.scope_type == ScopeType.CAMPUS and g.scope_id == location.campus:
                return True
            if g.scope_type == ScopeType.BUILDING and g.scope_id == location.building_id:
                return True
        return False


SYSTEM_PRINCIPAL = Principal("system:worker", "C-WORKER", ())

ROLE_FOR_ACTOR = {
    Actor.REQUEST_OWNER: Role.REQUESTER,
    Actor.COORDINATOR: Role.COORDINATOR,
    Actor.SUPERVISOR: Role.SUPERVISOR,
    Actor.ASSIGNED_TECHNICIAN: Role.TECHNICIAN,
    Actor.BUILDING_REP: Role.BUILDING_REP,
    Actor.SYSTEM: "SYSTEM",
}


class AuthorizationService:
    def request_relations(self, p: Principal, req: MaintenanceRequest, loc: Location) -> set[Actor]:
        rel: set[Actor] = set()
        owner = req.requester_id == p.subject_id
        if owner and p.has_role(Role.REQUESTER):
            rel.add(Actor.REQUEST_OWNER)
        # BR-10 / E2-AS-07: separación de funciones; nadie triagea su propia solicitud.
        if not owner:
            if p.covers(Role.COORDINATOR, loc):
                rel.add(Actor.COORDINATOR)
            if p.covers(Role.SUPERVISOR, loc):
                rel.add(Actor.SUPERVISOR)
        return rel

    def work_order_relations(
        self,
        p: Principal,
        wo: WorkOrder,
        loc: Location,
        requester_id: str,
        assignee_id: str | None,
    ) -> set[Actor]:
        if p is SYSTEM_PRINCIPAL:
            return {Actor.SYSTEM}
        rel: set[Actor] = set()
        is_assignee = assignee_id is not None and assignee_id == p.subject_id
        if is_assignee and p.has_role(Role.TECHNICIAN):
            rel.add(Actor.ASSIGNED_TECHNICIAN)
        if p.covers(Role.COORDINATOR, loc):
            rel.add(Actor.COORDINATOR)
        if p.covers(Role.SUPERVISOR, loc):
            rel.add(Actor.SUPERVISOR)
        # Separación de funciones en la confirmación (hallazgo H-03, divergences.md): quien ejecutó el
        # trabajo no puede confirmar su resultado, aunque también sea solicitante o representante.
        if not is_assignee:
            if requester_id == p.subject_id and p.has_role(Role.REQUESTER):
                rel.add(Actor.REQUEST_OWNER)
            if p.covers(Role.BUILDING_REP, loc) and p.has_role(Role.BUILDING_REP):
                rel.add(Actor.BUILDING_REP)
        return rel

    # ---------------------------------------------------------------- lecturas (§9.1 "Ver orden")

    def can_read_request(self, p: Principal, req: MaintenanceRequest, loc: Location) -> bool:
        if req.requester_id == p.subject_id:
            return True
        return p.covers(Role.COORDINATOR, loc) or p.covers(Role.SUPERVISOR, loc)

    def ensure_can_read_request(self, p: Principal, req: MaintenanceRequest, loc: Location) -> None:
        if not self.can_read_request(p, req, loc):
            raise ActionForbidden()

    def ensure_can_read_work_order(
        self, p: Principal, wo: WorkOrder, loc: Location, requester_id: str, assignee_id: str | None
    ) -> set[Actor]:
        rel = self.work_order_relations(p, wo, loc, requester_id, assignee_id)
        if not rel:
            raise ActionForbidden()
        return rel

    def ensure_can_read_history(
        self, p: Principal, wo: WorkOrder, loc: Location, requester_id: str, assignee_id: str | None
    ) -> None:
        """Historial: coordinador/supervisor con alcance, técnico asignado y director (QS-03)."""
        rel = self.work_order_relations(p, wo, loc, requester_id, assignee_id)
        allowed = {Actor.COORDINATOR, Actor.SUPERVISOR, Actor.ASSIGNED_TECHNICIAN}
        if not (rel & allowed or p.has_role(Role.DIRECTOR)):
            raise ActionForbidden()

    def ensure_role(self, p: Principal, *roles: Role) -> Role:
        for role in roles:
            if p.has_role(role):
                return role
        raise ActionForbidden()
