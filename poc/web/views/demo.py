"""Panel de demostración (solo DEMO_MODE=true): escenarios de riesgo con el resultado crudo de C-API.

Cada escena usa sesiones reales e independientes (X-ID simulado → C-API). Nada se decide aquí: el panel solo
dispara peticiones HTTP y muestra lo que respondió el servidor.
"""

import threading
import time

import streamlit as st

from web.client import ApiClient, mocks
from web.ui import combined_status, label, show_result

st.title("🎬 Panel de demostración")
st.caption("Solo entorno demo. Las escenas referencian el documento de la Evaluación 2.")
ACTIVE = "CREATED,ASSIGNED,IN_PROGRESS,BLOCKED,PENDING_CONFIRMATION,REOPENED"


def session(subject: str) -> ApiClient:
    """Sesión de demo reutilizable por sujeto (cada una con su propia cookie y CSRF)."""
    clients = st.session_state.setdefault("demo_clients", {})
    client = clients.get(subject)
    if client is None or not client.get("/auth/session").ok:
        client = ApiClient(subject)
        client.login()
        clients[subject] = client
    client.calls.clear()
    return client


def raw(title: str, result) -> None:
    st.markdown(f"**{title}** → `{result.status}`" + (f" `{result.code}`" if result.code else ""))
    st.json(result.body, expanded=False)


tab1, tab2, tab3, tab4, tab5 = st.tabs(["1 · Carrera de coordinadores", "2 · Notificaciones caídas",
                                        "3 · Intento no autorizado", "4 · Auditoría y estado", "5 · Reiniciar demo"])

