"""Solicitante: crear borrador, enviar y ver el estado combinado (E2-FR-01; §6.8)."""

import streamlit as st

from web.ui import combined_status, footer, label, refresh_button, show_result

api = st.session_state.get("api")
st.title("📝 Mis solicitudes")
if api is None:
    st.stop()

with st.expander("➕ Nueva solicitud", expanded=False):
    locs = api.get("/locations")
    if show_result(locs):
        active = [loc for loc in locs.body if loc["active"]]
        with st.form("nueva"):
            loc = st.selectbox("Ubicación (directorio de espacios)", [x["locationId"] for x in active],
                               format_func=lambda i: next(f"{x['locationId']} — {x['displayName']}"
                                                          for x in active if x["locationId"] == i))
            desc = st.text_area("Descripción del problema", max_chars=2000)
            c1, c2, c3 = st.columns(3)
            urg = c1.selectbox("Urgencia sugerida", ["LOW", "MEDIUM", "HIGH", "CRITICAL"], index=1)
            cat = c2.selectbox("Categoría sugerida", ["", "PLUMBING", "ELECTRICAL", "HVAC", "GENERAL"])
            safety = c3.checkbox("Riesgo de seguridad (fuga, falla eléctrica crítica)")
            send_now = st.checkbox("Enviar de inmediato", value=True)
            if st.form_submit_button("Crear"):
                r = api.command("/requests", {"locationId": loc, "description": desc, "suggestedUrgency": urg,
                                              "suggestedCategory": cat or None, "safetyFlag": safety})
                created = show_result(r, f"Borrador {r.body.get('requestId') if r.ok else ''} creado.")
                if created and send_now:
                    s = api.command(f"/requests/{r.body['requestId']}/submissions", etag=r.headers.get("etag"))
                    show_result(s, f"Solicitud {r.body['requestId']} enviada.")

refresh_button("refresh_mine")
mine = api.get("/requests", view="mine")
if show_result(mine):
    if not mine.body:
        st.info("No tiene solicitudes.")
    for req in mine.body:
        with st.container(border=True):
            st.markdown(f"**{req['requestId']}** · {req['locationName']} · {label(req['status'])}")
            st.caption(req["description"])
            status = api.get(f"/requests/{req['requestId']}/status")
            if status.ok:
                combined_status(status.body)
            cols = st.columns(3)
            if req["status"] in ("DRAFT", "NEEDS_INFO"):
                info = cols[0].text_input("Información adicional", key=f"info-{req['requestId']}") \
                    if req["status"] == "NEEDS_INFO" else None
                if cols[1].button("Enviar", key=f"send-{req['requestId']}"):
                    r = api.command(f"/requests/{req['requestId']}/submissions",
                                    {"additionalInfo": info} if info else None, etag=req["etag"])
                    show_result(r, "Enviada.") and st.rerun()
            if req["status"] in ("DRAFT", "SUBMITTED") and cols[2].button("Retirar", key=f"wd-{req['requestId']}"):
                show_result(api.command(f"/requests/{req['requestId']}/withdrawals", etag=req["etag"]),
                            "Retirada.") and st.rerun()
footer(api)
