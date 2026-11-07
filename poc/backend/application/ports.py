"""Puertos de la aplicación (§5.3, §5.4; E2-ADR-008 puertos y adaptadores).

`application/` define los contratos; `infrastructure/` los implementa (PostgreSQL, X-ID, X-DIR, X-NOTIFY).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from backend.domain.model import MaintenanceRequest, WorkOrder
from backend.domain.policies import DuplicateCandidate, PolicyRules, TechnicianCandidate

# --------------------------------------------------------------------------- reloj e inyección de fallos


class Clock(Protocol):
    def now(self) -> datetime: ...


class FaultInjector(Protocol):
    """Puntos de control para T-TXN-01. En producción es un no-op."""

    def checkpoint(self, name: str) -> None: ...


class NoFaults:
    def checkpoint(self, name: str) -> None:
        return None


# --------------------------------------------------------------------------- sistemas externos


@dataclass(frozen=True)
class VerifiedIdentity:
    subject_id: str
    display_name: str


class IdentityPort(Protocol):
    def verify(self, assertion: str) -> VerifiedIdentity: ...


@dataclass(frozen=True)
class Space:
    location_id: str
    building_id: str
    campus: str
    display_name: str
    active: bool
    restricted_access: bool
    building_representative_id: str | None
    directory_version: str
    source_revision: int


class SpaceDirectoryPort(Protocol):
    def fetch_all(self) -> list[Space]: ...


class TemporaryDeliveryFailure(Exception):
    pass


class PermanentDeliveryFailure(Exception):
    pass


@dataclass(frozen=True)
class Delivery:
    delivery_key: str
    event_type: str
    recipient_ref: str
    channel: str
    message: dict[str, Any]


class NotificationPort(Protocol):
    def send(self, delivery: Delivery) -> None: ...


# --------------------------------------------------------------------------- persistencia


@dataclass(frozen=True)
class Location:
    location_id: str
    building_id: str
    campus: str
    display_name: str
    active: bool
    restricted_access: bool
    building_representative_id: str | None
    directory_version: str
    source_revision: int
    fetched_at: datetime


@dataclass(frozen=True)
class RoleGrant:
    role: str
    scope_type: str
    scope_id: str | None


@dataclass(frozen=True)
class SessionRecord:
    subject_id: str
    display_name: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class Receipt:
    actor_id: str
    operation: str
    fingerprint: str
    response: dict[str, Any]


@dataclass(frozen=True)
class OutboxRow:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    payload: dict[str, Any]
    recipient_ref: str
    channel: str
    attempt_count: int
    lease_token: str
    correlation_id: str | None
    created_at: datetime


class RequestRepository(Protocol):
    def next_id(self) -> str: ...
    def add(self, request: MaintenanceRequest) -> None: ...
    def get(self, request_id: str, *, for_update: bool = False) -> MaintenanceRequest | None: ...
    def save(self, request: MaintenanceRequest, now: datetime) -> None: ...
    def list(self, *, requester_id: str | None = None, statuses: list[str] | None = None,
             location_ids: list[str] | None = None, limit: int = 100) -> list[MaintenanceRequest]: ...
    def open_at_location(self, location_id: str) -> list[DuplicateCandidate]: ...


class WorkOrderRepository(Protocol):
    def next_id(self) -> str: ...
    def add(self, order: WorkOrder) -> None:
        """Inserta; traduce violaciones de unicidad a DuplicateCandidateExists (E2-ADR-003 punto 4)."""
        ...
    def get(self, work_order_id: str, *, for_update: bool = False) -> WorkOrder | None: ...
    def by_request(self, request_id: str) -> WorkOrder | None: ...
    def save(self, order: WorkOrder) -> None: ...
    def active_at_location(self, location_id: str) -> list[DuplicateCandidate]: ...
    def search(self, filters: dict[str, Any], limit: int = 100) -> list[WorkOrder]: ...
    def pending_confirmation_before(self, cutoff: datetime) -> list[str]: ...
    def overdue_candidates(self, now: datetime) -> list[WorkOrder]: ...


class TriageRepository(Protocol):
    def next_id(self) -> str: ...
    def add(self, record: dict[str, Any]) -> None: ...
    def latest_for(self, request_id: str) -> dict[str, Any] | None: ...


class WorkLogRepository(Protocol):
    """Asignaciones, bloqueos, materiales, observaciones y confirmaciones de una orden."""

    def active_assignee(self, work_order_id: str) -> str | None: ...
    def end_active_assignments(self, work_order_id: str, now: datetime) -> None: ...
    def add_assignment(self, work_order_id: str, assignee_id: str, by: str, reason: str | None,
                       now: datetime) -> None: ...
    def open_block(self, work_order_id: str) -> dict[str, Any] | None: ...
    def add_block(self, work_order_id: str, block_type: str, detail: str, by: str, now: datetime) -> str: ...
    def close_block(self, block_id: str, by: str, now: datetime) -> None: ...
    def add_material(self, work_order_id: str, name: str, quantity: int, by: str, now: datetime) -> str: ...
    def add_observation(self, work_order_id: str, author: str, note: str, attachment_ref: str | None,
                        now: datetime) -> str: ...
    def observation_count(self, work_order_id: str) -> int: ...
    def add_confirmation(self, work_order_id: str, by: str, outcome: str, reason: str | None,
                         now: datetime) -> None: ...
    def details(self, work_order_id: str) -> dict[str, Any]: ...
    def technician_candidates(self) -> list[TechnicianCandidate]: ...
    def workload(self) -> list[dict[str, Any]]: ...


class AuditRepository(Protocol):
    def append(self, event: dict[str, Any]) -> None: ...
    def for_entities(self, entity_ids: list[str]) -> list[dict[str, Any]]: ...


class OutboxRepository(Protocol):
    def add(self, event: dict[str, Any]) -> None: ...
    def for_request(self, request_id: str) -> list[dict[str, Any]]: ...
    def list(self, aggregate_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...


class ReceiptRepository(Protocol):
    def lock(self, key: str) -> None:
        """Serializa comandos concurrentes con la misma clave (bloqueo transaccional)."""
        ...
    def get(self, key: str) -> Receipt | None: ...
    def add(self, key: str, receipt: Receipt, now: datetime) -> None:
        """Lanza ConcurrentDuplicateCommand si otra transacción registró la misma clave."""
        ...


class ConcurrentDuplicateCommand(Exception):
    """La unicidad del receipt serializó dos comandos con la misma clave (§8.4)."""


class LocationRepository(Protocol):
    def get(self, location_id: str) -> Location | None: ...
    def all(self) -> list[Location]: ...
    def upsert(self, space: Space, now: datetime) -> bool:
        """Devuelve False si la revisión retrocede (proyección no degradada; §8.5)."""
        ...


class AccessRepository(Protocol):
    def active_grants(self, subject_id: str) -> list[RoleGrant]: ...
    def create_session(self, session_hash: str, record: SessionRecord, now: datetime) -> None: ...
    def get_session(self, session_hash: str) -> SessionRecord | None: ...
    def delete_session(self, session_hash: str) -> None: ...


class PolicyRepository(Protocol):
    def active(self) -> PolicyRules: ...
    def get(self, version: str) -> PolicyRules | None: ...
    def install(self, version: str, now: datetime) -> None: ...
    def list(self) -> list[dict[str, Any]]: ...


class OverdueRepository(Protocol):
    def record(self, work_order_id: str, threshold: str, now: datetime) -> bool:
        """True si se registró ahora; False si ya existía la alerta para ese umbral."""
        ...


class UnitOfWork(Protocol):
    """Una transacción: mutación + AuditEvent + OutboxEvent (+ receipt) se confirman juntos (E2-ADR-007)."""

    requests: RequestRepository
    work_orders: WorkOrderRepository
    triage: TriageRepository
    work_log: WorkLogRepository
    audit: AuditRepository
    outbox: OutboxRepository
    receipts: ReceiptRepository
    locations: LocationRepository
    access: AccessRepository
    policies: PolicyRepository
    overdue: OverdueRepository
    faults: FaultInjector

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, *exc: object) -> None: ...
    def commit(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]


class DeliveryStore(Protocol):
    """Reclamo de entregas con lease (E2-ADR-004; §6.7). Transacciones cortas propias del worker."""

    def claim(self, now: datetime, lease_seconds: float, batch: int) -> list[OutboxRow]: ...
    def mark_delivered(self, event_id: str, lease_token: str, now: datetime) -> bool: ...
    def mark_retry(self, event_id: str, lease_token: str, next_attempt_at: datetime, error: str) -> bool: ...
    def mark_dead(self, event_id: str, lease_token: str, error: str) -> bool: ...
