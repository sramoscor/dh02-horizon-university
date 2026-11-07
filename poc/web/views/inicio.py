import streamlit as st

from web.ui import footer

api = st.session_state.get("api")
st.title("DH-02 · Mantenimiento y órdenes de trabajo del campus")
st.caption("Evaluación 3 · primer incremento ejecutable · datos sintéticos")
if api is None:
    st.info("Elija un usuario en la barra lateral para iniciar sesión (X-ID simulado).")
else:
    st.markdown(f"Sesión: **{api.me.get('displayName')}** (`{api.subject}`)")
    if not api.roles:
        st.warning("Este usuario no tiene roles activos: C-API denegará cualquier acción protegida.")
st.markdown(
    """
**Corte vertical (§10.1):** solicitud → triaje → orden → asignación → bloqueo → finalización → confirmación → cierre,
con auditoría y outbox sobre PostgreSQL.

| Contenedor | Qué es en esta demo |
|---|---|
| C-WEB | Esta interfaz Streamlit: **solo cliente HTTP** de C-API (E2-ADR-009) |
| C-API | FastAPI: reglas, autorización, transacciones |
| C-WORKER | Entregas del outbox con lease y reintentos; refresco del directorio |
| C-DB | PostgreSQL 17 |
| X-ID / X-DIR / X-NOTIFY | Simuladores con fallos controlables |

Cada página muestra al final las llamadas reales a C-API y su código de respuesta.
"""
)
footer(api)
