"""Técnico: órdenes asignadas; iniciar, bloquear, resolver, materiales, observaciones, finalizar (E2-FR-05)."""

import streamlit as st

from web.ui import footer, label, refresh_button, show_result

api = st.session_state.get("api")
st.title("🔧 Mi trabajo")
if api is None:
    st.stop()
refresh_button("refresh_tech")
mine = api.get("/work-orders", assigneeId=api.subject)
if not show_result(mine) or not mine.body:
    st.info("No tiene órdenes asignadas.")
    footer(api)
    st.stop()

for o in mine.body:
    wid = o["workOrderId"]
    detail = api.get(f"/work-orders/{wid}")
    if not detail.ok:
        continue
    wo = detail.body
    with st.container(border=True):
        st.markdown(f"### {wid} · {wo['location']['displayName']} · {label(wo['status'])}")
        st.caption(f"{wo['category']} · urgencia {wo['urgency']} · vence {wo['dueAt'][:16]} UTC · "
                   f"SLA {label(wo['slaState'])} · {wo['etag']}")
        cols = st.columns(4)
        if wo["status"] == "ASSIGNED" and cols[0].button("Iniciar", key=f"start-{wid}"):
            show_result(api.command(f"/work-orders/{wid}/start", etag=wo["etag"]), "Iniciada.") and st.rerun()
        if wo["status"] == "IN_PROGRESS":
            with cols[1].popover("Bloquear"):
                btype = st.selectbox("Motivo tipificado", ["MATERIALS", "ACCESS", "CONTRACTOR", "APPROVAL"],
                                     key=f"bt-{wid}")
                bdetail = st.text_input("Detalle", key=f"bd-{wid}")
                if st.button("Confirmar bloqueo", key=f"block-{wid}"):
                    show_result(api.command(f"/work-orders/{wid}/blocks", {"blockType": btype, "detail": bdetail},
                                            etag=wo["etag"]), "Bloqueada.") and st.rerun()
            if cols[3].button("Marcar finalizada", key=f"done-{wid}"):
                show_result(api.command(f"/work-orders/{wid}/completions", etag=wo["etag"]),
                            "Pendiente de confirmación.") and st.rerun()
        open_block = next((b for b in wo.get("blocks", []) if b["endedAt"] is None), None)
        if wo["status"] == "BLOCKED" and open_block:
            st.warning(f"Bloqueo abierto: {open_block['blockType']} — {open_block['detail']}")
            if cols[2].button("Resolver bloqueo", key=f"res-{wid}"):
                r = api.command(f"/work-orders/{wid}/blocks/{open_block['blockId']}/resolution", etag=wo["etag"])
                if show_result(r, "Bloqueo resuelto."):
                    if r.body.get("slaShift"):
                        st.info(f"SLA pausado: due_at desplazado a {r.body['slaShift']['dueAt']}")
                    st.rerun()
        if wo["status"] in ("ASSIGNED", "IN_PROGRESS", "BLOCKED"):
            m, n = st.columns(2)
            with m.form(f"mat-{wid}"):
                name = st.text_input("Material")
                qty = st.number_input("Cantidad", min_value=1, value=1, step=1)
                if st.form_submit_button("Registrar material"):
                    show_result(api.command(f"/work-orders/{wid}/materials",
                                            {"materialName": name, "quantity": int(qty)}), "Material registrado.")
            with n.form(f"obs-{wid}"):
                note = st.text_area("Observación")
                ref = st.text_input("Referencia de evidencia (simulada)", "sim://foto-001")
                if st.form_submit_button("Registrar observación"):
                    show_result(api.command(f"/work-orders/{wid}/observations",
                                            {"note": note, "attachmentRef": ref}), "Observación registrada.")
footer(api)
