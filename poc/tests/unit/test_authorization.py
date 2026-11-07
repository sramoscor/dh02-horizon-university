"""AuthorizationService: rol + alcance + propiedad + separación de funciones (E2-ADR-006; BR-10)."""

from datetime import UTC, datetime, timedelta

from backend.application.authorization import AuthorizationService, Principal
from backend.application.ports import Location, RoleGrant
from backend.domain.enums import Category, RequestStatus, Urgency, WorkOrderStatus
from backend.domain.model import MaintenanceRequest, WorkOrder
from backend.domain.transitions import Actor

NOW = datetime(2026, 11, 7, tzinfo=UTC)
CIE = Location("CIE-L-204", "CIE", "NORTE", "Lab", True, True, "rep-CIE-01", "DIR-v118", 118, NOW)
ING = Location("ING-B-110", "ING", "SUR", "Taller", True, False, None, "DIR-v118", 118, NOW)
authz = AuthorizationService()


def p(subject, *grants):
    return Principal(subject, subject, tuple(RoleGrant(*g) for g in grants))


def req(owner="usr-017", loc=CIE):
    return MaintenanceRequest("SR-1", owner, loc.location_id, "x", Urgency.MEDIUM, None, False,
                              RequestStatus.UNDER_TRIAGE, 3, NOW)


def wo(loc=CIE):
    return WorkOrder("WO-1", "SR-1", "TRG-1", loc.location_id, Category.PLUMBING, "", Urgency.HIGH, False,
                     WorkOrderStatus.ASSIGNED, NOW + timedelta(hours=24), "POL-001", 2, NOW)


COORD_NORTE = ("COORDINATOR", "CAMPUS", "NORTE")


def test_coordinator_in_scope_can_triage():
    assert Actor.COORDINATOR in authz.request_relations(p("coord-03", COORD_NORTE), req(), CIE)


def test_coordinator_out_of_scope_cannot_triage():
    assert authz.request_relations(p("coord-03", COORD_NORTE), req(loc=ING), ING) == set()


def test_separation_of_duties_on_own_request():
    coord = p("coord-03", COORD_NORTE, ("REQUESTER", "OWN", None))
    assert authz.request_relations(coord, req(owner="coord-03"), CIE) == {Actor.REQUEST_OWNER}


def test_no_grants_no_relations():
    # Rol inactivo ⇒ sin concesiones activas ⇒ sin relaciones (T-SEC-02).
    assert authz.request_relations(p("coord-99"), req(), CIE) == set()


def test_technician_relation_requires_active_assignment():
    tech = p("TECH-021", ("TECHNICIAN", "GLOBAL", None))
    assert authz.work_order_relations(tech, wo(), CIE, "usr-017", "TECH-014") == set()
    assert authz.work_order_relations(tech, wo(), CIE, "usr-017", "TECH-021") == {Actor.ASSIGNED_TECHNICIAN}


def test_building_rep_scope():
    rep = p("rep-CIE-01", ("BUILDING_REP", "BUILDING", "CIE"))
    assert authz.work_order_relations(rep, wo(), CIE, "usr-017", "TECH-014") == {Actor.BUILDING_REP}
    assert authz.work_order_relations(rep, wo(ING), ING, "usr-017", "TECH-014") == set()


def test_assigned_technician_cannot_confirm_own_work_even_as_requester():
    # Hallazgo H-03: separación de funciones en la confirmación.
    tech = p("TECH-014", ("TECHNICIAN", "GLOBAL", None), ("REQUESTER", "OWN", None))
    rel = authz.work_order_relations(tech, wo(), CIE, "TECH-014", "TECH-014")
    assert Actor.REQUEST_OWNER not in rel and Actor.ASSIGNED_TECHNICIAN in rel


def test_role_from_client_is_never_considered():
    # Un sujeto sin concesiones no gana relaciones aunque su nombre "parezca" un rol.
    assert authz.work_order_relations(p("SUPERVISOR"), wo(), CIE, "usr-017", None) == set()
