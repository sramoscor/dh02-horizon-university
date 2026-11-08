"""Vocabulario congelado del dominio (§2.1, §6.5–6.7)."""

from enum import StrEnum


class RequestStatus(StrEnum):
    """Dimensión 1: estado de la solicitud (§6.5, Figura F09)."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_TRIAGE = "UNDER_TRIAGE"
    NEEDS_INFO = "NEEDS_INFO"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REDIRECTED = "REDIRECTED"
    DUPLICATE_LINKED = "DUPLICATE_LINKED"
    WITHDRAWN = "WITHDRAWN"


class WorkOrderStatus(StrEnum):
    """Dimensión 2: estado de la orden (§6.6, Figura F10)."""

    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    REOPENED = "REOPENED"
    CLOSED = "CLOSED"
    ADMIN_CLOSED = "ADMIN_CLOSED"
    CANCELLED = "CANCELLED"


# Estados "activos" del índice único parcial (E2-ADR-003). Debe coincidir con la migración 0001.
ACTIVE_WORK_ORDER_STATUSES: frozenset[WorkOrderStatus] = frozenset(
    {
        WorkOrderStatus.CREATED,
        WorkOrderStatus.ASSIGNED,
        WorkOrderStatus.IN_PROGRESS,
        WorkOrderStatus.BLOCKED,
        WorkOrderStatus.PENDING_CONFIRMATION,
        WorkOrderStatus.REOPENED,
    }
)


class DeliveryState(StrEnum):
    """Dimensión 3: estado de la entrega (§6.7, Figura F11)."""

    PENDING = "PENDING"
    LEASED = "LEASED"
    RETRY_WAIT = "RETRY_WAIT"
    DELIVERED = "DELIVERED"
    DEAD_LETTER = "DEAD_LETTER"


class TriageAction(StrEnum):
    APPROVE_CREATE_WO = "APPROVE_CREATE_WO"
    REJECT = "REJECT"
    REDIRECT = "REDIRECT"
    LINK_TO_EXISTING = "LINK_TO_EXISTING"
    REQUEST_INFO = "REQUEST_INFO"


class Urgency(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return list(Urgency).index(self)


class Category(StrEnum):
    PLUMBING = "PLUMBING"
    ELECTRICAL = "ELECTRICAL"
    HVAC = "HVAC"
    GENERAL = "GENERAL"


class BlockType(StrEnum):
    MATERIALS = "MATERIALS"
    ACCESS = "ACCESS"
    CONTRACTOR = "CONTRACTOR"
    APPROVAL = "APPROVAL"


# E2-AS-04: solo MATERIALS y ACCESS pausan el SLA.
SLA_PAUSING_BLOCKS: frozenset[BlockType] = frozenset({BlockType.MATERIALS, BlockType.ACCESS})


class Role(StrEnum):
    REQUESTER = "REQUESTER"
    COORDINATOR = "COORDINATOR"
    TECHNICIAN = "TECHNICIAN"
    SUPERVISOR = "SUPERVISOR"
    BUILDING_REP = "BUILDING_REP"
    DIRECTOR = "DIRECTOR"
    OPERATIONS = "OPERATIONS"


class ScopeType(StrEnum):
    GLOBAL = "GLOBAL"
    CAMPUS = "CAMPUS"
    BUILDING = "BUILDING"
    OWN = "OWN"


class ConfirmationOutcome(StrEnum):
    CONFIRMED = "CONFIRMED"
    REJECTED_REOPEN = "REJECTED_REOPEN"
    ADMIN_CLOSED = "ADMIN_CLOSED"
