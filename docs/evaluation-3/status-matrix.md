# Matriz de estado: diseñado, implementado, verificado (actualiza §10.5)

Vocabulario: **Implementado** = el código existe y se ejecuta · **Simulado** = existe como simulador de un sistema
externo · **Diseñado, no implementado** = solo está en el documento · **Pendiente** = previsto para esta entrega
y no terminado. «Verificado» exige un registro de evidencia con estado PASSED en `poc/evidence/`.

Commits de referencia: rama `eval-3-increment` (los IDs exactos están en cada registro YAML como
`source_commit`).

> **Entorno de la evidencia:** corrida oficial con `make env-check` el 2026-11-07, commit `466210e`, en
> contenedores: Python 3.13.17 y PostgreSQL 17.10, simuladores en el servicio `mocks`. Resultado: 18 PASSED,
> 0 FAILED, 11 NOT_EXECUTED (151 pruebas pytest). Una corrida previa en el host (Python 3.14) no había
> detectado el defecto H-08; la primera corrida en Docker falló y su salida cruda se conserva en
> `poc/evidence/raw/`.

| Módulo | Diseño (Ev. 2) | Implementación (ruta) | Estado | Verificación |
|---|---|---|---|---|
| Solicitudes: borrador, envío, estado combinado | §6.5, §6.8, §8.1 | `poc/backend/application/requests.py` | **Implementado** | T-FUN-01, T-SEC-03 → PASSED |
| Ubicaciones: proyección versionada del directorio | E2-ADR-008, §8.5 | `requests.py:LocationService`, `infrastructure/sql.py:SqlLocations`, `delivery.py:DirectoryRefreshService` | **Implementado** (X-DIR **simulado**) | T-CONTRACT-01 → PASSED |
| Triaje atómico (5 acciones) | §5.3, E2-ADR-001/007 | `poc/backend/application/triage.py` | **Implementado** | T-FUN-01, T-TXN-01 → PASSED; T-FUN-04 (rechazar/redirigir/pedir info): **pendiente** |
| Unicidad bajo concurrencia | E2-ADR-003, QS-01 | `migrations/versions/0001_initial_schema.py`, `infrastructure/sql.py:SqlWorkOrders.add` | **Implementado** | T-CON-01 (100/100), T-CON-02 → PASSED |
| Duplicados asesores | E2-ADR-003 punto 3 | `domain/policies.py:DuplicateCandidatePolicy` | **Implementado** | Unitarias + T-FUN-01 |
| Tabla de transiciones + `row_version` | E2-ADR-007, §6.5–6.6 | `poc/backend/domain/transitions.py`, `domain/model.py` | **Implementado** | Unitarias (116), T-API-03 → PASSED |
| Idempotencia y precondiciones | §8.4 | `application/commands.py:CommandRunner`, `http/deps.py` | **Implementado** | T-API-01/02/03 → PASSED |
| Asignación / reasignación | E2-FR-04, E2-AS-01 | `application/work_orders.py:apply_assignment`, `domain/policies.py:AssignmentPolicy` | **Implementado** | T-FUN-01 (política) → PASSED |
| Técnico: inicio, bloqueo tipificado, materiales, observaciones, finalizar | E2-FR-05 | `application/work_orders.py:WorkOrderService` | **Implementado** | T-FUN-01 → PASSED; T-FUN-02 (cada tipo de bloqueo): **pendiente** |
| Pausa del SLA por MATERIALS/ACCESS | E2-AS-04 | `WorkOrderService.resolve_block`, `SlaPolicy.shift_for_pause` | **Implementado** | Unitaria + T-FUN-01; T-SLA-01: **diseñado, no implementado** |
| Confirmar / reabrir | E2-FR-06 | `WorkOrderService.confirm` | **Implementado** | T-FUN-01, T-SEC-02 → PASSED |
| Cierre administrativo manual (supervisor) | E2-AS-02 | `WorkOrderService.admin_close` | **Implementado** | Sin prueba propia (T-FUN-03 pendiente) |
| Cierre administrativo automático a 7 días hábiles | E2-AS-02 | `domain/calendar.py`, `SqlWorkOrders.pending_confirmation_before` | **Diseñado, no implementado** (falta el job del worker) | — |
| Detección de atrasos y alerta única por umbral | E2-FR-07, V-SEQ-04 | `SqlWorkOrders.overdue_candidates`, tabla `overdue_alert` | **Diseñado, no implementado** (falta el job) | T-SLA-01 NOT_EXECUTED |
| Bandera de seguridad | E2-AS-03, QS-08 | `UrgencyPolicy`, evento `SafetyFlagRaised` en el triaje | **Implementado en parte** (la reducción exclusiva del supervisor no existe) | Unitaria; T-SAF-01 NOT_EXECUTED |
| Políticas versionadas | E2-ADR-005 | `domain/policies.py`, `policy_version`, `POST /policies/{v}/installation` | **Implementado en parte** | T-POL-01 → PASSED; T-POL-02 NOT_EXECUTED |
| Outbox + worker (lease, reintentos, DEAD_LETTER) | E2-ADR-004, QS-02 | `application/delivery.py`, `infrastructure/sql.py:SqlDeliveryStore`, `bootstrap/worker.py` | **Implementado** (X-NOTIFY **simulado**) | T-INT-01 (acelerado y con parámetros del diseño), T-INT-02 → PASSED |
| Reproceso de entregas DEAD_LETTER | §8.1 `POST /deliveries/{id}/replays` | — | **Diseñado, no implementado** | — |
| Autorización por rol + alcance + propiedad + estado | E2-ADR-006, §9.1 | `application/authorization.py`, `domain/transitions.py` | **Implementado** | T-SEC-01/02/03/04 → PASSED |
| Identidad, sesión opaca y CSRF | §9.2 | `application/access.py`, `http/deps.py`, X-ID **simulado** | **Implementado** | T-SEC-04, T-CONTRACT-01 → PASSED |
| Auditoría reconstruible | E2-ADR-007, QS-03 | `commands.py:record_audit`, `SqlAudit`, trigger de solo anexado | **Implementado** | T-AUD-01, T-TXN-01 → PASSED |
| Búsqueda, filtros, carga de técnicos | E2-FR-10 | `WorkOrderService.search/workload`, página Tablero | **Implementado en parte** | T-FUN-05 NOT_EXECUTED |
| Reportes de recurrencia | E2-FR-10 | — | **Diseñado, no implementado** | — |
| C-WEB Streamlit + Panel de demostración | E2-ADR-009 | `poc/web/` | **Implementado** | T-ARCH-01 → PASSED; recorrido manual en navegador (sin registro YAML) |
| Reglas de dependencias | E2-ADR-002 | `poc/pyproject.toml [tool.importlinter]` | **Implementado** | T-ARCH-01 → PASSED (con control negativo) |
| Entorno reproducible | T-ENV-01 | `docker-compose.yml`, `Makefile`, `scripts/t_env_01.sh` | **Implementado** | T-ENV-01 → PASSED (make clean + make up desde cero, seed idempotente y humo extremo a extremo) |
| Proxy con TLS | V-DEP | — | **Pendiente** (D-10) | — |
| Inyección/entradas grandes, secretos en logs | T-SEC-05/06 | Consultas parametrizadas, límites de tamaño en BD | Implementado sin prueba | NOT_EXECUTED |
| Desempeño y recuperación | T-PERF-01, T-REC-01 | — | **Diseñado, no implementado** | NOT_EXECUTED |

## Riesgos abiertos y próximos pasos (Evaluación 4)

| Riesgo / pendiente | Acción propuesta |
|---|---|
| Jobs del worker para atrasos (T-SLA-01) y cierre automático (E2-AS-02) | Implementar con el puerto `Clock` y el calendario ya existentes; probar con `FakeClock` |
| T-FUN-02/03/04/05, T-POL-02, T-SAF-01 sin prueba | Completar P1 |
| La cookie de C-API vive en el servidor de Streamlit (E2-ADR-009) | Si C-WEB vuelve a ser un SPA, poner C-API en el mismo origen detrás de un proxy |
| Cuarentena de esquemas desconocidos del outbox | Implementar `schema_version` > 1 → estado retenido |
| R-014: un superusuario de BD puede alterar la auditoría | Fuera de alcance de la PoC; requiere controles independientes |
| H-01/H-03: reglas del documento contradictorias | Validarlas con el cliente (CL-006, BR-10) |
