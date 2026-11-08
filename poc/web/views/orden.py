"""Coordinador/Supervisor: asignar/reasignar, due_at, estado del SLA, cancelar y cierre administrativo."""

import streamlit as st

from web.ui import footer, label, refresh_button, show_result

api = st.session_state.get("api")
st.title("📋 Orden de trabajo")
if api is None:
    st.stop()
refresh_button("refresh_wo")
orders = api.get("/work-orders")
if not show_result(orders) or not orders.body:
    st.info("No hay órdenes visibles en su alcance.")
    footer(api)
    st.stop()
ids = [o["workOrderId"] for o in orders.body]
wid = st.selectbox("Orden", ids, format_func=lambda i: next(
    f"{o['workOrderId']} · {o['locationName']} · {o['category']} · {label(o['status'])}"
    for o in orders.body if o["workOrderId"] == i))
detail = api.get(f"/work-orders/{wid}")
if not show_result(detail):
    footer(api)
    st.stop()
wo = detail.body
a, b, c, d = st.columns(4)
a.metric("Estado", label(wo["status"]))
b.metric("Técnico", wo["assigneeId"] or "— sin asignar")
c.metric("Vence (due_at, UTC)", wo["dueAt"][:16].replace("T", " "))
d.metric("SLA", label(wo["slaState"]))
st.caption(f"Urgencia {wo['urgency']} · política {wo['policyVersion']} · solicitud {wo['requestId']} · "
           f"versión {wo['etag']} · sus relaciones: {', '.join(wo['yourRelations']) or '—'}")

cands = api.get(f"/work-orders/{wid}/assignment-candidates")
with st.form("asignar"):
    st.markdown("**Asignar / reasignar** (E2-AS-01: especialidad + disponibilidad, menor carga)")
    if cands.ok:
        st.dataframe(cands.body, hide_index=True, width="stretch")
    options = [""] + [c["subjectId"] for c in cands.body] if cands.ok else [""]
    who = st.selectbox("Técnico (vacío = el recomendado por la política)", options)
    reason = st.text_input("Motivo (obligatorio en reasignación o si se impone otro técnico)")
    if st.form_submit_button("Asignar"):
        show_result(api.command(f"/work-orders/{wid}/assignments", {"assigneeId": who or None,
                                                                    "reason": reason or None}, etag=wo["etag"]),
                    "Asignación registrada.")

col1, col2 = st.columns(2)
with col1.form("cancelar"):
    reason_c = st.text_input("Motivo de cancelación")
    if st.form_submit_button("Cancelar orden"):
        show_result(api.command(f"/work-orders/{wid}/cancellations", {"reason": reason_c}, etag=wo["etag"]),
                    "Orden cancelada.")
with col2.form("cierre"):
    reason_a = st.text_input("Motivo del cierre administrativo (supervisor)")
    if st.form_submit_button("Cierre administrativo"):
        show_result(api.command(f"/work-orders/{wid}/admin-closures", {"reason": reason_a}, etag=wo["etag"]),
                    "Cierre administrativo registrado.")

with st.expander("Bloqueos, materiales y observaciones"):
    st.write({k: wo.get(k) for k in ("blocks", "materials", "observations", "assignments", "confirmations")})
footer(api)
