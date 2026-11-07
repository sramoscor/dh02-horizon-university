"""Raíz de composición compartida por C-API, C-WORKER y las pruebas (§5.4 bootstrap)."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Engine

from backend.application.access import AccessService
from backend.application.authorization import AuthorizationService
from backend.application.commands import CommandRunner
from backend.application.operations import OperationsService
from backend.application.ports import Clock, FaultInjector, IdentityPort
from backend.application.requests import LocationService, RequestService
from backend.application.triage import TriageApplicationService
from backend.application.work_orders import WorkOrderService
from backend.http.deps import Services
from backend.infrastructure.adapters import HttpIdentityAdapter
from backend.infrastructure.seeding import SqlDemoControl
from backend.infrastructure.settings import Settings
from backend.infrastructure.sql import SqlUnitOfWorkFactory


@dataclass
class Container:
    settings: Settings
    engine: Engine
    clock: Clock
    uow_factory: SqlUnitOfWorkFactory
    services: Services


def build_container(
    settings: Settings,
    engine: Engine,
    clock: Clock,
    identity: IdentityPort | None = None,
    faults: FaultInjector | None = None,
) -> Container:
    uow_factory = SqlUnitOfWorkFactory(engine, faults)
    runner = CommandRunner(uow_factory, clock)
    authz = AuthorizationService()
    locations = LocationService(timedelta(hours=settings.directory_max_age_hours))
    services = Services(
        access=AccessService(uow_factory, identity or HttpIdentityAdapter(settings.xid_url), clock,
                             settings.session_ttl_minutes),
        requests=RequestService(uow_factory, runner, clock, authz, locations),
        triage=TriageApplicationService(uow_factory, runner, clock, authz, locations),
        work_orders=WorkOrderService(uow_factory, runner, clock, authz),
        operations=OperationsService(uow_factory, runner, clock, authz),
        demo=SqlDemoControl(engine, clock) if settings.demo_mode else None,
        cookie_secure=settings.cookie_secure,
    )
    return Container(settings, engine, clock, uow_factory, services)
