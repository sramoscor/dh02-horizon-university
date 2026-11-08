"""Contrato HTTP `/api/v1` (§8.1). Esta capa valida, delega y mapea respuestas; no decide reglas."""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from backend.application.authorization import Principal
from backend.application.commands import CommandContext, CommandResult
from backend.domain.errors import ResourceNotFound
from backend.http.deps import (
    SESSION_COOKIE,
    Services,
    Session,
    command_context,
    csrf_protected,
    current_principal,
    current_session,
    request_version,
    services,
    work_order_version,
)

router = APIRouter(prefix="/api/v1")
NO_STORE = {"Cache-Control": "no-store"}


def respond(result: CommandResult) -> JSONResponse:
    headers = dict(NO_STORE)
    if result.etag:
        headers["ETag"] = result.etag
    if result.location:
        headers["Location"] = result.location
    if result.replayed:
        headers["Idempotent-Replayed"] = "true"
    return JSONResponse(result.body, status_code=201 if result.created else 200, headers=headers)


def ok(body: Any) -> JSONResponse:
    return JSONResponse(body, headers=NO_STORE)


def principal_dto(p: Principal) -> dict[str, Any]:
    return {"subjectId": p.subject_id, "displayName": p.display_name,
            "roles": [{"role": g.role, "scopeType": g.scope_type, "scopeId": g.scope_id} for g in p.grants]}


# --------------------------------------------------------------------------- identidad (§9.2)


@router.post("/auth/sessions")
def login(response: Response, body: dict = Body(...), svc: Services = Depends(services)):
    """X-ID simulado → sesión opaca HttpOnly + token CSRF. El rol no viaja en la petición."""
    session = svc.access.login(str(body.get("assertion", "")))
    response.set_cookie(SESSION_COOKIE, session.token, httponly=True, samesite="strict",
                        secure=svc.cookie_secure, path="/api")
    response.headers["Cache-Control"] = "no-store"
    return {**principal_dto(session.principal), "csrfToken": session.csrf_token}


@router.get("/auth/session")
def me(session: Session = Depends(current_session)):
    return ok(principal_dto(session.principal))


