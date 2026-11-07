# Resumen de evidencia — DH-02 · Evaluación 3

- **Ejecutado:** 2026-11-07T23:23:09+00:00
- **Commit:** `088c99a8994eb42938f8469899a192bf069c3c63` (árbol con cambios sin confirmar: false)
- **Entorno:** Linux 7.0.0-42-generic x86_64 (contenedor Docker 'test') · Python 3.13.17 · PostgreSQL 17.10 (Debian 17.10-1.pgdg12+1)
- **Comando:** `make evidence → python -m tests.evidence_runner → pytest -v -rA --tb=short -p no:cacheprovider`
- **Salida cruda:** [`pytest-20261107T232008Z.log`](raw/pytest-20261107T232008Z.log) · código de salida de pytest: 0
- **Totales:** 18 PASSED · 0 FAILED · 11 NOT_EXECUTED

Un estado PASSED significa que todas las pruebas pytest vinculadas a ese ID se ejecutaron y pasaron en esta
corrida. Los FAILED se conservan. NOT_EXECUTED indica que no hay prueba implementada o que se omitió.

| ID | Prioridad | Procedimiento (§10.3) | Estado | Pruebas pytest | Registro |
|---|---|---|---|---|---|
| T-FUN-01 | P0 | Flujo completo con datos sintéticos | **PASSED** | 6/6 pasaron | [T-FUN-01.yaml](T-FUN-01.yaml) |
| T-FUN-02 | P1 | Bloquear/desbloquear con cada tipo; transiciones ilegales | **NOT_EXECUTED** | — | [T-FUN-02.yaml](T-FUN-02.yaml) |
| T-FUN-03 | P1 | Confirmar, reabrir y cerrar administrativamente | **NOT_EXECUTED** | — | [T-FUN-03.yaml](T-FUN-03.yaml) |
| T-FUN-04 | P1 | Redirigir, rechazar y pedir información | **NOT_EXECUTED** | — | [T-FUN-04.yaml](T-FUN-04.yaml) |
| T-FUN-05 | P1 | Búsqueda, filtros y reportes con alcances | **NOT_EXECUTED** | — | [T-FUN-05.yaml](T-FUN-05.yaml) |
| T-CON-01 | P0 | 100 carreras de dos aprobaciones con conexiones independientes | **PASSED** | 2/2 pasaron | [T-CON-01.yaml](T-CON-01.yaml) |
| T-CON-02 | P0 | Casos límite de unicidad | **PASSED** | 1/1 pasaron | [T-CON-02.yaml](T-CON-02.yaml) |
| T-TXN-01 | P0 | Fallo inyectado entre orden, auditoría, outbox, receipt y COMMIT | **PASSED** | 5/5 pasaron | [T-TXN-01.yaml](T-TXN-01.yaml) |
| T-API-01 | P0 | Reintento tras perder la respuesta | **PASSED** | 1/1 pasaron | [T-API-01.yaml](T-API-01.yaml) |
| T-API-02 | P0 | Clave reutilizada con contenido distinto | **PASSED** | 1/1 pasaron | [T-API-02.yaml](T-API-02.yaml) |
| T-API-03 | P0 | Versiones obsoletas | **PASSED** | 1/1 pasaron | [T-API-03.yaml](T-API-03.yaml) |
| T-AUD-01 | P0 | Historial con actores permitidos y prohibidos | **PASSED** | 1/1 pasaron | [T-AUD-01.yaml](T-AUD-01.yaml) |
| T-POL-01 | P1 | Tabla de urgencia y plazos por política | **PASSED** | 10/10 pasaron | [T-POL-01.yaml](T-POL-01.yaml) |
| T-POL-02 | P1 | Instalar POL-002 y comparar con POL-001 | **NOT_EXECUTED** | — | [T-POL-02.yaml](T-POL-02.yaml) |
| T-SLA-01 | P1 | Atrasos, pausa por MATERIALS/ACCESS | **NOT_EXECUTED** | — | [T-SLA-01.yaml](T-SLA-01.yaml) |
| T-SAF-01 | P1 | Bandera de seguridad | **NOT_EXECUTED** | — | [T-SAF-01.yaml](T-SAF-01.yaml) |
| T-INT-01 | P0 | Notificaciones caídas tras asignar | **PASSED** | 3/3 pasaron | [T-INT-01.yaml](T-INT-01.yaml) |
| T-INT-02 | P1 | Caída del worker tras envío aceptado | **PASSED** | 1/1 pasaron | [T-INT-02.yaml](T-INT-02.yaml) |
| T-CONTRACT-01 | P1 | Respuestas inválidas del directorio y de notificaciones | **PASSED** | 3/3 pasaron | [T-CONTRACT-01.yaml](T-CONTRACT-01.yaml) |
| T-SEC-01 | P0 | Matriz de lectura y mutación por rol, propiedad y alcance | **PASSED** | 1/1 pasaron | [T-SEC-01.yaml](T-SEC-01.yaml) |
| T-SEC-02 | P0 | Triaje/confirmación propia; rol inactivo | **PASSED** | 1/1 pasaron | [T-SEC-02.yaml](T-SEC-02.yaml) |
| T-SEC-03 | P0 | Propietario y estado falsificados | **PASSED** | 1/1 pasaron | [T-SEC-03.yaml](T-SEC-03.yaml) |
| T-SEC-04 | P1 | CSRF ausente, sesión expirada | **PASSED** | 1/1 pasaron | [T-SEC-04.yaml](T-SEC-04.yaml) |
| T-SEC-05 | — | Inyección y entradas sobredimensionadas | **NOT_EXECUTED** | — | [T-SEC-05.yaml](T-SEC-05.yaml) |
| T-SEC-06 | — | Secretos y datos en código, logs y mensajes | **NOT_EXECUTED** | — | [T-SEC-06.yaml](T-SEC-06.yaml) |
| T-PERF-01 | P2 | 100 envíos en 10 minutos | **NOT_EXECUTED** | — | [T-PERF-01.yaml](T-PERF-01.yaml) |
| T-ARCH-01 | P0 | Reglas de dependencias | **PASSED** | 5/5 pasaron | [T-ARCH-01.yaml](T-ARCH-01.yaml) |
| T-ENV-01 | P0 | Arranque desde cero con datos semilla | **PASSED** | 1/1 pasaron | [T-ENV-01.yaml](T-ENV-01.yaml) |
| T-REC-01 | P2 | Reiniciar API y worker conservando la BD | **NOT_EXECUTED** | — | [T-REC-01.yaml](T-REC-01.yaml) |
