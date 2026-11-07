"""Utilidades de presentación: mensajes legibles por `code` (§8.3), estados con texto y pie de auditoría."""

from typing import Any

import streamlit as st

from web.client import ApiClient, ApiResult

MESSAGES = {
    "DUPLICATE_CANDIDATE_EXISTS": "Ya existe una orden activa ({existingWorkOrderId}) para esta ubicación y "
                                  "categoría. No se creó otra: vincule la solicitud o declare un problema distinto.",
    "ACTION_FORBIDDEN": "El servidor denegó la acción: no tiene permiso para este recurso o alcance.",
    "INVALID_STATE_TRANSITION": "La acción no es válida en el estado actual del recurso.",
    "PRECONDITION_FAILED": "Otra persona modificó el recurso. Pulse «Actualizar» y vuelva a intentarlo.",
    "VALIDATION_FAILED": "Revise los datos: {detail}",
    "SESSION_INVALID": "La sesión no es válida o expiró. Vuelva a iniciar sesión.",
    "LOCATION_DIRECTORY_UNAVAILABLE": "El directorio de espacios no está actualizado (más de 24 h). "
                                      "Intente más tarde.",
    "IDEMPOTENCY_KEY_REUSED": "La operación ya se registró con otro contenido.",
    "REQUIRED_HEADER_MISSING": "Falta información obligatoria en la petición ({detail}).",
    "RESOURCE_NOT_FOUND": "El recurso no existe.",
    "CONNECTION_ERROR": "No se pudo contactar a C-API.",
    "INTERNAL_ERROR": "Falla temporal del servidor. Reintente.",
}

STATUS_LABEL = {
    "DRAFT": "📝 Borrador", "SUBMITTED": "📨 Enviada", "UNDER_TRIAGE": "🔎 En triaje",
    "NEEDS_INFO": "❓ Requiere información", "APPROVED": "✅ Aprobada (orden creada)", "REJECTED": "⛔ Rechazada",
    "REDIRECTED": "↪️ Redirigida", "DUPLICATE_LINKED": "🔗 Vinculada a otra orden", "WITHDRAWN": "↩️ Retirada",
    "CREATED": "🆕 Creada", "ASSIGNED": "👷 Asignada", "IN_PROGRESS": "🔧 En progreso", "BLOCKED": "⏸️ Bloqueada",
    "PENDING_CONFIRMATION": "🕒 Pendiente de confirmación", "REOPENED": "🔁 Reabierta", "CLOSED": "🏁 Cerrada",
    "ADMIN_CLOSED": "🗂️ Cierre administrativo", "CANCELLED": "✖️ Cancelada", "PENDING": "⏳ Pendiente",
    "LEASED": "📤 En envío", "RETRY_WAIT": "🔄 En reintento", "DELIVERED": "📬 Entregada",
    "DEAD_LETTER": "🛑 Retenida (dead letter)", "RUNNING": "▶️ Corriendo", "PAUSED": "⏸️ Pausado",
    "OVERDUE": "⚠️ Vencido", "STOPPED": "⏹️ Detenido",
}


def label(status: str | None) -> str:
    return STATUS_LABEL.get(status or "", status or "—")


def show_result(result: ApiResult, success: str | None = None) -> bool:
    if result.ok:
        if success:
            st.success(success)
        return True
    body = result.body if isinstance(result.body, dict) else {}
    template = MESSAGES.get(result.code or "", "{detail}")
    try:
        text = template.format(**{"detail": body.get("detail", ""), **body})
    except (KeyError, IndexError):
        text = body.get("detail") or template
    st.error(f"{text}  \n`{result.status} {result.code}`")
    return False


def footer(api: ApiClient | None) -> None:
    """Muestra los endpoints reales invocados y su código: la interfaz no decide nada."""
    if api is None:
        return
    with st.expander(f"🔌 Llamadas a C-API en esta vista ({len(api.calls)})", expanded=False):
        if not api.calls:
            st.caption("Sin llamadas.")
        for c in api.calls:
            st.code(f"{c.method} {c.endpoint} → {c.status}" + (f" {c.code}" if c.code else ""), language="http")


def combined_status(status: dict[str, Any]) -> None:
    """§6.8: tres dimensiones independientes, con texto y no solo color."""
    a, b, c = st.columns(3)
    a.metric("Solicitud", label(status["request"]["status"]))
    wo = status.get("workOrder")
    b.metric("Orden", label(wo["status"]) if wo else "— sin orden", wo["workOrderId"] if wo else None,
             delta_color="off")
    n = status.get("notification")
    c.metric("Notificación al solicitante", label(n["state"]) if n else "— sin eventos",
             f"{n['eventType']} · intentos {n['attempts']}" if n else None, delta_color="off")


def refresh_button(key: str) -> None:
    if st.button("🔄 Actualizar", key=key):
        st.rerun()
