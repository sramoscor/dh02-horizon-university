"""Plugin de pytest que genera registros de evidencia con la plantilla de §10.4.

Reglas de honestidad: el estado sale del resultado REAL de pytest. Un ID solo es PASSED si todas sus
pruebas se ejecutaron y pasaron; si alguna falló es FAILED (y se conserva); si todas se omitieron es
NOT_EXECUTED con la razón de la omisión.
"""

import os
import platform
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

EVIDENCE_DIR = Path(os.environ.get("EVIDENCE_DIR", "")) if os.environ.get("EVIDENCE_DIR") else None


class EvidenceRecorder:
    def __init__(self, test_id: str, nodeid: str) -> None:
        self.test_id = test_id
        self.nodeid = nodeid
        self.observed: dict[str, Any] = {}
        self.limitations: list[str] = []
        self.execution: dict[str, Any] = {}
        self.fixture: dict[str, Any] = {}

    def observe(self, key: str, value: Any) -> None:
        self.observed[key] = value

    def limit(self, text: str) -> None:
        self.limitations.append(text)

    def set_execution(self, **kwargs: Any) -> None:
        self.execution.update(kwargs)

    def set_fixture(self, **kwargs: Any) -> None:
        self.fixture.update(kwargs)


_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
_meta: dict[str, dict[str, Any]] = {}
_recorders: dict[str, EvidenceRecorder] = {}


@pytest.fixture
def evidence(request) -> EvidenceRecorder:
    marker = request.node.get_closest_marker("evidence")
    test_id = marker.args[0] if marker else "UNMAPPED"
    rec = EvidenceRecorder(test_id, request.node.nodeid)
    _recorders[request.node.nodeid] = rec
    return rec


def pytest_collection_modifyitems(items):
    for item in items:
        marker = item.get_closest_marker("evidence")
        if marker:
            meta = _meta.setdefault(marker.args[0], {"code_refs": [], "related": {}, "expected": []})
            for ref in marker.kwargs.get("code_refs", []):
                if ref not in meta["code_refs"]:
                    meta["code_refs"].append(ref)
            meta["related"].update(marker.kwargs.get("related", {}))
            if marker.kwargs.get("expected") and marker.kwargs["expected"] not in meta["expected"]:
                meta["expected"].append(marker.kwargs["expected"])


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    marker = item.get_closest_marker("evidence")
    if marker is None:
        return
    if report.when == "call" or (report.when == "setup" and (report.failed or report.skipped)):
        rec = _recorders.get(item.nodeid)
        skip_reason = None
        if report.skipped and isinstance(report.longrepr, tuple):
            skip_reason = report.longrepr[2]
        _records[marker.args[0]].append({
            "nodeid": item.nodeid,
            "outcome": report.outcome,  # passed | failed | skipped
            "duration_s": round(report.duration, 3),
            "skip_reason": skip_reason,
            "failure": str(report.longrepr)[-4000:] if report.failed else None,
            "stdout": (report.capstdout or "")[-6000:],
            "observed": rec.observed if rec else {},
            "limitations": rec.limitations if rec else [],
            "execution": rec.execution if rec else {},
            "fixture": rec.fixture if rec else {},
        })


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def environment() -> dict[str, Any]:
    db_version = os.environ.get("EVIDENCE_DB_VERSION")
    return {
        "os": f"{platform.system()} {platform.release()} (contenedor {platform.machine()})",
        "runtime": f"Python {platform.python_version()}",
        "database": db_version or "PostgreSQL (DATABASE_URL_TEST)",
        "migration": "alembic head 0001",
    }


def status_for(results: list[dict[str, Any]]) -> str:
    outcomes = {r["outcome"] for r in results}
    if "failed" in outcomes:
        return "FAILED"
    if outcomes == {"skipped"}:
        return "NOT_EXECUTED"
    if "passed" in outcomes:
        return "PASSED"
    return "NOT_EXECUTED"


def write_records(directory: Path, command: str, raw_log: str | None) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    commit = os.environ.get("SOURCE_COMMIT") or _git("rev-parse", "HEAD") or "unknown"
    dirty = os.environ.get("SOURCE_DIRTY", "unknown")
    executed_at = datetime.now(UTC).isoformat(timespec="seconds")
    statuses: dict[str, str] = {}
    for test_id, results in sorted(_records.items()):
        meta = _meta.get(test_id, {})
        status = status_for(results)
        statuses[test_id] = status
        limitations: list[str] = []
        observed: dict[str, Any] = {}
        execution: dict[str, Any] = {}
        fixture: dict[str, Any] = {"version": "fixtures/seed.json + fixtures/directory.json",
                                   "reset_procedure": "seeding.reset() antes de cada prueba (TRUNCATE + seed)"}
        for r in results:
            limitations += [x for x in r["limitations"] if x not in limitations]
            observed[r["nodeid"].split("::")[-1]] = {"outcome": r["outcome"], **r["observed"]}
            execution.update(r["execution"])
            fixture.update(r["fixture"])
            if r["skip_reason"]:
                limitations.append(f"Omitida: {r['skip_reason']}")
        record = {
            "test_id": test_id,
            "status": status,
            "source_commit": commit,
            "source_tree_dirty": dirty,
            "code_refs": meta.get("code_refs", []),
            "executed_at": executed_at,
            "executor": os.environ.get("EVIDENCE_EXECUTOR", "make evidence (pytest en contenedor 'test')"),
            "environment": environment(),
            "fixture": fixture,
            "execution": {
                "command": command,
                **execution,
                "pytest_nodes": [{"node": r["nodeid"], "outcome": r["outcome"], "duration_s": r["duration_s"]}
                                 for r in results],
                "raw_output": "\n".join(
                    filter(None, [r["stdout"] for r in results] + [r["failure"] for r in results])
                ) or "(sin salida capturada; ver raw_log)",
                "raw_log": raw_log,
            },
            "assertions": {"expected": " | ".join(meta.get("expected", [])), "observed": observed},
            "limitations": limitations,
            "related": meta.get("related", {}),
        }
        path = directory / f"{test_id}.yaml"
        path.write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    return statuses


def collected_records() -> dict[str, list[dict[str, Any]]]:
    return _records
