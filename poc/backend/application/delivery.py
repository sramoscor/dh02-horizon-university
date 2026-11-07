"""Entrega de notificaciones desde el outbox (E2-ADR-004; QS-02; V-SEQ-03; §6.7).

Entrega *al menos una vez*: la clave de entrega (`event_id`) es estable entre reintentos y el receptor
deduplica. Un worker con lease vencido no puede confirmar trabajo ajeno (el UPDATE exige su token).
"""

from dataclasses import dataclass, field
from datetime import timedelta

from backend.application.ports import (
    Clock,
    Delivery,
    DeliveryStore,
    NotificationPort,
    PermanentDeliveryFailure,
    SpaceDirectoryPort,
    TemporaryDeliveryFailure,
    UnitOfWorkFactory,
)


@dataclass(frozen=True)
class DeliverySettings:
    """Valores del diseño por defecto (QS-02): sondeo ≤ 2 s, esperas 5/10/20/30 s, 12 intentos, lease 15 s."""

    lease_seconds: float = 15.0
    backoff_seconds: tuple[float, ...] = (5.0, 10.0, 20.0, 30.0)
    max_attempts: int = 12
    batch_size: int = 20

    def backoff(self, attempt: int) -> timedelta:
        idx = min(max(attempt, 1) - 1, len(self.backoff_seconds) - 1)
        return timedelta(seconds=self.backoff_seconds[idx])


@dataclass
class DeliveryReport:
    claimed: int = 0
    delivered: int = 0
    retried: int = 0
    dead: int = 0
    stale_leases: int = 0
    errors: list[str] = field(default_factory=list)


class DeliveryService:
    def __init__(self, store: DeliveryStore, notifier: NotificationPort, clock: Clock,
                 settings: DeliverySettings) -> None:
        self._store = store
        self._notifier = notifier
        self._clock = clock
        self.settings = settings

    def run_once(self) -> DeliveryReport:
        report = DeliveryReport()
        rows = self._store.claim(self._clock.now(), self.settings.lease_seconds, self.settings.batch_size)
        report.claimed = len(rows)
        for row in rows:
            message = {
                "eventId": row.event_id,
                "eventType": row.event_type,
                "schemaVersion": 1,
                "aggregateType": row.aggregate_type,
                "aggregateId": row.aggregate_id,
                "aggregateVersion": row.aggregate_version,
                "occurredAt": row.created_at.isoformat(),
                "correlationId": row.correlation_id,
                "payload": row.payload,  # plantilla mínima: sin notas internas ni historial (§8.6)
            }
            delivery = Delivery(row.event_id, row.event_type, row.recipient_ref, row.channel, message)
            try:
                self._notifier.send(delivery)
            except PermanentDeliveryFailure as exc:
                ok = self._store.mark_dead(row.event_id, row.lease_token, f"permanente: {exc}")
                report.dead += ok
                report.stale_leases += not ok
                continue
            except TemporaryDeliveryFailure as exc:
                if row.attempt_count >= self.settings.max_attempts:
                    ok = self._store.mark_dead(row.event_id, row.lease_token, f"reintentos agotados: {exc}")
                    report.dead += ok
                else:
                    when = self._clock.now() + self.settings.backoff(row.attempt_count)
                    ok = self._store.mark_retry(row.event_id, row.lease_token, when, str(exc)[:500])
                    report.retried += ok
                report.stale_leases += not ok
                report.errors.append(str(exc)[:200])
                continue
            ok = self._store.mark_delivered(row.event_id, row.lease_token, self._clock.now())
            report.delivered += ok
            report.stale_leases += not ok
        return report


class DirectoryRefreshService:
    """C-WORKER: actualización periódica de la proyección del directorio (E2-ADR-008, §8.5)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, directory: SpaceDirectoryPort, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._directory = directory
        self._clock = clock

    def refresh(self) -> dict[str, int]:
        spaces = self._directory.fetch_all()  # el adaptador valida el esquema y rechaza datos inválidos
        now = self._clock.now()
        updated = rejected = 0
        with self._uow_factory() as uow:
            for space in spaces:
                if uow.locations.upsert(space, now):
                    updated += 1
                else:
                    rejected += 1  # sourceRevision retrocede: no se degrada la proyección
            uow.commit()
        return {"updated": updated, "rejected": rejected}
