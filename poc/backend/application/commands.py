"""Ejecución de comandos: idempotencia, auditoría y outbox en la misma transacción (§8.4; E2-ADR-004/007)."""

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from backend.application.authorization import Principal
from backend.application.ports import Clock, ConcurrentDuplicateCommand, Receipt, UnitOfWork, UnitOfWorkFactory
from backend.domain.errors import IdempotencyKeyReused, RequiredHeaderMissing


@dataclass(frozen=True)
class CommandContext:
    principal: Principal
    idempotency_key: str
    correlation_id: str
    expected_version: int | None = None


@dataclass
class CommandResult:
    body: dict[str, Any]
    created: bool = False
    etag: str | None = None
    location: str | None = None
    replayed: bool = field(default=False, compare=False)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("replayed")
        return data


def fingerprint(operation: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps({"operation": operation, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class CommandRunner:
    """Envuelve un caso de uso con `Idempotency-Key` y una sola transacción.

    - Misma clave + mismo contenido + receipt → reproduce el resultado (T-API-01).
    - Misma clave + contenido distinto → 409 IDEMPOTENCY_KEY_REUSED (T-API-02).
    - Duplicados concurrentes: un bloqueo por clave los serializa; el perdedor observa el resultado.
    - El receipt se escribe en la misma transacción que la mutación: sin éxito, no hay receipt.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def run(
        self,
        ctx: CommandContext,
        operation: str,
        payload: dict[str, Any],
        handler: Callable[[UnitOfWork], CommandResult],
    ) -> CommandResult:
        if not ctx.idempotency_key:
            raise RequiredHeaderMissing("Falta la cabecera Idempotency-Key.", header="Idempotency-Key")
        fp = fingerprint(operation, payload)
        for _ in range(3):
            with self._uow_factory() as uow:
                uow.receipts.lock(ctx.idempotency_key)
                existing = uow.receipts.get(ctx.idempotency_key)
                if existing is not None:
                    return self._replay(existing, ctx, operation, fp)
                result = handler(uow)
                try:
                    uow.receipts.add(
                        ctx.idempotency_key,
                        Receipt(ctx.principal.subject_id, operation, fp, result.to_json()),
                        self._clock.now(),
                    )
                except ConcurrentDuplicateCommand:
                    continue
                uow.faults.checkpoint("after_receipt")
                uow.faults.checkpoint("before_commit")
                uow.commit()
                return result
        raise IdempotencyKeyReused("No fue posible serializar el comando con esa clave.")

    @staticmethod
    def _replay(existing: Receipt, ctx: CommandContext, operation: str, fp: str) -> CommandResult:
        # §8.4: se reautoriza (mismo actor) y se reproduce antes de evaluar la precondición ya vieja.
        same_actor = existing.actor_id == ctx.principal.subject_id
        if not same_actor or existing.operation != operation or existing.fingerprint != fp:
            raise IdempotencyKeyReused()
        data = dict(existing.response)
        return CommandResult(**data, replayed=True)


def record_audit(
    uow: UnitOfWork,
    ctx: CommandContext,
    now: datetime,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    actor_role: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str | None = None,
) -> None:
    """BR-08 / E2-FR-09: toda mutación aceptada genera un AuditEvent en la misma transacción."""
    uow.audit.append(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "actor_id": ctx.principal.subject_id,
            "actor_role": str(actor_role),
            "action": action,
            "before_state": before,
            "after_state": after,
            "reason": reason,
            "correlation_id": ctx.correlation_id,
            "occurred_at": now,
        }
    )


def emit_event(
    uow: UnitOfWork,
    ctx: CommandContext,
    now: datetime,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    request_id: str | None,
    payload: dict[str, Any],
    recipients: list[str],
) -> None:
    """E2-ADR-004: la intención de notificar se persiste con la mutación; una fila por destinatario.

    El evento describe la versión confirmada del agregado (§8.6), no lo que exista cuando el worker lea.
    """
    for recipient in dict.fromkeys(r for r in recipients if r):
        uow.outbox.add(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "schema_version": 1,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "aggregate_version": aggregate_version,
                "request_id": request_id,
                "payload": payload,
                "recipient_ref": recipient,
                "channel": "EMAIL",
                "correlation_id": ctx.correlation_id,
                "created_at": now,
            }
        )
