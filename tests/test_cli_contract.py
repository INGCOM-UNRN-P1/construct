"""Contrato de línea de comandos (LINEAMIENTOS §3.2): -h, -v, doctor --json y cero tracebacks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner
from yutani.testing import pruebas_de_contrato

from construct import __version__
from construct.cli import app

test_ayuda, test_version, test_doctor_json = pruebas_de_contrato(app)

runner = CliRunner()


def test_version_exacta() -> None:
    assert runner.invoke(app, ["-v"]).output.strip() == f"construct {__version__}"


def test_doctor_json_con_sobre_comun() -> None:
    datos = json.loads(runner.invoke(app, ["doctor", "--json"]).stdout)
    assert datos["herramienta"] == "construct"
    assert {"nombre", "requerido", "ok", "detalle", "proposito", "sugerencia"} <= set(datos["chequeos"][0])


def test_doctor_dev_agrega_gdb() -> None:
    datos = json.loads(runner.invoke(app, ["doctor", "--json", "--dev"]).stdout)
    assert "gdb (desarrollo)" in [c["nombre"] for c in datos["chequeos"]]


def test_project_toml_invalido_sin_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "project.toml").write_text("[vm]\nram = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("P1_DEPURAR", raising=False)
    # TyperConErrores actúa en Typer.__call__ (CliRunner lo saltea): se corre como el script instalado.
    salida = subprocess.run([sys.executable, "-c", "from construct.cli import main; main()", "doctor"],
                            check=False, capture_output=True, text=True, encoding="utf-8")
    assert salida.returncode == 1
    assert "Traceback" not in salida.stderr
    assert "no tiene la clave «ram»" in salida.stderr
