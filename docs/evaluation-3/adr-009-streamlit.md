# E2-ADR-009 — C-WEB con Streamlit en la PoC

| Campo | Valor |
|---|---|
| Estado | Aceptada para la Evaluación 3 (PoC) |
| Autor | María Fernanda |
| Fecha | 2026-11-07 |
| Drivers | AD-05 (acceso por rol y relación), AD-06 (equipo pequeño, un semestre) |
| Reemplaza | La propuesta de tecnología de C-WEB en §5.2 (React 19.3 / TypeScript 5.9 / Vite 8.3) para esta entrega |

- **Contexto.** El documento de la Evaluación 2 propone React + TypeScript para C-WEB (§5.2). Para esta entrega el riesgo a demostrar está en el backend: unicidad bajo concurrencia, atomicidad entre mutación, auditoría y outbox, y autorización en el servidor (§10.1). Con un equipo de tres personas, mantener otra pila de cliente (Node 24, Vite, React Router, TanStack Query, Vitest) reduce el tiempo para probar esos riesgos. Además, el video necesita una interfaz que muestre en pantalla las respuestas reales de la API.
- **Decisión.** C-WEB se implementa con **Streamlit 1.66.1**, y solo como **cliente HTTP del contrato §8.1**:
  1. `poc/web/` no importa `backend/*` ni accede a la base de datos. Lo verifica el contrato `web-is-http-client` de import-linter (T-ARCH-01), que incluye un control negativo.
  2. La sesión vive en `st.session_state`: un `httpx.Client` guarda la cookie `dh02_session` (HttpOnly, SameSite=Strict) y el token CSRF que emite C-API. Los roles que se muestran son los que devuelve C-API (`ROLE_ASSIGNMENT`); el cliente no inventa ninguno.
  3. La navegación oculta las páginas que el rol no usa. Es solo usabilidad (§8.7): la autorización se aplica en C-API aunque se invoque sin la interfaz.
  4. Cada página muestra, en un pie colapsable, el endpoint real invocado y su código de respuesta.
  5. El Panel de demostración (`DEMO_MODE=true`) llama también al endpoint de control de los simuladores (`/_control`), nunca a la base.
- **Alternativas.**
  - React/TypeScript según §5.2: mejor interfaz y accesibilidad, pero más costo de construcción y mantenimiento en esta entrega.
  - Solo la API con Swagger: no sirve para un recorrido por roles en el video.
  - Plantillas servidas por FastAPI: acoplan C-WEB con C-API en el mismo proceso, contra la vista V-CONT.
- **Consecuencias.**
  - (+) Contenedor C-WEB separado y del mismo lenguaje; tiempo invertido en las pruebas de riesgo.
  - (+) Hace visible que la interfaz no decide nada: el pie muestra la llamada y su código.
  - (−) La cookie HttpOnly vive en el **proceso servidor de Streamlit**, no en el navegador. HttpOnly y CSRF protegen el canal C-WEB → C-API, pero no el navegador → Streamlit, que usa el WebSocket de Streamlit sin autenticación propia. Por eso T-SEC-04 verifica CSRF y sesión **contra C-API directamente**.
  - (−) La accesibilidad (§8.7) depende de los componentes de Streamlit y no se probó.
  - (−) Las sesiones de Streamlit están en memoria: al reiniciar el contenedor `web` hay que volver a iniciar sesión.
  - AD-05, E2-ADR-006 y T-ARCH-01 siguen válidos porque la frontera de autorización no se movió.
- **Verificación.** T-ARCH-01 (contrato `web-is-http-client`, control negativo e inspección de que `poc/web` no menciona `backend`, `sqlalchemy`, `psycopg` ni `DATABASE_URL`). Recorrido en navegador de las escenas del Panel de demostración (ver [`demo-runbook.md`](demo-runbook.md)).
- **Reconsiderar si.** El producto pasa a usuarios reales, se exige accesibilidad verificable o se necesita el control de sesión en el navegador (cookie de C-API con proxy en el mismo origen). En esos casos se vuelve a la propuesta de §5.2.

## Estado de E2-ADR-001 … 008 tras la implementación

| ADR | Estado | Qué pasó al implementarlo (consecuencias reales) | Evidencia |
|---|---|---|---|
| E2-ADR-001 Solicitud ≠ orden | **Aceptada** | `MaintenanceRequest` y `WorkOrder` son tablas y entidades distintas; la orden solo nace en `TriageApplicationService.decide`. Hizo falta un comando explícito para `SUBMITTED → UNDER_TRIAGE` (H-02) | T-FUN-01, T-CON-02 |
| E2-ADR-002 Monolito modular + worker | **Aceptada** | Un solo código base (`poc/backend`) para C-API y C-WORKER, con capas verificadas por import-linter. Se agregó una raíz de composición compartida (`bootstrap/container.py`) | T-ARCH-01, T-ENV-01 |
| E2-ADR-003 Integridad relacional | **Aceptada, refinada** | `UNIQUE(request_id)` y un índice único parcial con `discriminator NOT NULL DEFAULT ''`; la violación se traduce dentro de un SAVEPOINT para poder devolver `existingWorkOrderId`. En la variante «misma solicitud» el perdedor recibe 412, no 409 (H-06): el bloqueo `FOR UPDATE` lo detiene antes de llegar al índice | T-CON-01 (100/100), T-CON-02 |
| E2-ADR-004 Outbox transaccional | **Aceptada, modificada** | Una fila por destinatario (D-09). El lease es un token por fila; un worker con lease vencido no puede confirmar. La cuarentena de esquemas desconocidos sigue diseñada, no implementada | T-INT-01, T-INT-02, T-TXN-01 |
| E2-ADR-005 Políticas versionadas | **Aceptada (parcial)** | `PolicyVersion` con reglas validadas y hash; triaje y orden guardan la versión; la asignación y el SLA usan la versión de la orden. La instalación de POL-002 existe como endpoint, pero T-POL-02 no está implementada | T-POL-01 |
| E2-ADR-006 Identidad ≠ autorización | **Aceptada, refinada** | X-ID solo verifica la identidad; los roles salen de `ROLE_ASSIGNMENT` en cada petición, así que un rol desactivado deja de valer sin cerrar sesión. La separación de funciones en la confirmación se interpretó (H-03) | T-SEC-01..04 |
| E2-ADR-007 Transiciones explícitas + auditoría atómica | **Aceptada** | Tabla única en `domain/transitions.py`; el actor se valida antes que el estado para no revelar información; trigger de solo anexado en `audit_event` | T-AUD-01, T-TXN-01 |
| E2-ADR-008 Adaptadores y simulaciones | **Aceptada** | X-ID, X-DIR y X-NOTIFY como simuladores HTTP con modos de falla; los fixtures JSON se comparten con la semilla sin acoplar código | T-CONTRACT-01 |
| **E2-ADR-009** C-WEB con Streamlit | **Nueva** | Ver arriba | T-ARCH-01 |
