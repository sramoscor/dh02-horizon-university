"""Aplicación FastAPI de C-API: middleware de correlación, errores y rutas."""

from fastapi import FastAPI, Request

from backend.http.deps import Services, new_correlation_id
from backend.http.problems import install_handlers
from backend.http.routes import router


def create_http_app(services: Services, health_check) -> FastAPI:
    app = FastAPI(title="DH-02 C-API", version="0.3.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.services = services

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        # §9.6: se correlacionan comando, auditoría, evento y entrega mediante identificadores.
        incoming = request.headers.get("x-correlation-id", "")
        request.state.correlation_id = incoming[:64] if incoming else new_correlation_id()
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = request.state.correlation_id
        return response

    install_handlers(app)
    app.include_router(router)

    @app.get("/health")
    def health():
        return health_check()

    return app