@router.delete("/auth/session")
def logout(request: Request, response: Response, session: Session = Depends(csrf_protected),
           svc: Services = Depends(services)):
    svc.access.logout(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/api")
    return {"loggedOut": True}


# --------------------------------------------------------------------------- solicitudes


@router.get("/locations")
def locations(p: Principal = Depends(current_principal), svc: Services = Depends(services)):
    return ok(svc.requests.locations())


@router.post("/requests")
def create_request(body: dict = Body(...), ctx: CommandContext = Depends(command_context),
                   svc: Services = Depends(services)):
    return respond(svc.requests.create_draft(ctx, body))


@router.get("/requests")
def list_requests(view: str = Query("mine"), p: Principal = Depends(current_principal),
                  svc: Services = Depends(services)):
    return ok(svc.requests.list(p, view))


@router.get("/requests/{request_id}")
def get_request(request_id: str, p: Principal = Depends(current_principal), svc: Services = Depends(services)):
    return ok(svc.requests.get(p, request_id))


@router.get("/requests/{request_id}/status")
def request_status(request_id: str, p: Principal = Depends(current_principal), svc: Services = Depends(services)):
    return ok(svc.requests.combined_status(p, request_id))


@router.post("/requests/{request_id}/submissions")
def submit(request_id: str, body: dict | None = Body(default=None), ctx: CommandContext = Depends(request_version),
           svc: Services = Depends(services)):
    return respond(svc.requests.submit(ctx, request_id, (body or {}).get("additionalInfo")))


@router.post("/requests/{request_id}/withdrawals")
def withdraw(request_id: str, ctx: CommandContext = Depends(request_version), svc: Services = Depends(services)):
    return respond(svc.requests.withdraw(ctx, request_id))


@router.post("/requests/{request_id}/triage-reviews")
def start_review(request_id: str, ctx: CommandContext = Depends(request_version),
                 svc: Services = Depends(services)):
    return respond(svc.triage.start_review(ctx, request_id))


@router.get("/requests/{request_id}/duplicate-candidates")
def duplicate_candidates(request_id: str, category: str | None = None, p: Principal = Depends(current_principal),
                         svc: Services = Depends(services)):
    return ok(svc.triage.duplicate_candidates(p, request_id, category))


@router.post("/requests/{request_id}/triage")
def triage(request_id: str, body: dict = Body(...), ctx: CommandContext = Depends(request_version),
           svc: Services = Depends(services)):
    return respond(svc.triage.decide(ctx, request_id, body))


# --------------------------------------------------------------------------- órdenes de trabajo


@router.get("/work-orders")
def search_work_orders(request: Request, p: Principal = Depends(current_principal),
                       svc: Services = Depends(services)):
    return ok(svc.work_orders.search(p, dict(request.query_params)))


@router.get("/work-orders/{work_order_id}")
def get_work_order(work_order_id: str, p: Principal = Depends(current_principal), svc: Services = Depends(services)):
    body = svc.work_orders.get(p, work_order_id)
    return JSONResponse(body, headers={**NO_STORE, "ETag": body["etag"]})


@router.get("/work-orders/{work_order_id}/history")
def history(work_order_id: str, p: Principal = Depends(current_principal), svc: Services = Depends(services)):
    return ok(svc.work_orders.history(p, work_order_id))


@router.get("/work-orders/{work_order_id}/assignment-candidates")
def assignment_candidates(work_order_id: str, p: Principal = Depends(current_principal),
                          svc: Services = Depends(services)):
    return ok(svc.work_orders.assignment_candidates(p, work_order_id))


@router.post("/work-orders/{work_order_id}/assignments")
def assign(work_order_id: str, body: dict | None = Body(default=None),
           ctx: CommandContext = Depends(work_order_version), svc: Services = Depends(services)):
    body = body or {}
    return respond(svc.work_orders.assign(ctx, work_order_id, body.get("assigneeId"), body.get("reason")))


@router.post("/work-orders/{work_order_id}/start")
def start(work_order_id: str, ctx: CommandContext = Depends(work_order_version), svc: Services = Depends(services)):
    return respond(svc.work_orders.start(ctx, work_order_id))


@router.post("/work-orders/{work_order_id}/blocks")
def block(work_order_id: str, body: dict = Body(...), ctx: CommandContext = Depends(work_order_version),
          svc: Services = Depends(services)):
    return respond(svc.work_orders.block(ctx, work_order_id, body.get("blockType", ""), body.get("detail", "")))


@router.post("/work-orders/{work_order_id}/blocks/{block_id}/resolution")
def resolve_block(work_order_id: str, block_id: str, ctx: CommandContext = Depends(work_order_version),
                  svc: Services = Depends(services)):
    return respond(svc.work_orders.resolve_block(ctx, work_order_id, block_id))


@router.post("/work-orders/{work_order_id}/materials")
def materials(work_order_id: str, body: dict = Body(...), ctx: CommandContext = Depends(command_context),
              svc: Services = Depends(services)):
    return respond(svc.work_orders.record_work(ctx, work_order_id, "material", body))


@router.post("/work-orders/{work_order_id}/observations")
def observations(work_order_id: str, body: dict = Body(...), ctx: CommandContext = Depends(command_context),
                 svc: Services = Depends(services)):
    return respond(svc.work_orders.record_work(ctx, work_order_id, "observation", body))


@router.post("/work-orders/{work_order_id}/completions")
def complete(work_order_id: str, ctx: CommandContext = Depends(work_order_version),
             svc: Services = Depends(services)):
    return respond(svc.work_orders.complete(ctx, work_order_id))


@router.post("/work-orders/{work_order_id}/confirmations")
def confirm(work_order_id: str, body: dict = Body(...), ctx: CommandContext = Depends(work_order_version),
            svc: Services = Depends(services)):
    return respond(svc.work_orders.confirm(ctx, work_order_id, body.get("outcome", ""), body.get("reason")))


@router.post("/work-orders/{work_order_id}/admin-closures")
def admin_close(work_order_id: str, body: dict | None = Body(default=None),
                ctx: CommandContext = Depends(work_order_version), svc: Services = Depends(services)):
    return respond(svc.work_orders.admin_close(ctx, work_order_id, (body or {}).get("reason")))


@router.post("/work-orders/{work_order_id}/cancellations")
def cancel(work_order_id: str, body: dict | None = Body(default=None),
           ctx: CommandContext = Depends(work_order_version), svc: Services = Depends(services)):
    return respond(svc.work_orders.cancel(ctx, work_order_id, (body or {}).get("reason")))


@router.get("/technicians/workload")
def workload(p: Principal = Depends(current_principal), svc: Services = Depends(services)):
    return ok(svc.work_orders.workload(p))


# --------------------------------------------------------------------------- operaciones y políticas


@router.get("/deliveries")
def deliveries(aggregate_id: str | None = Query(None, alias="aggregateId"),
               p: Principal = Depends(current_principal), svc: Services = Depends(services)):
    return ok(svc.operations.deliveries(p, aggregate_id))


@router.get("/policies")
def policies(p: Principal = Depends(current_principal), svc: Services = Depends(services)):
    return ok(svc.operations.policies(p))


@router.post("/policies/{version}/installation")
def install_policy(version: str, ctx: CommandContext = Depends(command_context), svc: Services = Depends(services)):
    return respond(svc.operations.install_policy(ctx, version))


# --------------------------------------------------------------------------- demo (solo DEMO_MODE=true)


@router.get("/demo")
def demo_info(svc: Services = Depends(services)):
    return {"demoMode": svc.demo is not None}


@router.post("/demo/reset")
def demo_reset(svc: Services = Depends(services)):
    """Equivale a `make demo-reset`. No existe fuera del entorno de demostración."""
    if svc.demo is None:
        raise ResourceNotFound()
    return ok(svc.demo.reset())
