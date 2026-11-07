"""Línea de tiempo de AuditEvent por orden (QS-03; E2-FR-09)."""

import streamlit as st

from web.ui import footer, refresh_button, show_result

api = st.session_state.get("api")
st.title("🕓 Historial de auditoría")
if api is None:
    st.stop()
refresh_button("refresh_hist")
wid = st.text_input("Orden de trabajo", st.session_state.get("history_wo", "WO-3001"))
st.session_state["history_wo"] = wid
history = api.get(f"/work-orders/{wid}/history")
if show_result(history):
    for h in history.body:
        before = (h["before"] or {}).get("status")
        after = (h["after"] or {}).get("status")
        change = f"{before or '∅'} → {after or '∅'}" if (before or after) else ""
        with st.container(border=True):
            st.markdown(f"`#{h['seq']}` **{h['action']}** · {h['entityType']} `{h['entityId']}` · {change}")
            st.caption(f"{h['occurredAt']} · autor `{h['actorId']}` · rol `{h['actorRole']}` · "
                       f"correlación `{h['correlationId']}`" + (f" · motivo: {h['reason']}" if h["reason"] else ""))
footer(api)
