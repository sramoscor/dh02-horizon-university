# DH-02 · Prueba de concepto — Evaluación 3 («Arquitectura que ya funciona»)

Primer incremento ejecutable del Sistema de Gestión de Mantenimiento y Órdenes de Trabajo del Campus
(Horizon University). Equipo: María Fernanda Romero · Sebastián Brito · Samuel Ramos.

**Caso de uso implementado (§10.1, Figura F19):** solicitud → triaje → orden → asignación → bloqueo →
finalización → confirmación → cierre, con auditoría y outbox sobre PostgreSQL real, más los tres ataques de la
PoC: carrera de dos coordinadores, caída de notificaciones e intento no autorizado.

## Estado real

| Pieza | Estado |
|---|---|
| C-API (FastAPI): solicitudes, triaje atómico, órdenes, asignación, bloqueos, confirmación, auditoría, idempotencia, `If-Match`, CSRF, autorización | **Implementado** |
| C-WORKER: outbox con lease, reintentos, `DEAD_LETTER`, refresco del directorio | **Implementado** |
| C-WEB en Streamlit (cliente HTTP) y Panel de demostración | **Implementado** (E2-ADR-009) |
| X-ID, X-DIR, X-NOTIFY | **Simulados** con fallos controlables |
| Políticas versionadas (POL-001 vigente, POL-002 instalable) | Implementado en parte: T-POL-02 sin prueba |
| Detección de atrasos, cierre administrativo automático a 7 días hábiles, escalamiento, reportes de recurrencia, reproceso de `DEAD_LETTER` | **Diseñado, no implementado** |
| Proxy Nginx con TLS (V-DEP) | **Pendiente** (D-10) |

Detalle por módulo: [`docs/evaluation-3/status-matrix.md`](../docs/evaluation-3/status-matrix.md). Diferencias
con el diseño: [`docs/evaluation-3/divergences.md`](../docs/evaluation-3/divergences.md).

## Requisitos previos

- Docker Engine con Compose v2 (`docker compose version`), con permiso para usar el socket (grupo `docker`).
- GNU Make y Git.
- Puertos libres: **8501** (C-WEB), **8000** (C-API), **8090** (simuladores).

No hace falta Python ni Node en la máquina: todo corre en contenedores (`python:3.13.17-slim-bookworm`,
`postgres:17.10-bookworm`).

## Instalar e iniciar

```bash
git clone https://github.com/sramoscor/dh02-horizon-university.git
cd dh02-horizon-university/poc
make up
```

`make up` copia `.env.example` a `.env` si no existe, construye las imágenes, levanta `db`, aplica las
migraciones de Alembic y siembra los datos (servicio `migrate`), y espera los health checks de `api`, `worker`,
`mocks` y `web`.

| URL | Servicio |
|---|---|
| <http://localhost:8501> | C-WEB (Streamlit) |
| <http://localhost:8000/api/docs> | C-API (OpenAPI) |
| <http://localhost:8000/health> | Salud de C-API |
| <http://localhost:8090/_control> | Estado y control de los simuladores |

Otros comandos:

| Comando | Qué hace |
|---|---|
| `make seed` | Siembra de nuevo (idempotente) |
| `make demo-reset` | Vuelve al estado inicial de la demo (BD y simuladores) |
| `make down` / `make clean` | Detiene / borra contenedores **y volúmenes** |
| `make logs` / `make ps` | Logs y estado |

## Usuarios de prueba (sintéticos)

En C-WEB, barra lateral **«Iniciar sesión como»**. Los roles no los envía el cliente: C-API los resuelve en
`ROLE_ASSIGNMENT`.

| Usuario | Rol y alcance |
|---|---|
| `usr-017`, `usr-052` | Solicitante (propias) |
| `coord-03` | Coordinadora, campus NORTE; también solicitante (SR-2045 es suya) |
| `coord-04` | Coordinador, campus NORTE y SUR |
| `TECH-014` / `TECH-021` | Técnico de plomería / electricidad |
| `rep-CIE-01` | Representante del edificio CIE |
| `sup-01` | Supervisor (global) |
| `ops-01` | Operaciones de TI |
| `dir-01` | Director |
| `coord-99` | Coordinador con **rol inactivo** (T-SEC-02) |

