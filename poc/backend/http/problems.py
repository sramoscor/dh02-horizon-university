"""Errores en formato problem-details estilo RFC 9457 (§8.3). Sin trazas técnicas ni datos de terceros."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.domain.errors import DomainError

log = logging.getLogger("dh02.api")

STATUS_BY_CODE = {
    "REQUIRED_HEADER_MISSING": 400,
    "SESSION_INVALID": 401,
    "ACTION_FORBIDDEN": 403,
    "RESOURCE_NOT_FOUND": 404,
    "DUPLICATE_CANDIDATE_EXISTS": 409,
    "INVALID_STATE_TRANSITION": 409,
    "IDEMPOTENCY_KEY_REUSED": 409,
    "PRECONDITION_FAILED": 412,
    "VALIDATION_FAILED": 422,
    "LOCATION_DIRECTORY_UNAVAILABLE": 503,
}


def problem(request: Request, status: int, code: str, title: str, detail: str, **extra) -> JSONResponse:
    body = {
        "type": f"urn:dh02:problem:{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        "instance": request.url.path,
        "correlationId": getattr(request.state, "correlation_id", None),
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type="application/problem+json",
                        headers={"Cache-Control": "no-store"})


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status = STATUS_BY_CODE.get(exc.code, 400)
        # Señal de observabilidad §9.6: resultado por código (sin datos privados).
        log.info("command_result code=%s status=%s path=%s corr=%s", exc.code, status, request.url.path,
                 getattr(request.state, "correlation_id", None))
        return problem(request, status, exc.code, exc.title, exc.detail, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = {".".join(str(p) for p in e["loc"][1:]): e["msg"] for e in exc.errors()}
        return problem(request, 422, "VALIDATION_FAILED", "Datos inválidos", "Corrija los campos indicados.",
                       fields=fields)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unexpected_error path=%s", request.url.path)
        return problem(request, 500, "INTERNAL_ERROR", "Falla temporal", "Reintente con la misma clave.")
