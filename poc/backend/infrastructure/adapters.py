"""Adaptadores HTTP de X-ID, X-DIR y X-NOTIFY (E2-ADR-008; §8.5, §8.6; T-CONTRACT-01)."""

from typing import Any

import httpx

from backend.application.ports import (
    Delivery,
    PermanentDeliveryFailure,
    Space,
    TemporaryDeliveryFailure,
    VerifiedIdentity,
)
from backend.domain.errors import SessionInvalid


class HttpIdentityAdapter:
    """Valida la aserción con el proveedor simulado. No inventa identidades si falla (§5.1)."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def verify(self, assertion: str) -> VerifiedIdentity:
        try:
            r = httpx.post(f"{self._base}/verify", json={"assertion": assertion}, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise SessionInvalid("Proveedor de identidad no disponible.") from exc
        if r.status_code != 200:
            raise SessionInvalid("Aserción de identidad rechazada.")
        data = r.json()
        subject, name = data.get("subject"), data.get("displayName")
        if not isinstance(subject, str) or not subject or not isinstance(name, str):
            raise SessionInvalid("Respuesta de identidad inválida.")
        return VerifiedIdentity(subject, name)


class DirectoryDataInvalid(ValueError):
    pass


SPACE_FIELDS: dict[str, type | tuple[type, ...]] = {
    "locationId": str, "buildingId": str, "campus": str, "displayName": str, "active": bool,
    "restrictedAccess": bool, "buildingRepresentativeId": (str, type(None)), "directoryVersion": str,
    "sourceRevision": int,
}


def parse_space(raw: Any) -> Space:
    """Valida el esquema de §8.5. Datos inválidos se rechazan: nunca degradan la proyección (QS-09)."""
    if not isinstance(raw, dict):
        raise DirectoryDataInvalid("espacio no es un objeto")
    for name, kind in SPACE_FIELDS.items():
        value = raw.get(name)
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise DirectoryDataInvalid(f"campo {name} inválido")
    if not raw["locationId"].strip() or raw["sourceRevision"] < 0:
        raise DirectoryDataInvalid("identificador o revisión inválidos")
    return Space(raw["locationId"], raw["buildingId"], raw["campus"], raw["displayName"], raw["active"],
                 raw["restrictedAccess"], raw["buildingRepresentativeId"], raw["directoryVersion"],
                 raw["sourceRevision"])


class HttpDirectoryAdapter:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def fetch_all(self) -> list[Space]:
        r = httpx.get(f"{self._base}/spaces", timeout=self._timeout)
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict) or not isinstance(body.get("spaces"), list):
            raise DirectoryDataInvalid("respuesta sin lista de espacios")
        return [parse_space(s) for s in body["spaces"]]


class HttpNotificationAdapter:
    """La clave de entrega viaja como `Idempotency-Key`: el receptor deduplica (QS-02, R-013)."""

    def __init__(self, base_url: str, timeout: float) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def send(self, delivery: Delivery) -> None:
        try:
            r = httpx.post(
                f"{self._base}/messages",
                json={"recipient": delivery.recipient_ref, "channel": delivery.channel,
                      "eventType": delivery.event_type, "message": delivery.message},
                headers={"Idempotency-Key": delivery.delivery_key},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise TemporaryDeliveryFailure("tiempo agotado") from exc
        except httpx.HTTPError as exc:
            raise TemporaryDeliveryFailure(f"sin conexión: {type(exc).__name__}") from exc
        if r.status_code in (408, 429) or r.status_code >= 500:
            raise TemporaryDeliveryFailure(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise PermanentDeliveryFailure(f"HTTP {r.status_code}")
