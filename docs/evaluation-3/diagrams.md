# Vistas actualizadas — lo que realmente existe (Evaluación 3)

Solo se redibujan las vistas que cambiaron o que describen el código implementado. Los nombres son los mismos
de la Evaluación 2 (`C-WEB`, `C-API`, `C-WORKER`, `C-DB`, `X-ID`, `X-DIR`, `X-NOTIFY`). V-CTX (§5.1) no cambió.

## V-CONT — Contenedores (actualiza §5.2, Figura F02)

```mermaid
flowchart LR
    U["Usuarios en navegador"] -->|"HTTP + WebSocket de Streamlit"| W
    subgraph DH["Frontera de DH-02 (docker compose: proyecto dh02)"]
        W["C-WEB<br/>Streamlit 1.66.1 (servicio web :8501)<br/>solo cliente HTTP"]
        A["C-API<br/>FastAPI 0.143 (servicio api :8000)"]
        K["C-WORKER<br/>mismo código (servicio worker)"]
        DB[("C-DB<br/>PostgreSQL 17.10 (servicio db)<br/>negocio, auditoría, outbox")]
        W -->|"HTTP JSON /api/v1, cookie HttpOnly + X-CSRF-Token"| A
        A -->|"SQL transaccional (psycopg 3)"| DB
        K -->|"SQL: reclamo con lease, SKIP LOCKED"| DB
    end
    subgraph EXT["Sistemas externos simulados (servicio mocks :8090)"]
        ID["X-ID Identidad"]
        DIR["X-DIR Directorio"]
        NOT["X-NOTIFY Notificaciones"]
    end
    W -->|"aserción de inicio de sesión"| ID
    A -->|"verificar aserción"| ID
    K -->|"refresco de proyección"| DIR
    K -->|"entregas con Idempotency-Key"| NOT
    W -.->|"solo DEMO_MODE: /_control"| EXT
```

*Cambios respecto de F02: C-WEB es Streamlit (E2-ADR-009) y obtiene la aserción de X-ID en lugar del navegador.
C-API no llama a X-DIR en línea: valida contra la proyección local que mantiene C-WORKER. No hay proxy Nginx
ni TLS (D-10). La flecha punteada existe solo en el entorno de demostración.*

## V-COMP — Componentes del backend implementados (actualiza §5.3, Figura F03)

```mermaid
flowchart TD
    subgraph HTTP["backend/http"]
        R["routes.py<br/>contrato /api/v1"]
        D["deps.py<br/>sesión, CSRF, Idempotency-Key, If-Match"]
        P["problems.py<br/>problem-details §8.3"]
    end
    subgraph APP["backend/application"]
        AC["AccessService"]
        AZ["AuthorizationService"]
        RS["RequestService + LocationService"]
        TS["TriageApplicationService"]
        WS["WorkOrderService"]
        CR["CommandRunner<br/>receipt + transacción"]
        DS["DeliveryService<br/>DirectoryRefreshService"]
        PORTS["ports.py<br/>UnitOfWork, repositorios, Clock,<br/>IdentityPort, SpaceDirectoryPort, NotificationPort"]
    end
    subgraph DOM["backend/domain"]
        TR["transitions.py<br/>tablas §6.5 / §6.6"]
        PO["policies.py<br/>Urgency, Sla, Assignment, DuplicateCandidate"]
        MO["model.py · enums.py · errors.py"]
    end
    subgraph INF["backend/infrastructure"]
        SQL["sql.py<br/>SqlUnitOfWork, repositorios, SqlDeliveryStore"]
        AD["adapters.py<br/>X-ID, X-DIR, X-NOTIFY"]
        SE["seeding.py"]
    end
    subgraph BOOT["backend/bootstrap"]
        API["api.py · container.py"]
        WK["worker.py"]
        CLI["cli.py"]
    end
    R --> D --> AC
    R --> RS & TS & WS
    RS & TS & WS --> AZ
    RS & TS & WS --> CR
    TS --> PO
    WS --> PO
    AZ --> TR
    TS & WS & RS --> TR
    CR --> PORTS
    DS --> PORTS
    SQL -. implementa .-> PORTS
    AD -. implementa .-> PORTS
    API --> R
    API --> SQL & AD
    WK --> DS
    WK --> SQL & AD
```

*Las flechas son importaciones reales. import-linter verifica que `domain` no importe a nadie fuera de sí mismo,
que `application` no importe `infrastructure`, `http`, SQLAlchemy ni HTTPX, y que `http` e `infrastructure` no se
importen entre sí (T-ARCH-01).*

## V-SEQ-01/02 implementado — Triaje que crea la orden y su error principal

```mermaid
sequenceDiagram
    actor C as Coordinador (coord-03)
    participant W as C-WEB (Streamlit)
    participant A as C-API
    participant DB as C-DB
    C->>W: Aprobar SR-2043 (ELECTRICAL, HIGH)
    W->>A: POST /api/v1/requests/SR-2043/triage<br/>Cookie, X-CSRF-Token, If-Match "request-SR-2043-v3", Idempotency-Key
    A->>A: deps.py: sesión → Principal (roles desde ROLE_ASSIGNMENT); CSRF; ETag → versión 3
    A->>DB: BEGIN · pg_advisory_xact_lock(clave) · SELECT command_receipt
    A->>DB: SELECT … FROM maintenance_request FOR UPDATE
    A->>A: AuthorizationService + plan_transition("approve"): actor → estado → motivo
    A->>DB: UPDATE solicitud (APPROVED, v4) · INSERT triage_record
    A->>DB: SAVEPOINT · INSERT work_order (CREATED)
    alt índice uq_work_order_active_slot libre
        A->>DB: INSERT audit_event ×2 · INSERT outbox_event · INSERT command_receipt
        A->>DB: COMMIT
        A-->>W: 201 · ETag "work-order-WO-3001-v1" · Location
    else otra transacción ya tiene una orden activa en (ubicación, categoría, discriminador)
        DB-->>A: espera hasta que la otra confirme → unique_violation
        A->>DB: ROLLBACK TO SAVEPOINT · SELECT orden ganadora
        A->>DB: ROLLBACK (sin auditoría, outbox ni receipt)
        A-->>W: 409 DUPLICATE_CANDIDATE_EXISTS · existingWorkOrderId
    end
    W-->>C: mensaje legible + pie con "POST …/triage → 201|409"
```

*Diferencias con F13/F14: el receipt se busca **antes** de la precondición (§8.4) bajo un bloqueo por clave;
la traducción del error usa un SAVEPOINT para leer la orden ganadora en la misma transacción; la notificación
nunca se envía dentro de la transacción: la entrega la hace C-WORKER.*

## V-DEP — Despliegue real de la demostración (actualiza §5.5)

```mermaid
flowchart LR
    subgraph HOST["Una máquina con Docker Engine + Compose v2"]
        B["Navegador → localhost:8501"]
        subgraph NET["red de compose dh02_default"]
            W["web (C-WEB)"]
            A["api (C-API) :8000"]
            K["worker (C-WORKER)"]
            M["mocks (X-ID, X-DIR, X-NOTIFY) :8090"]
            MIG["migrate (una vez: Alembic + seed)"]
            DB[("db (C-DB)<br/>volumen dbdata")]
            T["test (perfil test)<br/>pytest → base dh02_test"]
        end
    end
    B --> W
    W --> A
    W --> M
    A --> DB
    A --> M
    K --> DB
    K --> M
    MIG --> DB
    T --> DB
    T --> M
```

*Un solo dominio de falla (R-011). Los servicios tienen health checks y `api`/`worker` esperan a que `migrate`
termine con éxito.*
