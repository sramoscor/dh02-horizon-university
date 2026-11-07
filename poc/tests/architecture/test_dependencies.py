"""T-ARCH-01 · Reglas de dependencias (§5.4, Figura F04; E2-ADR-002; E2-ADR-009).

Incluye un control negativo: se planta una importación prohibida en una copia del código y el linter debe
fallar. Sin ese control, un "KEPT" podría significar solo que el linter no mira donde creemos.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[2]
LINT = [sys.executable, "-m", "importlinter.cli", "--no-cache"]
REFS = ["poc/pyproject.toml:[tool.importlinter]", "poc/backend/domain", "poc/web"]
RELATED = {"requirements": [], "qualities": ["QS-04"], "decisions": ["E2-ADR-002", "E2-ADR-006", "E2-ADR-009"]}


def lint(cwd: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-c", "import sys; from importlinter.cli import lint_imports_command as c; "
                                 "sys.argv=['lint-imports','--no-cache']; c()"]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={"PYTHONPATH": str(cwd), "PATH": ""})


def copy_tree(tmp_path: Path) -> Path:
    dst = tmp_path / "poc"
    for name in ("backend", "web", "mocks"):
        shutil.copytree(POC / name, dst / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(POC / "pyproject.toml", dst / "pyproject.toml")
    return dst


@pytest.mark.evidence("T-ARCH-01", code_refs=REFS, related=RELATED,
                      expected="El dominio no importa HTTP, SQL ni SDK de proveedores; C-WEB no importa el backend")
def test_contracts_are_kept(evidence):
    result = lint(POC)
    print(result.stdout[-2500:])
    assert result.returncode == 0, result.stdout + result.stderr
    kept = [line for line in result.stdout.splitlines() if line.endswith("KEPT")]
    assert len(kept) == 5
    evidence.observe("contratos", kept)
    evidence.set_execution(linter="import-linter 2.15 (lint-imports --no-cache)")


@pytest.mark.evidence("T-ARCH-01")
@pytest.mark.parametrize("target,planted", [
    ("backend/domain/model.py", "import sqlalchemy  # violación plantada\n"),
    ("backend/application/triage.py", "import backend.infrastructure.sql  # violación plantada\n"),
    ("web/__init__.py", "import backend.domain.model  # violación plantada\n"),
])
def test_negative_control_planted_violation_is_detected(tmp_path, target, planted, evidence):
    copy = copy_tree(tmp_path)
    path = copy / target
    path.write_text(planted + path.read_text())
    result = lint(copy)
    assert result.returncode != 0 and "BROKEN" in result.stdout, result.stdout
    evidence.observe(f"control_negativo[{target}]", "BROKEN (detectado)")


@pytest.mark.evidence("T-ARCH-01")
def test_streamlit_client_does_not_touch_database_or_domain(evidence):
    offenders = []
    for py in (POC / "web").rglob("*.py"):
        text = py.read_text()
        for forbidden in ("import backend", "from backend", "sqlalchemy", "psycopg", "DATABASE_URL"):
            if forbidden in text:
                offenders.append(f"{py.name}: {forbidden}")
    assert offenders == []
    evidence.observe("web_sin_acceso_a_bd_ni_dominio", "ninguna referencia a backend, sqlalchemy, psycopg o "
                                                       "DATABASE_URL en poc/web")
