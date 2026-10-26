"""Regla de portabilidad (PLAN §5.3, AGENTS.md): sólo construct.platform consulta el sistema operativo."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PAQUETE = Path(__file__).resolve().parent.parent / "src" / "construct"
MODULOS_PROHIBIDOS = {"termios", "tty", "pty", "fcntl", "msvcrt", "winreg", "winsound", "platform"}
ATRIBUTOS_PROHIBIDOS = {("sys", "platform"), ("os", "name"), ("platform", "system"), ("platform", "machine"),
                        ("ctypes", "windll"), ("ctypes", "WinDLL"), ("sys", "getwindowsversion")}

ARCHIVOS = sorted(p for p in PAQUETE.rglob("*.py") if "platform" not in p.relative_to(PAQUETE).parts)


def violaciones(codigo: str) -> list[str]:
    encontradas = []
    for nodo in ast.walk(ast.parse(codigo)):
        if isinstance(nodo, ast.Import):
            encontradas += [f"import {a.name}" for a in nodo.names if a.name.split(".")[0] in MODULOS_PROHIBIDOS]
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
            if nodo.module.split(".")[0] in MODULOS_PROHIBIDOS:
                encontradas.append(f"from {nodo.module} import ...")
        elif (isinstance(nodo, ast.Attribute) and isinstance(nodo.value, ast.Name)
              and (nodo.value.id, nodo.attr) in ATRIBUTOS_PROHIBIDOS):
            encontradas.append(f"{nodo.value.id}.{nodo.attr}")
    return encontradas


def test_hay_archivos_que_revisar() -> None:
    assert any(p.name == "cli.py" for p in ARCHIVOS)


@pytest.mark.parametrize("archivo", ARCHIVOS, ids=lambda p: str(p.relative_to(PAQUETE)))
def test_fuera_de_platform_no_se_consulta_el_sistema(archivo: Path) -> None:
    assert violaciones(archivo.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("codigo", [
    "import termios", "import sys\nif sys.platform == 'win32': pass", "from msvcrt import getch",
    "import os\nos.name", "import platform", "import ctypes\nctypes.windll.kernel32",
])
def test_el_detector_encuentra_las_violaciones(codigo: str) -> None:
    assert violaciones(codigo)


def test_construct_platform_no_cuenta_como_platform() -> None:
    assert violaciones("from construct.platform import host\nfrom construct.platform.accel import detectar") == []
