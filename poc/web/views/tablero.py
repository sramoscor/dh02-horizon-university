"""Supervisor/Director: filtros de órdenes, carga de técnicos, atrasos y entregas (E2-FR-10, parcial)."""

import streamlit as st

from web.ui import footer, refresh_button, show_result

api = st.session_state.get("api")
st.title("📊 Tablero")
if api is None:
    st.stop()
refresh_button("refresh_dash")
a, b, c = st.columns(3)
status = a.multiselect("Estado", ["CREATED", "ASSIGNED", "IN_PROGRESS", "BLOCKED", "PENDING_CONFIRMATION", "REOPENED",
                                  "CLOSED", "ADMIN_CLOSED", "CANCELLED"])
category = b.multiselect("Categoría", ["PLUMBING", "ELECTRICAL", "HVAC", "GENERAL"])
location = c.text_input("Ubicación (ID)")
orders = api.get("/work-orders", status=",".join(status), category=",".join(category), locationId=location)
if show_result(orders):
    st.markdown(f"**Órdenes visibles:** {len(orders.body)} · **atrasadas:** "
                f"{sum(1 for o in orders.body if o['overdue'])}")
    st.dataframe([{k: o[k] for k in ("workOrderId", "locationName", "category", "status", "assigneeId", "urgency",
                                      "dueAt", "overdue", "policyVersion")} for o in orders.body],
                 hide_index=True, width="stretch")
st.subheader("Carga de técnicos")
load = api.get("/technicians/workload")
if show_result(load):
    st.dataframe(load.body, hide_index=True, width="stretch")
st.subheader("Entregas de notificaciones (outbox)")
deliveries = api.get("/deliveries")
if show_result(deliveries):
    st.dataframe(deliveries.body, hide_index=True, width="stretch")
footer(api)
