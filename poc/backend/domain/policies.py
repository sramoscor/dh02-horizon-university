"""Políticas versionadas con Strategy (E2-ADR-005; E2-AS-01, E2-AS-03, E2-AS-04).

Las reglas viven en una configuración validada y versionada (`PolicyVersion.validated_rules`); el
evaluador no cambia entre versiones. No se evalúan reglas como texto arbitrario.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from backend.domain.enums import Category, Urgency
from backend.domain.errors import ValidationFailed


@dataclass(frozen=True)
class PolicyRules:
    version: str
    sla_hours: dict[Urgency, int]
    category_sla_hours: dict[Category, dict[Urgency, int]] = field(default_factory=dict)
    category_specialty: dict[Category, str] = field(default_factory=dict)
    admin_close_business_days: int = 7
    safety_forced_urgency: Urgency = Urgency.CRITICAL

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(rules_to_json(self), sort_keys=True).encode()).hexdigest()


def parse_rules(version: str, raw: dict[str, Any]) -> PolicyRules:
    """Valida una configuración de política. Una política malformada se rechaza (T-POL-01)."""
    try:
        sla = {Urgency(k): int(v) for k, v in raw["slaHours"].items()}
        if set(sla) != set(Urgency):
            raise ValueError("slaHours debe definir todas las urgencias")
        overrides = {
            Category(cat): {Urgency(k): int(v) for k, v in hours.items()}
            for cat, hours in raw.get("categorySlaHours", {}).items()
        }
        specialty = {Category(k): str(v) for k, v in raw.get("categorySpecialty", {}).items()}
        days = int(raw.get("adminCloseBusinessDays", 7))
        forced = Urgency(raw.get("safetyForcedUrgency", Urgency.CRITICAL))
        for hours in [sla, *overrides.values()]:
            if any(h <= 0 for h in hours.values()):
                raise ValueError("los plazos deben ser positivos")
        if days <= 0:
            raise ValueError("adminCloseBusinessDays debe ser positivo")
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationFailed(f"Política {version} malformada: {exc}") from exc
    return PolicyRules(version, sla, overrides, specialty, days, forced)


def rules_to_json(rules: PolicyRules) -> dict[str, Any]:
    return {
        "slaHours": {u.value: h for u, h in rules.sla_hours.items()},
        "categorySlaHours": {
            c.value: {u.value: h for u, h in hours.items()} for c, hours in rules.category_sla_hours.items()
        },
        "categorySpecialty": {c.value: s for c, s in rules.category_specialty.items()},
        "adminCloseBusinessDays": rules.admin_close_business_days,
        "safetyForcedUrgency": rules.safety_forced_urgency.value,
    }


class UrgencyPolicy:
    """BR-04: el solicitante sugiere, el coordinador decide. BR-05 / E2-AS-03: la bandera fuerza el máximo."""

    def decide(self, rules: PolicyRules, validated: Urgency, safety_flag: bool) -> Urgency:
        return rules.safety_forced_urgency if safety_flag else validated


class SlaPolicy:
    """E2-AS-04: plazo por urgencia (y categoría si la versión lo define)."""

    def hours(self, rules: PolicyRules, urgency: Urgency, category: Category) -> int:
        return rules.category_sla_hours.get(category, {}).get(urgency, rules.sla_hours[urgency])

    def due_at(self, rules: PolicyRules, created_at: datetime, urgency: Urgency, category: Category) -> datetime:
        return created_at + timedelta(hours=self.hours(rules, urgency, category))

    @staticmethod
    def shift_for_pause(due_at: datetime, block_started: datetime, block_ended: datetime) -> datetime:
        """Un bloqueo MATERIALS/ACCESS desplaza `due_at` por su duración al cerrarse (E2-AS-04)."""
        return due_at + (block_ended - block_started)


@dataclass(frozen=True)
class TechnicianCandidate:
    subject_id: str
    specialty: str
    available: bool
    active_load: int


class AssignmentPolicy:
    """E2-AS-01: filtrar por especialidad y disponibilidad; ordenar por menor carga activa."""

    def eligible(
        self, rules: PolicyRules, category: Category, candidates: list[TechnicianCandidate]
    ) -> list[TechnicianCandidate]:
        specialty = rules.category_specialty.get(category, category.value)
        return sorted(
            (c for c in candidates if c.available and c.specialty == specialty),
            key=lambda c: (c.active_load, c.subject_id),
        )

    def choose(
        self, rules: PolicyRules, category: Category, candidates: list[TechnicianCandidate]
    ) -> TechnicianCandidate | None:
        ranked = self.eligible(rules, category, candidates)
        return ranked[0] if ranked else None


@dataclass(frozen=True)
class DuplicateCandidate:
    kind: str  # "WORK_ORDER" | "REQUEST"
    id: str
    status: str
    category: str | None
    discriminator: str | None = None


class DuplicateCandidatePolicy:
    """E2-ADR-003 punto 3: detección **asesora**. La garantía final la da la base de datos."""

    def candidates(
        self,
        request_id: str,
        category: Category | None,
        active_orders: list[DuplicateCandidate],
        open_requests: list[DuplicateCandidate],
    ) -> list[DuplicateCandidate]:
        def same_category(c: DuplicateCandidate) -> bool:
            return category is None or c.category in (None, category.value)

        orders = [c for c in active_orders if same_category(c)]
        requests = [c for c in open_requests if c.id != request_id and same_category(c)]
        return orders + requests
