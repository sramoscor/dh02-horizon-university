"""C-WEB (Streamlit) — navegación por rol. El servidor decide; ocultar una página es solo usabilidad (§8.7)."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # permite `import web.*` con `streamlit run web/app.py`

from web.client import DEMO_MODE, ApiClient, list_users  # noqa: E402
from web.ui import show_result  # noqa: E402

st.set_page_config(page_title="DH-02 · Mantenimiento del campus", page_icon="🛠️", layout="wide")

PAGES = {
    "mis_solicitudes": ("views/mis_solicitudes.py", "Mis solicitudes", "📝", {"REQUESTER"}),
    "triaje": ("views/triaje.py", "Triaje", "🔎", {"COORDINATOR", "SUPERVISOR"}),
    "orden": ("views/orden.py", "Orden de trabajo", "📋", {"COORDINATOR", "SUPERVISOR"}),
    "mi_trabajo": ("views/mi_trabajo.py", "Mi trabajo", "🔧", {"TECHNICIAN"}),
    "confirmaciones": ("views/confirmaciones.py", "Confirmaciones", "✅", {"REQUESTER", "BUILDING_REP"}),
    "historial": ("views/historial.py", "Historial", "🕓", {"COORDINATOR", "SUPERVISOR", "DIRECTOR", "TECHNICIAN"}),
    "tablero": ("views/tablero.py", "Tablero", "📊", {"SUPERVISOR", "DIRECTOR", "OPERATIONS", "COORDINATOR"}),
}


def sidebar_login() -> ApiClient | None:
    st.sidebar.markdown("### Iniciar sesión como")
    st.sidebar.caption("X-ID simulado: elige un usuario sintético. Los roles los resuelve C-API (ROLE_ASSIGNMENT).")
    try:
        users = list_users()
    except Exception as exc:  # noqa: BLE001
        st.sidebar.error(f"X-ID no disponible: {type(exc).__name__}")
        return st.session_state.get("api")
    options = {u["subject"]: f"{u['subject']} — {u['displayName']}" for u in users}
    current = st.session_state.get("api")
    subjects = list(options)
    index = subjects.index(current.subject) if current and current.subject in subjects else 0
    choice = st.sidebar.selectbox("Usuario", subjects, index=index, format_func=options.get, key="login_choice")
    if current is None or current.subject != choice:
        if current is not None:
            current.logout()
        api = ApiClient(choice)
        result = api.login()
        if not show_result(result):
            return None
        st.session_state["api"] = api
        current = api
    roles = [f"{r['role']}" + (f" · {r['scopeType']}:{r['scopeId']}" if r["scopeId"] else f" · {r['scopeType']}")
             for r in current.me.get("roles", [])]
    st.sidebar.markdown("**Roles activos (servidor):**  \n" + ("  \n".join(roles) if roles else "— ninguno —"))
    if st.sidebar.button("Cerrar sesión"):
        current.logout()
        st.session_state.pop("api", None)
        st.rerun()
    return current


api = sidebar_login()
roles = api.roles if api else set()
if api is not None:
    api.calls.clear()  # el pie de cada página muestra solo las llamadas de esta vista
pages = [st.Page("views/inicio.py", title="Inicio", icon="🏠", default=True)]
pages += [st.Page(path, title=title, icon=icon) for path, title, icon, allowed in PAGES.values() if roles & allowed]
if DEMO_MODE:
    pages.append(st.Page("views/demo.py", title="Panel de demostración", icon="🎬"))
st.navigation(pages).run()
