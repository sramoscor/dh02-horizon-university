"""Políticas versionadas (E2-ADR-005; E2-AS-01/03/04) y calendario de días hábiles (E2-AS-02)."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.domain.calendar import add_business_days
from backend.domain.enums import Category, Urgency
from backend.domain.errors import ValidationFailed
from backend.domain.policies import (
    AssignmentPolicy,
    DuplicateCandidate,
    DuplicateCandidatePolicy,
    SlaPolicy,
    TechnicianCandidate,
    UrgencyPolicy,
    parse_rules,
)
from backend.infrastructure.seeding import FIXTURES

POLICIES = {p["version"]: p["rules"] for p in json.loads((FIXTURES / "seed.json").read_text())["policies"]}
T0 = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)


@pytest.fixture
def pol1():
    return parse_rules("POL-001", POLICIES["POL-001"])


@pytest.fixture
def pol2():
    return parse_rules("POL-002", POLICIES["POL-002"])


@pytest.mark.evidence("T-POL-01", code_refs=["poc/backend/domain/policies.py:SlaPolicy", "poc/backend/domain/policies.py:parse_rules"],
                      related={"requirements": ["E2-FR-02"], "qualities": ["QS-05"], "decisions": ["E2-ADR-005"]},
                      expected="Resultado esperado por caso de urgencia/plazo; política malformada rechazada")
@pytest.mark.parametrize("urgency,category,hours", [
    (Urgency.CRITICAL, Category.PLUMBING, 4), (Urgency.HIGH, Category.PLUMBING, 24),
    (Urgency.MEDIUM, Category.HVAC, 72), (Urgency.LOW, Category.GENERAL, 168),
    (Urgency.HIGH, Category.ELECTRICAL, 24),
])
def test_sla_table_pol001(pol1, urgency, category, hours, evidence):
    due = SlaPolicy().due_at(pol1, T0, urgency, category)
    assert due == T0 + timedelta(hours=hours)  # E2-AS-04: 4 h / 24 h / 72 h / 7 días
    evidence.observe(f"{urgency}/{category}", f"{hours} h")


@pytest.mark.evidence("T-POL-01")
@pytest.mark.parametrize("raw", [
    {},
    {"slaHours": {"HIGH": 24}},
    {"slaHours": {"CRITICAL": 4, "HIGH": 24, "MEDIUM": 72, "LOW": -1}},
    {"slaHours": {"CRITICAL": 4, "HIGH": 24, "MEDIUM": 72, "LOW": 168}, "categorySlaHours": {"ROBOTS": {}}},
    {"slaHours": {"CRITICAL": 4, "HIGH": 24, "MEDIUM": 72, "LOW": 168}, "adminCloseBusinessDays": 0},
])
def test_malformed_policy_is_rejected(raw, evidence):
    with pytest.raises(ValidationFailed):
        parse_rules("POL-BAD", raw)
    evidence.observe("rechazada", json.dumps(raw)[:80])


def test_pol002_changes_only_electrical(pol1, pol2):
    sla = SlaPolicy()
    assert sla.hours(pol2, Urgency.HIGH, Category.ELECTRICAL) == 8
    assert sla.hours(pol1, Urgency.HIGH, Category.ELECTRICAL) == 24
    assert sla.hours(pol2, Urgency.HIGH, Category.PLUMBING) == sla.hours(pol1, Urgency.HIGH, Category.PLUMBING)
    assert pol1.content_hash() != pol2.content_hash()


def test_safety_flag_forces_max_urgency(pol1):
    assert UrgencyPolicy().decide(pol1, Urgency.LOW, safety_flag=True) == Urgency.CRITICAL
    assert UrgencyPolicy().decide(pol1, Urgency.LOW, safety_flag=False) == Urgency.LOW


def test_sla_pause_shifts_due_at_by_block_duration():
    started, ended = T0, T0 + timedelta(hours=5, minutes=10)
    assert SlaPolicy.shift_for_pause(T0 + timedelta(hours=24), started, ended) == T0 + timedelta(hours=29, minutes=10)


def test_assignment_policy_filters_specialty_availability_and_orders_by_load(pol1):
    candidates = [
        TechnicianCandidate("TECH-099", "PLUMBING", False, 0),  # no disponible
        TechnicianCandidate("TECH-030", "PLUMBING", True, 3),
        TechnicianCandidate("TECH-014", "PLUMBING", True, 1),
        TechnicianCandidate("TECH-021", "ELECTRICAL", True, 0),  # otra especialidad
    ]
    ranked = AssignmentPolicy().eligible(pol1, Category.PLUMBING, candidates)
    assert [c.subject_id for c in ranked] == ["TECH-014", "TECH-030"]
    assert AssignmentPolicy().choose(pol1, Category.ELECTRICAL, candidates).subject_id == "TECH-021"
    assert AssignmentPolicy().choose(pol1, Category.PLUMBING, candidates[:1]) is None


def test_duplicate_candidates_are_advisory_and_filtered_by_category():
    orders = [DuplicateCandidate("WORK_ORDER", "WO-1", "ASSIGNED", "PLUMBING"),
              DuplicateCandidate("WORK_ORDER", "WO-2", "ASSIGNED", "ELECTRICAL")]
    reqs = [DuplicateCandidate("REQUEST", "SR-1", "SUBMITTED", "PLUMBING"),
            DuplicateCandidate("REQUEST", "SR-2", "SUBMITTED", None)]
    found = DuplicateCandidatePolicy().candidates("SR-1", Category.PLUMBING, orders, reqs)
    assert [c.id for c in found] == ["WO-1", "SR-2"]


def test_business_days_skip_weekends_and_holidays():
    friday = datetime(2026, 11, 6, 10, tzinfo=UTC)
    assert add_business_days(friday, 1) == datetime(2026, 11, 9, 10, tzinfo=UTC)  # lunes
    assert add_business_days(friday, 7) == datetime(2026, 11, 17, 10, tzinfo=UTC)
    holidays = frozenset({date(2026, 11, 9)})
    assert add_business_days(friday, 1, holidays) == datetime(2026, 11, 10, 10, tzinfo=UTC)
