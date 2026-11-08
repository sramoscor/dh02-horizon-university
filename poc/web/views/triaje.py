"""Coordinador: cola de triaje, candidatos a duplicado y decisión (E2-FR-02/03; E2-ADR-003; §8.7)."""

import streamlit as st

from web.ui import footer, label, refresh_button, show_result

api = st.session_state.get("api")
st.title("🔎 Triaje")
if api is None:
    st.stop()
refresh_button("refresh_triage")
queue = api.get("/requests", view="triage")
if not show_result(queue):
    footer(api)
    st.stop()
if not queue.body:
    st.info("No hay solicitudes en su alcance.")
    footer(api)
    st.stop()

options = {r["requestId"]: r for r in queue.body}
rid = st.selectbox("Solicitud", list(options), format_func=lambda i: f"{i} · {options[i]['locationName']} · "
                   f"{label(options[i]['status'])}" + (" · (propia)" if options[i]["ownRequest"] else ""))
req = options[rid]
c1, c2 = st.columns([2, 1])
with c1:
    st.markdown(f"**{rid}** — {req['description']}")
    st.caption(f"Solicitante `{req['requesterId']}` · ubicación `{req['locationId']}` · versión `{req['etag']}`")
    if req["additionalInfo"]:
        st.info(f"Información adicional: {req['additionalInfo']}")
with c2:
    st.metric("Urgencia sugerida (solicitante)", req["suggestedUrgency"])
    st.metric("Categoría sugerida", req["suggestedCategory"] or "—")
    if req["safetyFlag"]:
        st.error("⚠️ Bandera de seguridad: la urgencia se fuerza al máximo (E2-AS-03)")

if req["status"] == "SUBMITTED":
    st.warning("La solicitud debe tomarse para triaje (SUBMITTED → UNDER_TRIAGE).")
    if st.button("Tomar para triaje"):
        show_result(api.command(f"/requests/{rid}/triage-reviews", etag=req["etag"]), "En triaje.") and st.rerun()
elif req["status"] == "UNDER_TRIAGE":
    category = st.selectbox("Categoría validada", ["PLUMBING", "ELECTRICAL", "HVAC", "GENERAL"],
                            index=["PLUMBING", "ELECTRICAL", "HVAC", "GENERAL"].index(req["suggestedCategory"])
                            if req["suggestedCategory"] else 0)
    cands = api.get(f"/requests/{rid}/duplicate-candidates", category=category)
    if cands.ok and cands.body:
        st.warning("Candidatos a duplicado (asesoría; la base de datos da la garantía final — E2-ADR-003):")
        st.dataframe(cands.body, hide_index=True, width="stretch")
    elif cands.ok:
        st.success("Sin candidatos a duplicado para esta ubicación y categoría.")
    with st.form("decision"):
        action = st.radio("Decisión", ["APPROVE_CREATE_WO", "LINK_TO_EXISTING", "REQUEST_INFO", "REJECT", "REDIRECT"],
                          horizontal=True)
        a, b, c = st.columns(3)
        urgency = a.selectbox("Urgencia decidida", ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                              index=["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(req["suggestedUrgency"]))
        safety = b.checkbox("Bandera de seguridad", value=req["safetyFlag"])
        discriminator = c.text_input("Discriminador (solo si es un problema distinto)", "")
        techs = api.get("/technicians/workload")
        tech_ids = [""] + [t["technicianId"] for t in techs.body] if techs.ok else [""]
        assignee = st.selectbox("Asignar a (opcional, en la misma transacción)", tech_ids)
        existing = st.selectbox("Orden existente (para vincular)",
                                [""] + [x["id"] for x in (cands.body if cands.ok else []) if x["kind"] == "WORK_ORDER"])
        reason = st.text_area("Motivo / comentarios (obligatorio para rechazar, redirigir o pedir información)")
        if st.form_submit_button("Registrar decisión"):
            body = {"action": action, "category": category, "validatedUrgency": urgency, "safetyFlag": safety,
                    "discriminator": discriminator, "assigneeId": assignee or None, "reason": reason or None,
                    "existingWorkOrderId": existing or None}
            r = api.command(f"/requests/{rid}/triage", body, etag=req["etag"])
            if show_result(r, f"Decisión registrada: {r.body.get('requestStatus') if r.ok else ''}"):
                if r.body.get("workOrder"):
                    wo = r.body["workOrder"]
                    st.success(f"Orden **{wo['workOrderId']}** {label(wo['status'])} · vence {wo['dueAt']}")
            elif r.code == "DUPLICATE_CANDIDATE_EXISTS":
                st.info("Elija «LINK_TO_EXISTING» con la orden indicada, o declare un discriminador si es distinto.")
else:
    st.info(f"Estado actual: {label(req['status'])}. El solicitante debe complementar la información.")
footer(api)