with tab1:
    st.markdown("**QS-01 · V-SEQ-02 · E2-ADR-003** — `coord-03` y `coord-04`, dos sesiones y dos hilos, aprueban a la "
                "vez SR-2043 y SR-2044 (misma ubicación ADM-201 y categoría ELECTRICAL).")
    if st.button("▶️ Lanzar carrera", type="primary"):
        a, b, sup = session("coord-03"), session("coord-04"), session("sup-01")
        ea, eb = a.get("/requests/SR-2043"), b.get("/requests/SR-2044")
        if not (ea.ok and eb.ok) or ea.body["status"] != "UNDER_TRIAGE" or eb.body["status"] != "UNDER_TRIAGE":
            st.error("La pareja SR-2043/SR-2044 ya no está en UNDER_TRIAGE: use «Reiniciar demo».")
        else:
            body = {"action": "APPROVE_CREATE_WO", "category": "ELECTRICAL", "validatedUrgency": "HIGH",
                    "discriminator": ""}
            barrier, results = threading.Barrier(2), {}

            def run(name, client, rid, etag):
                barrier.wait()
                t0 = time.perf_counter()
                results[name] = (client.command(f"/requests/{rid}/triage", body, etag=etag),
                                 round((time.perf_counter() - t0) * 1000))

            threads = [threading.Thread(target=run, args=("coord-03 · SR-2043", a, "SR-2043", ea.body["etag"])),
                       threading.Thread(target=run, args=("coord-04 · SR-2044", b, "SR-2044", eb.body["etag"]))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            cols = st.columns(2)
            for col, (name, (res, ms)) in zip(cols, sorted(results.items()), strict=True):
                with col:
                    raw(f"{name} ({ms} ms)", res)
            active = sup.get("/work-orders", locationId="ADM-201", category="ELECTRICAL", status=ACTIVE)
            st.metric("Órdenes activas en ADM-201 / ELECTRICAL", len(active.body) if active.ok else "?")
            st.caption("Esperado: un 201 y un 409 DUPLICATE_CANDIDATE_EXISTS con existingWorkOrderId; 1 orden activa.")

with tab2:
    st.markdown("**QS-02 · V-SEQ-03 · E2-ADR-004** — X-NOTIFY se cae; la orden sigue `ASSIGNED` y la entrega pasa "
                "a `RETRY_WAIT`; al restaurar, `DELIVERED`.")
    c1, c2, c3 = st.columns(3)
    if c1.button("🔴 Caer X-NOTIFY"):
        mocks("POST", "/_control/xnotify", json={"mode": "down"})
    if c2.button("🟢 Restaurar X-NOTIFY"):
        mocks("POST", "/_control/xnotify", json={"mode": "up"})
    sup = session("sup-01")
    orders = sup.get("/work-orders", status=ACTIVE)
    ids = [o["workOrderId"] for o in orders.body] if orders.ok else []
    wid = c3.selectbox("Orden", ids or ["—"])
    with st.form("reasignar"):
        techs = sup.get("/technicians/workload")
        who = st.selectbox("Asignar/reasignar a (genera WorkOrderAssigned)",
                           [t["technicianId"] for t in techs.body] if techs.ok else [])
        if st.form_submit_button("Asignar como sup-01") and wid != "—":
            wo = sup.get(f"/work-orders/{wid}")
            show_result(sup.command(f"/work-orders/{wid}/assignments", {"assigneeId": who, "reason": "demo QS-02"},
                                    etag=wo.body["etag"]), "Asignación confirmada (la notificación va al outbox).")

    @st.fragment(run_every=2)
    def live_outbox():
        client = session("sup-01")
        ctl = mocks("GET", "/_control").json()
        mode = ctl["control"]["xnotify"]["mode"]
        st.markdown(f"X-NOTIFY: **{'🔴 caído' if mode == 'down' else '🟢 ' + mode}** · recibidos únicos "
                    f"{ctl['xnotify']['unique']} · duplicados {ctl['xnotify']['duplicates']} · intentos "
                    f"{ctl['xnotify']['attempts']}")
        if wid != "—":
            wo = client.get(f"/work-orders/{wid}")
            if wo.ok:
                st.metric(f"Estado de {wid}", label(wo.body["status"]), wo.body["etag"], delta_color="off")
        rows = client.get("/deliveries", aggregateId=None if wid == "—" else wid)
        if rows.ok:
            st.dataframe([{"evento": r["event_type"], "destinatario": r["recipient_ref"],
                           "estado": label(r["delivery_state"]), "intentos": r["attempt_count"],
                           "próximo intento": r["next_attempt_at"], "error": r["last_error"]}
                          for r in rows.body], hide_index=True, width="stretch")
        st.caption(f"Actualizado {time.strftime('%H:%M:%S')} · GET /api/v1/deliveries")

    live_outbox()

with tab3:
    st.markdown("**QS-04 · E2-ADR-006** — la API se invoca directamente; el servidor deniega y no escribe nada.")
    c1, c2 = st.columns(2)
    if c1.button("coord-03 triagea su propia solicitud SR-2045"):
        coord = session("coord-03")
        before = coord.get("/requests/SR-2045")
        r1 = coord.command("/requests/SR-2045/triage-reviews", etag=before.body["etag"])
        r2 = coord.command("/requests/SR-2045/triage", {"action": "APPROVE_CREATE_WO", "category": "HVAC",
                                                        "validatedUrgency": "LOW"}, etag=before.body["etag"])
        after = coord.get("/requests/SR-2045")
        raw("POST /requests/SR-2045/triage-reviews", r1)
        raw("POST /requests/SR-2045/triage", r2)
        st.metric("SR-2045 antes → después", f"{before.body['status']} v{before.body['rowVersion']} → "
                  f"{after.body['status']} v{after.body['rowVersion']}")
    if c2.button("TECH-021 lee la orden WO-3000 (asignada a TECH-014)"):
        tech, sup = session("TECH-021"), session("sup-01")
        hist_before = sup.get("/work-orders/WO-3000/history")
        r = tech.get("/work-orders/WO-3000")
        hist_after = sup.get("/work-orders/WO-3000/history")
        raw("GET /work-orders/WO-3000 (TECH-021)", r)
        st.metric("Eventos de auditoría de WO-3000 antes → después",
                  f"{len(hist_before.body)} → {len(hist_after.body)}")

with tab4:
    st.markdown("**QS-03 · E2-ADR-007 · §6.8** — línea de tiempo y estado combinado.")
    sup = session("sup-01")
    rid = st.text_input("Solicitud", "SR-2041")
    status = sup.get(f"/requests/{rid}/status")
    if show_result(status):
        combined_status(status.body)
        wo = (status.body.get("workOrder") or {}).get("workOrderId")
        if wo:
            hist = sup.get(f"/work-orders/{wo}/history")
            if hist.ok:
                st.dataframe([{"#": h["seq"], "acción": h["action"], "entidad": h["entityId"], "autor": h["actorId"],
                               "rol": h["actorRole"], "antes": (h["before"] or {}).get("status"),
                               "después": (h["after"] or {}).get("status"), "motivo": h["reason"],
                               "correlación": h["correlationId"], "momento": h["occurredAt"]}
                              for h in hist.body], hide_index=True, width="stretch")

with tab5:
    st.markdown("Vuelve al estado inicial (equivale a `make demo-reset`): base de datos y simuladores.")
    if st.button("♻️ Reiniciar demo", type="primary"):
        client = ApiClient("anon")
        r = client._send("POST", "/api/v1/demo/reset")
        mocks("POST", "/_control/reset")
        for key in ("api", "demo_clients"):
            st.session_state.pop(key, None)
        raw("POST /api/v1/demo/reset", r)
        st.success("Demo reiniciada. Vuelva a elegir usuario en la barra lateral.")
