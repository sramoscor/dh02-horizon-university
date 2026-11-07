# Trazabilidad extendida con la implementación

Extiende la cadena de evidencia de §12.1 hasta el código y los registros de ejecución:

`requisito → escenario de calidad → driver → ADR → componente → archivo:función → prueba → registro de evidencia`

Las rutas son relativas a la raíz del repositorio. Los registros de `poc/evidence/` dicen en qué entorno se
ejecutaron (ver [SUMMARY.md](../../poc/evidence/SUMMARY.md)).

## Cadenas obligatorias

### 1 · Unicidad de la orden bajo concurrencia

| Eslabón | Elemento |
|---|---|
| Requisito | **E2-FR-03** La aprobación crea una única orden, de forma atómica (BR-02, BR-03) |
| Escenario | **QS-01** 100 ensayos: 1 éxito + 1 conflicto; 0 pares activos indebidos |
| Driver | **AD-01** Integridad del ciclo de vida |
| Decisión | **E2-ADR-003** Unicidad por solicitud y por (ubicación, categoría, discriminador) activas |
| Componente | `TriageApplicationService` (C-API) + índice único parcial (C-DB) |
| Código | `poc/backend/application/triage.py:TriageApplicationService.decide` · `poc/backend/infrastructure/sql.py:SqlWorkOrders.add` (SAVEPOINT y traducción a `DuplicateCandidateExists`) · `poc/migrations/versions/0001_initial_schema.py` (`uq_work_order_request`, `uq_work_order_active_slot`) · `poc/backend/http/problems.py` (409 problem-details) |
| Prueba | **T-CON-01** `poc/tests/integration/test_concurrency.py::test_two_coordinators_race_distinct_requests` y `::test_two_coordinators_race_same_request`; **T-CON-02** `::test_boundaries` |
| Evidencia | [`poc/evidence/T-CON-01.yaml`](../../poc/evidence/T-CON-01.yaml), [`T-CON-02.yaml`](../../poc/evidence/T-CON-02.yaml) |

### 2 · Notificaciones que fallan sin afectar la orden

| Eslabón | Elemento |
|---|---|
| Requisito | **E2-FR-08** Notificar sin que la falla afecte la orden (BR-09) |
| Escenario | **QS-02** X-NOTIFY caído 2 min tras asignar; orden `ASSIGNED`; entrega ≤ 60 s tras restaurar |
| Driver | **AD-04** Integraciones desacopladas |
| Decisión | **E2-ADR-004** Outbox transaccional |
| Componente | `C-WORKER` + tabla `outbox_event` (C-DB) + adaptador X-NOTIFY |
| Código | `poc/backend/application/commands.py:emit_event` (misma transacción) · `poc/backend/infrastructure/sql.py:SqlDeliveryStore.claim` (`FOR UPDATE SKIP LOCKED`, lease) y `mark_delivered/mark_retry/mark_dead` (exigen el token del lease) · `poc/backend/application/delivery.py:DeliveryService.run_once` · `poc/backend/infrastructure/adapters.py:HttpNotificationAdapter.send` · `poc/backend/bootstrap/worker.py:main` |
| Prueba | **T-INT-01** `poc/tests/integration/test_notifications.py::test_notifications_down_accelerated`, `::test_notifications_down_design_parameters`, `::test_permanent_error_goes_to_dead_letter_and_order_is_intact`; **T-INT-02** `::test_worker_crash_after_accepted_send` |
| Evidencia | [`poc/evidence/T-INT-01.yaml`](../../poc/evidence/T-INT-01.yaml), [`T-INT-02.yaml`](../../poc/evidence/T-INT-02.yaml) |

### 3 · Acceso por rol y relación

| Eslabón | Elemento |
|---|---|
| Requisito | **E2-FR-11** Autenticación simulada y control de acceso por rol y relación (BR-10, BR-13) |
| Escenario | **QS-04** 100 % de la matriz negativa denegada; 0 escrituras; con control positivo |
| Driver | **AD-05** Acceso por rol y relación |
| Decisión | **E2-ADR-006** Identidad no es autorización |
| Componente | `AuthorizationService` (C-API) |
| Código | `poc/backend/application/authorization.py:AuthorizationService.request_relations` y `.work_order_relations` (alcance, propiedad, separación de funciones) · `poc/backend/domain/transitions.py:plan_transition` (actor → estado → motivo) · `poc/backend/http/deps.py:current_session`, `csrf_protected` · `poc/backend/application/access.py:AccessService.resolve` (roles releídos en cada petición) |
| Prueba | **T-SEC-01** `poc/tests/integration/test_security.py::test_authorization_matrix`; **T-SEC-02** `::test_separation_of_duties_and_inactive_role`; **T-SEC-03** `::test_forged_owner_state_and_role_are_ignored`; **T-SEC-04** `::test_csrf_and_session` |
| Evidencia | [`poc/evidence/T-SEC-01.yaml`](../../poc/evidence/T-SEC-01.yaml) (y T-SEC-02/03/04) |

