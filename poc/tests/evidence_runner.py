"""`make evidence`: ejecuta la suite y escribe `poc/evidence/<test_id>.yaml` (§10.4) y `poc/evidence/SUMMARY.md`.

El estado de cada ID sale del resultado real de pytest. Los IDs del catálogo §10.3 sin prueba implementada se
registran como NOT_EXECUTED con la razón. Nada se marca PASSED sin ejecución.
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

POC = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("EVIDENCE_OUT", POC / "evidence"))

# Catálogo §10.3 (orden del documento) con la prioridad del prompt de la Evaluación 3.
CATALOG = [
    ("T-FUN-01", "P0", "Flujo completo con datos sintéticos"),
    ("T-FUN-02", "P1", "Bloquear/desbloquear con cada tipo; transiciones ilegales"),
    ("T-FUN-03", "P1", "Confirmar, reabrir y cerrar administrativamente"),
    ("T-FUN-04", "P1", "Redirigir, rechazar y pedir información"),
    ("T-FUN-05", "P1", "Búsqueda, filtros y reportes con alcances"),
    ("T-CON-01", "P0", "100 carreras de dos aprobaciones con conexiones independientes"),
    ("T-CON-02", "P0", "Casos límite de unicidad"),
    ("T-TXN-01", "P0", "Fallo inyectado entre orden, auditoría, outbox, receipt y COMMIT"),
    ("T-API-01", "P0", "Reintento tras perder la respuesta"),
    ("T-API-02", "P0", "Clave reutilizada con contenido distinto"),
    ("T-API-03", "P0", "Versiones obsoletas"),
    ("T-AUD-01", "P0", "Historial con actores permitidos y prohibidos"),
    ("T-POL-01", "P1", "Tabla de urgencia y plazos por política"),
    ("T-POL-02", "P1", "Instalar POL-002 y comparar con POL-001"),
    ("T-SLA-01", "P1", "Atrasos, pausa por MATERIALS/ACCESS"),
    ("T-SAF-01", "P1", "Bandera de seguridad"),
    ("T-INT-01", "P0", "Notificaciones caídas tras asignar"),
    ("T-INT-02", "P1", "Caída del worker tras envío aceptado"),
    ("T-CONTRACT-01", "P1", "Respuestas inválidas del directorio y de notificaciones"),
    ("T-SEC-01", "P0", "Matriz de lectura y mutación por rol, propiedad y alcance"),
    ("T-SEC-02", "P0", "Triaje/confirmación propia; rol inactivo"),
    ("T-SEC-03", "P0", "Propietario y estado falsificados"),
    ("T-SEC-04", "P1", "CSRF ausente, sesión expirada"),
    ("T-SEC-05", "—", "Inyección y entradas sobredimensionadas"),
    ("T-SEC-06", "—", "Secretos y datos en código, logs y mensajes"),
    ("T-PERF-01", "P2", "100 envíos en 10 minutos"),
    ("T-ARCH-01", "P0", "Reglas de dependencias"),
    ("T-ENV-01", "P0", "Arranque desde cero con datos semilla"),
    ("T-REC-01", "P2", "Reiniciar API y worker conservando la BD"),
]
NOT_IMPLEMENTED_REASON = "Prueba diseñada en §10.3 pero no implementada en este incremento (diseñado, no implementado)."


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
        return len(data)

    def flush(self):
        for s in self.streams:
            s.flush()

    def isatty(self):
        return False


def main(argv: list[str]) -> int:
    from tests import evidence_plugin

    OUT.mkdir(parents=True, exist_ok=True)
    for old in [*OUT.glob("T-*.yaml"), OUT / "SUMMARY.md"]:
        old.unlink(missing_ok=True)  # registros solo de esta corrida (la salida cruda previa se conserva)
    raw_dir = OUT / "raw"
    raw_dir.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = raw_dir / f"pytest-{stamp}.log"
    args = ["-v", "-rA", "--tb=short", "-p", "no:cacheprovider", *argv]
    from tests.evidence_plugin import IN_CONTAINER

    prefix = "make evidence → " if IN_CONTAINER else ""
    command = prefix + "python -m tests.evidence_runner → pytest " + " ".join(args)
    with log_path.open("w", encoding="utf-8") as log:
        original = sys.stdout
        sys.stdout = Tee(original, log)
        try:
            exit_code = pytest.main(args)
        finally:
            sys.stdout = original
    rel_log = str(log_path.relative_to(POC)) if log_path.is_relative_to(POC) else str(log_path)
    statuses = evidence_plugin.write_records(OUT, command, rel_log, log_path.read_text(encoding="utf-8"))
    merge_env_smoke(statuses)
    for test_id, _, _ in CATALOG:
        if test_id not in statuses:
            write_not_executed(test_id)
            statuses[test_id] = "NOT_EXECUTED"
    write_summary(statuses, command, rel_log, exit_code)
    print(f"\nEvidencia escrita en {OUT} (pytest exit code {exit_code})")
    return int(exit_code)


def merge_env_smoke(statuses: dict[str, str]) -> None:
    """T-ENV-01: incorpora el resultado del arranque desde cero (scripts/t_env_01.sh), si se ejecutó."""
    result, log = os.environ.get("ENV_SMOKE_RESULT"), os.environ.get("ENV_SMOKE_LOG")
    path = OUT / "T-ENV-01.yaml"
    if not path.exists():
        if result:  # pytest no llegó a ejecutar la parte de seed: el registro refleja solo el arranque
            write_not_executed("T-ENV-01")
            record = yaml.safe_load(path.read_text(encoding="utf-8"))
            record.update(status="FAILED" if result != "PASSED" else "PASSED",
                          executed_at=datetime.now(UTC).isoformat(timespec="seconds"))
            record["assertions"]["observed"] = {"arranque_desde_cero": {"resultado": result, "log": log}}
            record["limitations"] = ["La parte pytest (seed/demo-reset) no se ejecutó en esta corrida."]
            path.write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
            statuses["T-ENV-01"] = record["status"]
        return
    record = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not result:
        # Sin el arranque desde cero, T-ENV-01 no está verificada aunque seed/demo-reset hayan pasado.
        record["status"] = statuses["T-ENV-01"] = "NOT_EXECUTED"
        record["limitations"].insert(0, "No se ejecutó el arranque desde cero (scripts/t_env_01.sh); solo la parte "
                                        "de seed/demo-reset, cuyo resultado se conserva en assertions.observed.")
        path.write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
        return
    record["assertions"]["observed"]["arranque_desde_cero"] = {"resultado": result, "log": log}
    record["execution"]["command"] = "scripts/t_env_01.sh (make clean && make up && humo) + " + \
        record["execution"]["command"]
    if result != "PASSED":
        record["status"] = "FAILED"
        statuses["T-ENV-01"] = "FAILED"
    path.write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")


def write_not_executed(test_id: str) -> None:
    from tests.evidence_plugin import _meta

    reason = ("Prueba implementada pero no ejecutada en esta corrida (deseleccionada, p. ej. -m 'not slow')."
              if test_id in _meta else NOT_IMPLEMENTED_REASON)
    record = {
        "test_id": test_id, "status": "NOT_EXECUTED", "source_commit": os.environ.get("SOURCE_COMMIT"),
        "code_refs": None, "executed_at": None, "executor": None,
        "environment": None, "fixture": None, "execution": None,
        "assertions": {"expected": next(p for i, _, p in CATALOG if i == test_id), "observed": None},
        "limitations": [reason], "related": {},
    }
    (OUT / f"{test_id}.yaml").write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False),
                                         encoding="utf-8")


def write_summary(statuses: dict[str, str], command: str, log: str, exit_code: int) -> None:
    from tests.evidence_plugin import collected_records, environment

    records = collected_records()
    env = environment()
    counts = {s: sum(1 for v in statuses.values() if v == s) for s in ("PASSED", "FAILED", "NOT_EXECUTED")}
    lines = [
        "# Resumen de evidencia — DH-02 · Evaluación 3",
        "",
        f"- **Ejecutado:** {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- **Commit:** `{os.environ.get('SOURCE_COMMIT', 'unknown')}` (árbol con cambios sin confirmar: "
        f"{os.environ.get('SOURCE_DIRTY', 'unknown')})",
        f"- **Entorno:** {env['os']} · {env['runtime']} · {env['database']}",
        f"- **Comando:** `{command}`",
        f"- **Salida cruda:** [`{Path(log).name}`](raw/{Path(log).name}) · código de salida de pytest: {exit_code}",
        f"- **Totales:** {counts['PASSED']} PASSED · {counts['FAILED']} FAILED · {counts['NOT_EXECUTED']} NOT_EXECUTED",
        "",
        "Un estado PASSED significa que todas las pruebas pytest vinculadas a ese ID se ejecutaron y pasaron en esta",
        "corrida. Los FAILED se conservan. NOT_EXECUTED indica que no hay prueba implementada o que se omitió.",
        "",
        "| ID | Prioridad | Procedimiento (§10.3) | Estado | Pruebas pytest | Registro |",
        "|---|---|---|---|---|---|",
    ]
    for test_id, prio, procedure in CATALOG:
        res = records.get(test_id, [])
        passed = sum(1 for r in res if r["outcome"] == "passed")
        cell = f"{passed}/{len(res)} pasaron" if res else "—"
        lines.append(f"| {test_id} | {prio} | {procedure} | **{statuses[test_id]}** | {cell} | "
                     f"[{test_id}.yaml]({test_id}.yaml) |")
    extra = sorted(set(statuses) - {c[0] for c in CATALOG})
    for test_id in extra:
        lines.append(f"| {test_id} | — | (fuera del catálogo) | **{statuses[test_id]}** | | "
                     f"[{test_id}.yaml]({test_id}.yaml) |")
    (OUT / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
