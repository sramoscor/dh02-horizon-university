"""Humo contra los contenedores reales (T-ENV-01): X-ID → C-API → C-DB → C-WORKER → X-NOTIFY, y C-WEB.

Uso: `docker compose --profile test run --rm test python -m tests.smoke` (lo invoca scripts/t_env_01.sh).
Deja la demo en su estado inicial al terminar.
"""

import os
import sys
import time
import uuid

import httpx

API = os.environ.get("SMOKE_API_URL", "http://api:8000")
MOCKS = os.environ.get("MOCKS_URL", "http://mocks:8090")
WEB = os.environ.get("SMOKE_WEB_URL", "http://web:8501")


def session(subject: str) -> tuple[httpx.Client, str]:
    assertion = httpx.post(f"{MOCKS}/x-id/authorize", json={"subject": subject}, timeout=10).json()["assertion"]
    client = httpx.Client(base_url=API, timeout=15)
    r = client.post("/api/v1/auth/sessions", json={"assertion": assertion})
    r.raise_for_status()
    return client, r.json()["csrfToken"]


def cmd(client: httpx.Client, csrf: str, path: str, body=None, etag=None) -> httpx.Response:
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": f"smoke-{uuid.uuid4()}"}
    if etag:
        headers["If-Match"] = etag
    return client.post(f"/api/v1{path}", json=body or {}, headers=headers)


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'OK' if condition else 'FALLA'}] {label} {detail}")
    if not condition:
        raise SystemExit(1)


def main() -> int:
    check("C-API /health", httpx.get(f"{API}/health", timeout=10).json().get("status") == "ok")
    check("C-WEB /_stcore/health", httpx.get(f"{WEB}/_stcore/health", timeout=10).status_code == 200)
    student, s_csrf = session("usr-017")
    coord, c_csrf = session("coord-03")
    sup, _ = session("sup-01")
    locs = student.get("/api/v1/locations").json()
    check("proyección del directorio sembrada", len(locs) == 6 and not any(x["stale"] for x in locs), f"{len(locs)}")
    r = cmd(student, s_csrf, "/requests", {"locationId": "CIE-L-204", "description": "Humo T-ENV-01",
                                           "suggestedUrgency": "MEDIUM", "suggestedCategory": "PLUMBING"})
    check("crear borrador", r.status_code == 201, r.json().get("requestId", r.text))
    rid = r.json()["requestId"]
    r = cmd(student, s_csrf, f"/requests/{rid}/submissions", etag=r.headers["etag"])
    check("enviar", r.status_code == 200)
    r = cmd(coord, c_csrf, f"/requests/{rid}/triage-reviews", etag=r.headers["etag"])
    check("tomar para triaje", r.status_code == 200)
    r = cmd(coord, c_csrf, f"/requests/{rid}/triage", {"action": "APPROVE_CREATE_WO", "category": "PLUMBING",
                                                       "validatedUrgency": "HIGH", "assigneeId": "TECH-014"},
            etag=r.headers["etag"])
    check("triaje aprueba y asigna", r.status_code == 201, r.json().get("workOrder", {}).get("workOrderId", r.text))
    wo = r.json()["workOrder"]["workOrderId"]
    deadline = time.monotonic() + 60
    states: set[str] = set()
    while time.monotonic() < deadline:
        rows = sup.get("/api/v1/deliveries", params={"aggregateId": wo}).json()
        states = {d["delivery_state"] for d in rows}
        if rows and states == {"DELIVERED"}:
            break
        time.sleep(1)
    check("C-WORKER entrega el outbox a X-NOTIFY", states == {"DELIVERED"}, str(states))
    reset = httpx.post(f"{API}/api/v1/demo/reset", timeout=30)
    httpx.post(f"{MOCKS}/_control/reset", timeout=10)
    check("demo-reset", reset.status_code == 200)
    print("HUMO T-ENV-01: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
