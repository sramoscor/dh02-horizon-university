"""Solicitante / representante del edificio: confirmar o reabrir (E2-FR-06)."""

import streamlit as st

from web.ui import footer, label, refresh_button, show_result

api = st.session_state.get("api")
st.title("✅ Confirmaciones")
if api is None:
    st.stop()
refresh_button("refresh_conf")
pending = api.get("/work-orders", status="PENDING_CONFIRMATION")
if not show_result(pending) or not pending.body:
    st.info("No hay órdenes pendientes de confirmación visibles para usted.")
    footer(api)
    st.stop()
for o in pending.body:
    wid = o["workOrderId"]
    with st.container(border=True):
        st.markdown(f"**{wid}** · {o['locationName']} · {o['category']} · {label(o['status'])}")
        st.caption(f"Solicitud {o['requestId']} · técnico {o['assigneeId'] or '—'} · {o['etag']}")
        reason = st.text_input("Motivo (obligatorio para reabrir)", key=f"r-{wid}")
        a, b = st.columns(2)
        if a.button("Confirmar resultado", key=f"ok-{wid}"):
            show_result(api.command(f"/work-orders/{wid}/confirmations", {"outcome": "CONFIRMED"}, etag=o["etag"]),
                        "Orden cerrada.") and st.rerun()
        if b.button("Reabrir: el problema persiste", key=f"re-{wid}"):
            show_result(api.command(f"/work-orders/{wid}/confirmations", {"outcome": "REOPEN", "reason": reason},
                                    etag=o["etag"]), "Orden reabierta.") and st.rerun()
footer(api)
