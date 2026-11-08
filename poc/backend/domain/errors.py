"""Errores de negocio (§8.3). El dominio no conoce HTTP: la capa http traduce `code` a estado HTTP."""

from typing import Any


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"
    title: str = "Error de negocio"

    def __init__(self, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra


class ValidationFailed(DomainError):
    code = "VALIDATION_FAILED"
    title = "Datos inválidos"


class InvalidStateTransition(DomainError):
    code = "INVALID_STATE_TRANSITION"
    title = "La transición no es legal desde el estado actual"


class DuplicateCandidateExists(DomainError):
    """E2-ADR-003 punto 4: la violación de unicidad se traduce a un error de negocio, nunca técnico."""

    code = "DUPLICATE_CANDIDATE_EXISTS"
    title = "Ya existe una orden activa para esta ubicación y categoría"

    def __init__(self, existing_work_order_id: str | None) -> None:
        super().__init__(
            "No se creó una nueva orden. Vincule la solicitud a la orden existente "
            "o indique que se trata de un problema distinto.",
            existingWorkOrderId=existing_work_order_id,
        )
        self.existing_work_order_id = existing_work_order_id


class PreconditionFailed(DomainError):
    code = "PRECONDITION_FAILED"
    title = "La versión del recurso cambió; recargue y reconcilie antes de reenviar"


class ActionForbidden(DomainError):
    code = "ACTION_FORBIDDEN"
    title = "Sin permiso para esta acción o alcance"


class ResourceNotFound(DomainError):
    code = "RESOURCE_NOT_FOUND"
    title = "El recurso no existe"


class IdempotencyKeyReused(DomainError):
    code = "IDEMPOTENCY_KEY_REUSED"
    title = "La clave de idempotencia ya se usó con un comando diferente"


class SessionInvalid(DomainError):
    code = "SESSION_INVALID"
    title = "Sesión inválida o expirada"


class LocationDirectoryUnavailable(DomainError):
    code = "LOCATION_DIRECTORY_UNAVAILABLE"
    title = "Sin proyección vigente del directorio de espacios"


class RequiredHeaderMissing(DomainError):
    code = "REQUIRED_HEADER_MISSING"
    title = "Falta una cabecera obligatoria"
