# Prueba de concepto (PoC)

> **Estado:** todavía no implementada. Esta carpeta solo contiene la estructura prevista.

El corte vertical a construir (§10.1 del documento): solicitud → triaje → orden → asignación → bloqueo → finalización → confirmación → cierre, con auditoría y outbox, sobre PostgreSQL real.

| Carpeta | Propósito |
|---|---|
| `web/` | C-WEB: interfaz en React + TypeScript |
| `backend/http/` | Capa HTTP: validación y mapeo de respuestas |
| `backend/application/` | Casos de uso y puertos |
| `backend/domain/` | Entidades, reglas de negocio y tabla de transiciones |
| `backend/infrastructure/` | PostgreSQL y adaptadores de identidad, directorio y notificaciones |
| `backend/bootstrap/` | Raíces de composición del API y del worker |
| `migrations/` | Migraciones Alembic (índices únicos parciales, restricciones) |
| `mocks/` | Simuladores X-ID, X-DIR y X-NOTIFY con fallos controlables |
| `tests/unit/` | Pruebas de dominio y políticas |
| `tests/integration/` | Pruebas con base de datos real (T-CON, T-TXN, T-INT) |
| `tests/contract/` | Pruebas de contrato de adaptadores (T-CONTRACT) |
| `tests/architecture/` | Reglas de dependencias (T-ARCH-01) |
| `evidence/` | Registros de evidencia de pruebas ejecutadas (§10.4) |

Regla de dependencias: `domain/` no importa HTTP, SQL ni SDK de proveedores.
