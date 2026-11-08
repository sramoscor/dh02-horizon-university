"""Cliente HTTP del contrato §8.1 para C-WEB (E2-ADR-009).

Streamlit solo habla HTTP con C-API (y, en modo demo, con el control de los simuladores). No importa el
backend ni accede a la base de datos; la autorización la decide siempre el servidor.
"""

import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

API_URL = os.environ.get("API_URL", "http://localhost:8000")
XID_URL = os.environ.get("XID_URL", "http://localhost:8090/x-id")
MOCKS_URL = os.environ.get("MOCKS_URL", "http://localhost:8090")
DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() in ("1", "true", "yes")


@dataclass
class Call:
    method: str
    endpoint: str
    status: int
    code: str | None = None


@dataclass
class ApiResult:
    status: int
    body: Any
    headers: dict[str, str]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def code(self) -> str | None:
        return self.body.get("code") if isinstance(self.body, dict) else None


@dataclass
class ApiClient:
    """Una sesión de C-API. La cookie HttpOnly vive en este cliente (lado servidor de Streamlit)."""

    subject: str
    http: httpx.Client = field(default_factory=lambda: httpx.Client(base_url=API_URL, timeout=15))
    csrf: str | None = None
    me: dict[str, Any] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    @property
    def roles(self) -> set[str]:
        return {r["role"] for r in self.me.get("roles", [])}

    def login(self) -> ApiResult:
        """X-ID simulado emite una aserción; C-API la verifica y abre la sesión (§9.2)."""
        assertion = httpx.post(f"{XID_URL}/authorize", json={"subject": self.subject}, timeout=10)
        self.calls.append(Call("POST", "X-ID /x-id/authorize", assertion.status_code))
        if assertion.status_code != 200:
            return ApiResult(assertion.status_code, {"code": "SESSION_INVALID", "detail": assertion.text}, {})
        result = self._send("POST", "/api/v1/auth/sessions", json={"assertion": assertion.json()["assertion"]})
        if result.ok:
            self.csrf = result.body["csrfToken"]
            self.me = result.body
        return result

    def _send(self, method: str, path: str, **kwargs) -> ApiResult:
        try:
            r = self.http.request(method, path, **kwargs)
            body = r.json() if r.content else None
        except httpx.HTTPError as exc:
            self.calls.append(Call(method, path, 0, "CONNECTION_ERROR"))
            return ApiResult(0, {"code": "CONNECTION_ERROR", "detail": str(exc)}, {})
        result = ApiResult(r.status_code, body, dict(r.headers))
        self.calls.append(Call(method, path, r.status_code, result.code))
        return result

    def get(self, path: str, **params) -> ApiResult:
        return self._send("GET", f"/api/v1{path}", params={k: v for k, v in params.items() if v not in (None, "")})

    def command(self, path: str, body: dict | None = None, *, etag: str | None = None,
                key: str | None = None) -> ApiResult:
        headers = {"X-CSRF-Token": self.csrf or "", "Idempotency-Key": key or f"web-{uuid.uuid4()}"}
        if etag:
            headers["If-Match"] = etag
        return self._send("POST", f"/api/v1{path}", json=body or {}, headers=headers)

    def logout(self) -> None:
        self._send("DELETE", "/api/v1/auth/session", headers={"X-CSRF-Token": self.csrf or ""})


def list_users() -> list[dict[str, str]]:
    r = httpx.get(f"{XID_URL}/users", timeout=10)
    r.raise_for_status()
    return r.json()["users"]


def mocks(method: str, path: str, **kwargs) -> httpx.Response:
    """Control de simuladores (solo Panel de demostración)."""
    return httpx.request(method, f"{MOCKS_URL}{path}", timeout=10, **kwargs)