## Recorrido de la demo

Paso a paso en [`docs/evaluation-3/demo-runbook.md`](../docs/evaluation-3/demo-runbook.md). En resumen:
`usr-017` crea y envía SR-2041 → `coord-03` la toma, ve el duplicado SR-2042 y aprueba con TECH-014 (WO-3001)
→ `TECH-014` inicia, bloquea por MATERIALS, resuelve, registra y finaliza → `rep-CIE-01` confirma. El Panel de
demostración ejecuta la carrera, la caída de X-NOTIFY y los intentos no autorizados.

## Ejecutar las pruebas

```bash
make test        # suite completa contra PostgreSQL real (base dh02_test) y simuladores
make test-fast   # sin las pruebas lentas (carreras de 100 ensayos, caída de 2 min)
make lint        # ruff
make arch        # import-linter (T-ARCH-01)
make evidence    # suite + poc/evidence/<test_id>.yaml + poc/evidence/SUMMARY.md
make env-check   # T-ENV-01: make clean, make up, seed, humo extremo a extremo y evidencia
```

**Resultados esperados:** todas las pruebas pasan; `SUMMARY.md` lista cada ID del catálogo §10.3 como PASSED,
FAILED o NOT_EXECUTED (con la razón). La suite completa tarda unos 4 minutos, porque T-INT-01 con los
parámetros del diseño mantiene X-NOTIFY caído 2 minutos.

| Carpeta | Contenido |
|---|---|
| `tests/unit` | Tabla de transiciones, políticas, calendario, autorización |
| `tests/integration` | API real + PostgreSQL real: flujo, concurrencia, transacciones, idempotencia, seguridad, auditoría, notificaciones |
| `tests/contract` | Adaptadores contra los simuladores (T-CONTRACT-01) |
| `tests/architecture` | Reglas de dependencias con control negativo (T-ARCH-01) |
| `evidence/` | Registros §10.4, `SUMMARY.md` y salida cruda en `evidence/raw/` |

## Parámetros del worker

Por defecto, los valores del diseño (QS-02): sondeo 2 s, timeout 5 s, esperas 5/10/20/30 s, 12 intentos,
lease 15 s (`.env.example`). Para tomas de video más cortas: `WORKER_BACKOFF_SECONDS=2,4,8,10` y `make up`.
T-INT-01 se ejecuta dos veces: una variante acelerada (caída de 20 s) y otra con los valores del diseño
(caída de 120 s).

## Estructura

```text
poc/
├── backend/{domain,application,infrastructure,http,bootstrap}   C-API y C-WORKER (§5.4)
├── migrations/            Alembic (índice único parcial, auditoría solo-anexado)
├── mocks/                 X-ID, X-DIR, X-NOTIFY
├── web/                   C-WEB (Streamlit)
├── fixtures/              datos sintéticos compartidos por la semilla y los simuladores
├── tests/                 unit, integration, contract, architecture + runner de evidencia
├── evidence/              registros de ejecución
├── scripts/t_env_01.sh    arranque desde cero (T-ENV-01)
├── docker-compose.yml · Dockerfile · Makefile · requirements.txt · constraints.txt
```

## Limitaciones conocidas

- Identidad, directorio y notificaciones son **simulados**: no prueban el comportamiento de proveedores reales.
- No hay TLS ni proxy; la cookie de sesión se emite sin `Secure` en la demo (D-10).
- Con Streamlit, la cookie de C-API vive en el servidor de C-WEB, no en el navegador (E2-ADR-009).
- Un solo despliegue (R-011): no es alta disponibilidad.
- La auditoría no resiste a un superusuario de la base de datos (R-014); `demo-reset` usa `TRUNCATE`.
- Las secuencias de PostgreSQL no son transaccionales: una aprobación revertida consume un número `WO-` (H-07).
- Funcionalidad diseñada y no implementada: ver «Estado real».