### 4 · Auditoría reconstruible

| Eslabón | Elemento |
|---|---|
| Requisito | **E2-FR-09** Registrar todo cambio de estado, asignación y decisión (BR-08) |
| Escenario | **QS-03** Autor, rol, momento, estado anterior/posterior, motivo y correlación; sin éxito falso tras reversión |
| Driver | **AD-02** Trazabilidad |
| Decisión | **E2-ADR-007** Transiciones explícitas y auditoría atómica |
| Componente | `AuditEvent` en C-DB + consulta de historial autorizada |
| Código | `poc/backend/application/commands.py:record_audit` · `poc/backend/infrastructure/sql.py:SqlAudit.append`, `.for_entities` · `poc/migrations/versions/0001_initial_schema.py` (`audit_event`, trigger `audit_event_append_only`) · `poc/backend/application/work_orders.py:WorkOrderService.history` |
| Prueba | **T-AUD-01** `poc/tests/integration/test_security.py::test_audit_trail`; **T-TXN-01** `poc/tests/integration/test_transactions_api.py::test_injected_failure_rolls_back_everything` |
| Evidencia | [`poc/evidence/T-AUD-01.yaml`](../../poc/evidence/T-AUD-01.yaml), [`T-TXN-01.yaml`](../../poc/evidence/T-TXN-01.yaml) |

## Matriz de requisitos con implementación

| Requisito | ADR | Código principal | Pruebas ejecutadas | Estado |
|---|---|---|---|---|
| E2-FR-01 Solicitud con ubicación validada | 006, 008 | `application/requests.py:RequestService`, `LocationService.ensure_valid` | T-FUN-01, T-SEC-03, T-CONTRACT-01 | Implementado |
| E2-FR-02 Triaje y clasificación | 001, 005, 007 | `application/triage.py`, `domain/policies.py:DuplicateCandidatePolicy` | T-FUN-01, T-CON-02, T-SEC-02 | Implementado (REJECT/REDIRECT/REQUEST_INFO sin prueba propia: T-FUN-04 pendiente) |
| E2-FR-03 Creación atómica de la orden | 003, 007 | `application/triage.py:decide`, `infrastructure/sql.py:SqlWorkOrders.add` | T-CON-01/02, T-TXN-01, T-API-01/02/03 | Implementado |
| E2-FR-04 Asignación y reasignación | 005, 007 | `application/work_orders.py:apply_assignment`, `domain/policies.py:AssignmentPolicy` | T-FUN-01 (incluye política) | Implementado; escalamiento no |
| E2-FR-05 Ejecución y bloqueos | 007 | `WorkOrderService.start/block/resolve_block/record_work/complete` | T-FUN-01 | Implementado (T-FUN-02 por tipo de bloqueo: pendiente) |
| E2-FR-06 Confirmación, reapertura, cierre | 006, 007 | `WorkOrderService.confirm/admin_close` | T-FUN-01, T-SEC-02 | Confirmar/reabrir implementado; cierre automático por plazo **diseñado, no implementado** |
| E2-FR-07 Atrasos y alertas | 004, 005 | `SqlWorkOrders.overdue_candidates`, tabla `overdue_alert` | — | **Diseñado, no implementado** (consulta y tabla existen, el job no) |
| E2-FR-08 Notificaciones | 004 | `DeliveryService`, `SqlDeliveryStore` | T-INT-01/02 | Implementado (envío simulado) |
| E2-FR-09 Auditoría | 007 | `record_audit`, `SqlAudit` | T-AUD-01, T-TXN-01 | Implementado |
| E2-FR-10 Tablero y búsqueda | — | `WorkOrderService.search`, `workload`; página Tablero | — | Parcial; reportes de recurrencia no implementados |
| E2-FR-11 Identidad y acceso | 006, 008 | `AccessService`, `AuthorizationService`, X-ID simulado | T-SEC-01..04, T-CONTRACT-01 | Implementado (identidad simulada) |

## Traza inversa de ejemplo (desde el código)

`poc/backend/infrastructure/sql.py:SqlWorkOrders.add` existe porque dos coordinadores pueden aprobar a la vez
dos solicitudes del mismo problema (QS-01). E2-ADR-003 decide que la garantía la dé la base de datos. Por eso
el método no consulta antes de insertar: inserta dentro de un SAVEPOINT y traduce `uq_work_order_active_slot`
a `DuplicateCandidateExists(existingWorkOrderId)`. Lo prueban T-CON-01 (100 ensayos con conexiones
independientes y contención real) y T-CON-02. La escena 2 del video lo muestra desde el Panel de demostración.
