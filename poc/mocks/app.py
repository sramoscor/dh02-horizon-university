"""Simuladores de sistemas externos con fallos controlables (E2-ADR-008): X-ID, X-DIR, X-NOTIFY.

Estado en memoria de un solo proceso. No importa el backend (contrato `mocks-independent`).
Control: `POST /_control/{xid|xdir|xnotify}` con `{"mode": ..., "latencyMs": ...}`; `POST /_control/reset`.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

FIXTURES = Path(os.environ.get("FIXTURES_DIR", Path(__file__).resolve().parents[1] / "fixtures"))
SECRET = os.environ.get("XID_SIGNING_SECRET", "demo-only-not-a-real-secret").encode()
USERS = {u["subject"]: u for u in json.loads((FIXTURES / "users.json").read_text())["users"]}
DIRECTORY = json.loads((FIXTURES / "directory.json").read_text())

MODES = {"xid": {"up", "down"}, "xdir": {"up", "down", "invalid", "regress"},
         "xnotify": {"up", "down", "slow", "error400"}}

app = FastAPI(title="DH-02 simuladores externos (X-ID, X-DIR, X-NOTIFY)")
_lock = threading.Lock()
state: dict[str, Any] = {}


def _reset() -> None:
    with _lock:
        state.clear()
        state.update(
            control={name: {"mode": "up", "latencyMs": 0} for name in MODES},
            used_nonces=set(),
            received={},  # Idempotency-Key -> mensaje (deduplicación del receptor)
            duplicates=0,
            attempts=0,
            log=[],
        )


_reset()


def _gate(service: str) -> dict[str, Any]:
    ctl = state["control"][service]
    if ctl["latencyMs"]:
        time.sleep(ctl["latencyMs"] / 1000)
    return ctl


# --------------------------------------------------------------------------- control


@app.get("/_control")
def get_control():
    return {"control": state["control"], "xnotify": {"unique": len(state["received"]),
                                                      "duplicates": state["duplicates"],
                                                      "attempts": state["attempts"]}}


@app.post("/_control/reset")
def reset_control():
    _reset()
    return get_control()


@app.post("/_control/{service}")
def set_control(service: str, body: dict):
    if service not in MODES or body.get("mode", "up") not in MODES[service]:
        raise HTTPException(422, f"modo inválido; permitidos: {sorted(MODES.get(service, []))}")
    with _lock:
        state["control"][service] = {"mode": body.get("mode", "up"), "latencyMs": int(body.get("latencyMs", 0))}
        state["log"].append({"at": time.time(), "service": service, **state["control"][service]})
    return get_control()


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------- X-ID (identidad simulada)


def _sign(payload: bytes) -> str:
    return hmac.new(SECRET, payload, hashlib.sha256).hexdigest()


@app.get("/x-id/users")
def users():
    return {"users": list(USERS.values())}


@app.post("/x-id/authorize")
def authorize(body: dict):
    """Simula el retorno del proveedor institucional: emite una aserción de un solo uso (60 s)."""
    if _gate("xid")["mode"] == "down":
        raise HTTPException(503, "X-ID no disponible")
    subject = body.get("subject")
    if subject not in USERS:
        raise HTTPException(404, "sujeto desconocido")
    payload = json.dumps({"sub": subject, "exp": time.time() + 60, "nonce": secrets.token_hex(8)}).encode()
    return {"assertion": base64.urlsafe_b64encode(payload).decode() + "." + _sign(payload)}


@app.post("/x-id/verify")
def verify(body: dict):
    if _gate("xid")["mode"] == "down":
        raise HTTPException(503, "X-ID no disponible")
    try:
        raw, signature = str(body.get("assertion", "")).split(".", 1)
        payload = base64.urlsafe_b64decode(raw.encode())
        claims = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(401, "aserción mal formada") from None
    if not hmac.compare_digest(_sign(payload), signature) or claims["exp"] < time.time():
        raise HTTPException(401, "aserción inválida o vencida")
    with _lock:
        if claims["nonce"] in state["used_nonces"]:
            raise HTTPException(401, "aserción ya utilizada")
        state["used_nonces"].add(claims["nonce"])
    user = USERS[claims["sub"]]
    return {"subject": user["subject"], "displayName": user["displayName"]}


# --------------------------------------------------------------------------- X-DIR (directorio simulado)


@app.get("/x-dir/spaces")
def spaces():
    mode = _gate("xdir")["mode"]
    if mode == "down":
        raise HTTPException(503, "X-DIR no disponible")
    revision = DIRECTORY["sourceRevision"] - (1 if mode == "regress" else 0)
    items = [{**s, "directoryVersion": DIRECTORY["directoryVersion"], "sourceRevision": revision}
             for s in DIRECTORY["spaces"]]
    if mode == "invalid":
        items[0] = {**items[0], "active": "sí", "sourceRevision": "118"}  # esquema roto a propósito
    return {"directoryVersion": DIRECTORY["directoryVersion"], "spaces": items}


# --------------------------------------------------------------------------- X-NOTIFY (notificaciones)


@app.post("/x-notify/messages")
def receive(request_body: dict, idempotency_key: str | None = Header(default=None)):
    with _lock:
        state["attempts"] += 1
    mode = _gate("xnotify")["mode"]
    if mode == "down":
        return JSONResponse({"error": "X-NOTIFY no disponible"}, status_code=503)
    if mode == "slow":
        time.sleep(30)  # más que el timeout del adaptador → tiempo agotado
    if mode == "error400":
        return JSONResponse({"error": "destinatario rechazado"}, status_code=400)
    if not idempotency_key:
        return JSONResponse({"error": "Idempotency-Key obligatoria"}, status_code=400)
    with _lock:
        if idempotency_key in state["received"]:
            state["duplicates"] += 1  # el receptor deduplica: sin efecto adicional
            return {"accepted": True, "duplicate": True}
        state["received"][idempotency_key] = {"receivedAt": time.time(), **request_body}
    return JSONResponse({"accepted": True, "duplicate": False}, status_code=202)


@app.get("/x-notify/messages")
def received(request: Request):
    items = [{"deliveryKey": k, **v} for k, v in state["received"].items()]
    return {"unique": len(items), "duplicates": state["duplicates"], "attempts": state["attempts"],
            "messages": items[-int(request.query_params.get("limit", 50)):]}
