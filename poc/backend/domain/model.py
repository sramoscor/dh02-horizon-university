"""Entidades del dominio (§6.1). Solicitud y orden son entidades distintas (E2-ADR-001)."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from backend.domain.enums import Category, Urgency
from backend.domain.errors import PreconditionFailed
from backend.domain.transitions import Transition


@dataclass
class MaintenanceRequest:
    request_id: str
    requester_id: str
    location_id: str
    description: str
    suggested_urgency: Urgency
    suggested_category: Category | None
    safety_flag: bool
    status: str
    row_version: int
    created_at: datetime
    submitted_at: datetime | None = None
    linked_work_order_id: str | None = None
    additional_info: str | None = None

    def etag(self) -> str:
        return f'"request-{self.request_id}-v{self.row_version}"'

    def snapshot(self) -> dict[str, Any]:
        return {"status": self.status, "rowVersion": self.row_version}

    def apply(self, transition: Transition, expected_version: int | None) -> tuple[dict, dict]:
        """Aplica una transición ya autorizada con control optimista de versión (E2-ADR-007)."""
        check_version(self.row_version, expected_version)
        before = self.snapshot()
        self.status = transition.target_for(self.status)
        self.row_version += 1
        return before, self.snapshot()


@dataclass
class WorkOrder:
    work_order_id: str
    request_id: str
    triage_id: str
    location_id: str
    category: Category
    discriminator: str
    urgency: Urgency
    safety_flag: bool
    status: str
    due_at: datetime
    policy_version: str
    row_version: int
    created_at: datetime
    closed_at: datetime | None = None
    assignee_id: str | None = field(default=None, compare=False)

    def etag(self) -> str:
        return f'"work-order-{self.work_order_id}-v{self.row_version}"'

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rowVersion": self.row_version,
            "assigneeId": self.assignee_id,
            "dueAt": self.due_at.isoformat(),
            "urgency": self.urgency.value,
        }

    def apply(self, transition: Transition, expected_version: int | None) -> tuple[dict, dict]:
        check_version(self.row_version, expected_version)
        before = self.snapshot()
        self.status = transition.target_for(self.status)
        self.row_version += 1
        return before, self.snapshot()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_version(current: int, expected: int | None) -> None:
    """`If-Match` obsoleto → 412 sin efectos (T-API-03)."""
    if expected is not None and expected != current:
        raise PreconditionFailed(currentVersion=current, expectedVersion=expected)


def parse_etag(value: str | None, prefix: str, entity_id: str) -> int | None:
    """Convierte `"request-SR-2041-v3"` en 3. Devuelve None si no hay cabecera."""
    if value is None:
        return None
    raw = value.strip().removeprefix("W/").strip('"')
    expected_prefix = f"{prefix}-{entity_id}-v"
    if not raw.startswith(expected_prefix) or not raw[len(expected_prefix) :].isdigit():
        raise PreconditionFailed("If-Match no corresponde a este recurso.")
    return int(raw[len(expected_prefix) :])
