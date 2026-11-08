"""Tabla de transiciones explícita (E2-ADR-007; §6.5, §6.6): solo cambios legales, actor antes que estado."""

import itertools
from datetime import UTC, datetime

import pytest

from backend.domain.enums import RequestStatus, Urgency, WorkOrderStatus
from backend.domain.errors import ActionForbidden, InvalidStateTransition, PreconditionFailed, ValidationFailed
from backend.domain.model import MaintenanceRequest, parse_etag
from backend.domain.transitions import (
    REQUEST_TRANSITIONS,
    WORK_ORDER_TRANSITIONS,
    Actor,
    plan_transition,
)

ALL_ACTORS = set(Actor)

# Aristas esperadas según las Figuras F09 y F10 (fuente: documento de Evaluación 2).
EXPECTED_REQUEST_EDGES = {
    ("DRAFT", "SUBMITTED"), ("NEEDS_INFO", "SUBMITTED"), ("DRAFT", "WITHDRAWN"), ("SUBMITTED", "WITHDRAWN"),
    ("SUBMITTED", "UNDER_TRIAGE"), ("UNDER_TRIAGE", "APPROVED"), ("UNDER_TRIAGE", "REJECTED"),
    ("UNDER_TRIAGE", "REDIRECTED"), ("UNDER_TRIAGE", "DUPLICATE_LINKED"), ("UNDER_TRIAGE", "NEEDS_INFO"),
}


def test_request_table_matches_figure_f09():
    edges = {(s, t.target) for t in REQUEST_TRANSITIONS.values() for s in t.sources}
    assert edges == EXPECTED_REQUEST_EDGES


def test_terminal_request_states_have_no_exit():
    terminal = {"APPROVED", "REJECTED", "REDIRECTED", "DUPLICATE_LINKED", "WITHDRAWN"}
    for t in REQUEST_TRANSITIONS.values():
        assert not (t.sources & terminal), t.command


def test_terminal_work_order_states_only_reopen_from_admin_closed():
    for t in WORK_ORDER_TRANSITIONS.values():
        assert WorkOrderStatus.CLOSED not in t.sources
        assert WorkOrderStatus.CANCELLED not in t.sources
        if WorkOrderStatus.ADMIN_CLOSED in t.sources:
            assert t.command == "reopen"  # E2-AS-02: cierre administrativo reabrible


@pytest.mark.parametrize("command,state", [
    (cmd, state)
    for cmd, t in WORK_ORDER_TRANSITIONS.items()
    for state in WorkOrderStatus if state not in t.sources
])
def test_illegal_work_order_transitions_are_rejected(command, state):
    with pytest.raises(InvalidStateTransition):
        plan_transition(WORK_ORDER_TRANSITIONS, command, state, ALL_ACTORS, reason="motivo")


def test_actor_is_checked_before_state():
    # Un técnico no asignado no debe enterarse de si la transición sería legal (QS-04).
    with pytest.raises(ActionForbidden):
        plan_transition(WORK_ORDER_TRANSITIONS, "start", "CLOSED", {Actor.REQUEST_OWNER})


@pytest.mark.parametrize("command", [c for c, t in REQUEST_TRANSITIONS.items() if t.requires_reason])
def test_reason_is_mandatory_for_reject_redirect_request_info(command):
    with pytest.raises(ValidationFailed):
        plan_transition(REQUEST_TRANSITIONS, command, "UNDER_TRIAGE", {Actor.COORDINATOR}, reason="  ")


def test_only_assigned_technician_starts_blocks_completes():
    for command in ("start", "block", "complete"):
        t = WORK_ORDER_TRANSITIONS[command]
        assert t.actors == {Actor.ASSIGNED_TECHNICIAN}


def test_confirmation_actors():
    assert WORK_ORDER_TRANSITIONS["confirm"].actors == {Actor.REQUEST_OWNER, Actor.BUILDING_REP}
    assert WORK_ORDER_TRANSITIONS["admin_close"].actors == {Actor.SUPERVISOR, Actor.SYSTEM}


def test_supervisor_reassign_keeps_state():
    t, actor = plan_transition(WORK_ORDER_TRANSITIONS, "supervisor_reassign", "BLOCKED", {Actor.SUPERVISOR},
                               reason="técnico enfermo")
    assert t.target_for("BLOCKED") == "BLOCKED" and actor == Actor.SUPERVISOR


def _request(version: int = 3) -> MaintenanceRequest:
    return MaintenanceRequest("SR-1", "usr-017", "CIE-L-204", "x", Urgency.MEDIUM, None, False,
                              RequestStatus.UNDER_TRIAGE, version, datetime.now(UTC))


def test_optimistic_version_guard():
    req = _request(3)
    t, _ = plan_transition(REQUEST_TRANSITIONS, "approve", req.status, {Actor.COORDINATOR})
    with pytest.raises(PreconditionFailed):
        req.apply(t, expected_version=2)
    assert req.status == RequestStatus.UNDER_TRIAGE and req.row_version == 3
    before, after = req.apply(t, expected_version=3)
    assert before == {"status": "UNDER_TRIAGE", "rowVersion": 3}
    assert after == {"status": "APPROVED", "rowVersion": 4}


def test_etag_parsing():
    assert parse_etag('"request-SR-2041-v3"', "request", "SR-2041") == 3
    assert parse_etag(None, "request", "SR-2041") is None
    for bad in ('"request-SR-2042-v3"', '"work-order-SR-2041-v3"', '"request-SR-2041-vX"'):
        with pytest.raises(PreconditionFailed):
            parse_etag(bad, "request", "SR-2041")


def test_every_command_has_at_least_one_actor():
    for t in itertools.chain(REQUEST_TRANSITIONS.values(), WORK_ORDER_TRANSITIONS.values()):
        assert t.actors, t.command
