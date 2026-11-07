"""Operaciones: observación de entregas, políticas versionadas y control de la demo."""

from typing import Any, Protocol

from backend.application.authorization import AuthorizationService, Principal
from backend.application.commands import CommandContext, CommandResult, CommandRunner, record_audit
from backend.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from backend.domain.enums import Role
from backend.domain.errors import ResourceNotFound


class DemoControl(Protocol):
    def reset(self) -> dict[str, Any]: ...


class OperationsService:
    def __init__(self, uow_factory: UnitOfWorkFactory, runner: CommandRunner, clock: Clock,
                 authz: AuthorizationService) -> None:
        self._uow_factory = uow_factory
        self._runner = runner
        self._clock = clock
        self._authz = authz

    def deliveries(self, principal: Principal, aggregate_id: str | None) -> list[dict[str, Any]]:
        """§9.1: Operaciones ve metadatos de diagnóstico; el supervisor, el estado de entregas."""
        self._authz.ensure_role(principal, Role.OPERATIONS, Role.SUPERVISOR)
        with self._uow_factory() as uow:
            rows = uow.outbox.list(aggregate_id, limit=100)
        keep = ("event_id", "event_type", "aggregate_id", "aggregate_version", "request_id", "recipient_ref",
                "delivery_state", "attempt_count", "next_attempt_at", "lease_expires_at", "last_error",
                "delivered_at", "created_at")
        return [{k: r.get(k) for k in keep} for r in rows]

    def policies(self, principal: Principal) -> list[dict[str, Any]]:
        self._authz.ensure_role(principal, Role.SUPERVISOR, Role.COORDINATOR, Role.DIRECTOR)
        with self._uow_factory() as uow:
            return uow.policies.list()

    def install_policy(self, ctx: CommandContext, version: str) -> CommandResult:
        """E2-ADR-005 / QS-05: las nuevas solicitudes usan la versión instalada; las órdenes conservan la suya."""
        role = self._authz.ensure_role(ctx.principal, Role.SUPERVISOR)

        def handler(uow: UnitOfWork) -> CommandResult:
            now = self._clock.now()
            if uow.policies.get(version) is None:
                raise ResourceNotFound()
            previous = uow.policies.active().version
            uow.policies.install(version, now)
            record_audit(uow, ctx, now, entity_type="PolicyVersion", entity_id=version, action="POLICY_INSTALLED",
                         actor_role=role, before={"active": previous}, after={"active": version})
            return CommandResult({"activePolicyVersion": version, "previous": previous})

        return self._runner.run(ctx, "policy.install", {"version": version}, handler)
