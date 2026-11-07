# DH-02 — Sistema de Gestión de Mantenimiento y Órdenes de Trabajo del Campus

Proyecto del curso Software Design II · Caso Horizon University (Oficina de Instalaciones y Operaciones del Campus).

**Equipo consultor:** María Fernanda Romero · Sebastián Brito · Samuel Ramos

## Estado actual (2026-11-07)

| Aspecto | Estado |
|---|---|
| Evaluación 2 — Arquitectura y Diseño | Documentada ([`docs/evaluation-2/`](docs/evaluation-2/)) |
| Evaluación 3 — Primer incremento ejecutable | Corte vertical P0 implementado en [`poc/`](poc/); documentación en [`docs/evaluation-3/`](docs/evaluation-3/) |
| Pruebas | Resultados reales en [`poc/evidence/SUMMARY.md`](poc/evidence/SUMMARY.md) (PASSED / FAILED / NOT_EXECUTED) |

## Estructura

| Carpeta | Contenido |
|---|---|
| [`docs/evaluation-2/`](docs/evaluation-2/) | Documento de arquitectura y diseño de la Evaluación 2 (fuente del diseño) |
| [`docs/evaluation-3/`](docs/evaluation-3/) | Divergencias, ADR-009, matriz de estado, trazabilidad, vistas actualizadas, guion de la demo |
| [`poc/`](poc/) | Código del incremento, pruebas y evidencia ([instrucciones](poc/README.md)) |

## Inicio rápido

```bash
cd poc
make up          # http://localhost:8501
make evidence    # ejecuta las pruebas y escribe poc/evidence/
```

## Tecnologías

Python 3.13 · FastAPI 0.143.0 · SQLAlchemy 2.1.4 · Alembic 1.20.0 · psycopg 3.3.6 · PostgreSQL 17 ·
**Streamlit 1.66.1** (C-WEB, ver E2-ADR-009; reemplaza React/TypeScript para esta entrega) · Docker Compose v2.
Las versiones exactas están en `poc/requirements.txt`, `poc/constraints.txt` y `poc/web/requirements.txt`.
