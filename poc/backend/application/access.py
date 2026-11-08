"""Autenticación simulada y sesión opaca gestionada por el servidor (E2-FR-11, E2-ADR-006, §9.2)."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from backend.application.authorization import Principal
from backend.application.ports import Clock, IdentityPort, SessionRecord, UnitOfWorkFactory
from backend.domain.errors import SessionInvalid


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class NewSession:
    token: str
    csrf_token: str
    principal: Principal


class AccessService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, identity: IdentityPort, clock: Clock, ttl_minutes: int = 480
    ) -> None:
        self._uow_factory = uow_factory
        self._identity = identity
        self._clock = clock
        self._ttl = timedelta(minutes=ttl_minutes)

    def login(self, assertion: str) -> NewSession:
        """X-ID verifica la identidad; los roles salen de ROLE_ASSIGNMENT local, no del proveedor."""
        identity = self._identity.verify(assertion)
        now = self._clock.now()
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self._uow_factory() as uow:
            uow.access.create_session(
                hash_token(token),
                SessionRecord(identity.subject_id, identity.display_name, csrf, now + self._ttl),
                now,
            )
            grants = tuple(uow.access.active_grants(identity.subject_id))
            uow.commit()
        return NewSession(token, csrf, Principal(identity.subject_id, identity.display_name, grants))

    def resolve(self, token: str | None) -> tuple[Principal, str]:
        """Valida la sesión y relee las concesiones activas en cada petición (un rol desactivado deja de valer)."""
        if not token:
            raise SessionInvalid()
        with self._uow_factory() as uow:
            record = uow.access.get_session(hash_token(token))
            if record is None or record.expires_at <= self._clock.now():
                raise SessionInvalid()
            grants = tuple(uow.access.active_grants(record.subject_id))
        return Principal(record.subject_id, record.display_name, grants), record.csrf_token

    def logout(self, token: str) -> None:
        with self._uow_factory() as uow:
            uow.access.delete_session(hash_token(token))
            uow.commit()
