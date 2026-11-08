"""Sesión, CSRF, idempotencia y precondiciones (§8.1, §8.4, §9.2; E2-ADR-006)."""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, Request

from backend.application.access import AccessService
from backend.application.authorization import Principal
from backend.application.commands import CommandContext
from backend.application.operations import DemoControl, OperationsService
from backend.application.requests import RequestService
from backend.application.triage import TriageApplicationService
from backend.application.work_orders import WorkOrderService
from backend.domain.errors import RequiredHeaderMissing, SessionInvalid
from backend.domain.model import parse_etag

SESSION_COOKIE = "dh02_session"


@dataclass
class Services:
    access: AccessService
    requests: RequestService
    triage: TriageApplicationService
    work_orders: WorkOrderService
    operations: OperationsService
    demo: DemoControl | None
    cookie_secure: bool


def services(request: Request) -> Services:
    return request.app.state.services


@dataclass
class Session:
    principal: Principal
    csrf_token: str


def current_session(request: Request, svc: Services = Depends(services)) -> Session:
    """El actor se deriva de la sesión del servidor, nunca de campos del cliente (T-SEC-03)."""
    principal, csrf = svc.access.resolve(request.cookies.get(SESSION_COOKIE))
    request.state.subject_id = principal.subject_id
    return Session(principal, csrf)


def current_principal(session: Session = Depends(current_session)) -> Principal:
    return session.principal


def csrf_protected(
    session: Session = Depends(current_session),
    x_csrf_token: str | None = Header(default=None),
) -> Session:
    """T-SEC-04: comando que muta sin token CSRF válido → sin efecto."""
    if not x_csrf_token:
        raise RequiredHeaderMissing("Falta el token CSRF.", header="X-CSRF-Token")
    if x_csrf_token != session.csrf_token:
        raise SessionInvalid("Token CSRF inválido para esta sesión.")
    return session


def _context(request: Request, session: Session, idempotency_key: str | None, expected: int | None):
    if not idempotency_key:
        raise RequiredHeaderMissing("Falta la cabecera Idempotency-Key.", header="Idempotency-Key")
    if len(idempotency_key) > 200:
        raise RequiredHeaderMissing("Idempotency-Key demasiado larga.", header="Idempotency-Key")
    return CommandContext(session.principal, idempotency_key, request.state.correlation_id, expected)


def command_context(
    request: Request,
    session: Session = Depends(csrf_protected),
    idempotency_key: str | None = Header(default=None),
) -> CommandContext:
    return _context(request, session, idempotency_key, None)


def versioned(prefix: str, param: str):
    """Comandos sobre recursos versionados exigen `If-Match` (T-API-03)."""

    def dependency(
        request: Request,
        session: Session = Depends(csrf_protected),
        idempotency_key: str | None = Header(default=None),
        if_match: str | None = Header(default=None),
    ) -> CommandContext:
        if not if_match:
            raise RequiredHeaderMissing("Falta la precondición If-Match.", header="If-Match")
        expected = parse_etag(if_match, prefix, request.path_params[param])
        return _context(request, session, idempotency_key, expected)

    return dependency


request_version = versioned("request", "request_id")
work_order_version = versioned("work-order", "work_order_id")


def new_correlation_id() -> str:
    return f"corr-{uuid.uuid4().hex[:12]}"
