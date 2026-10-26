"""Modo desarrollo (PLAN §3.4): `construct dev gdb` y `construct dev gdbserver`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from construct import comandos
from construct.cli import app
from construct.core.config import cargar_del_directorio

runner = CliRunner()

LDD = """\tlinux-vdso.so.1 (0x00007ffd5a9f2000)
\tlibm.so.6 => /lib/x86_64-linux-gnu/libm.so.6 (0x00007f1c2a000000)
\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f1c29e00000)
\t/lib64/ld-linux-x86-64.so.2 (0x00007f1c2a100000)
"""


def test_bibliotecas_de_ldd() -> None:
    assert comandos.bibliotecas_de_ldd(LDD) == ["/lib/x86_64-linux-gnu/libm.so.6", "/lib/x86_64-linux-gnu/libc.so.6",
                                                "/lib64/ld-linux-x86-64.so.2"]
    assert comandos.bibliotecas_de_ldd("\tnot a dynamic executable\n") == []


def test_citar_shell() -> None:
    assert comandos.citar_shell("/usr/bin/python3.12") == "/usr/bin/python3.12"
    assert comandos.citar_shell("C:\\Users\\Martín Pérez") == "'C:\\Users\\Martín Pérez'"
    assert comandos.citar_shell("it's") == "'it'\\''s'"
    assert comandos.citar_shell("") == "''"


def test_exige_modo_desarrollo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(comandos.VARIABLE_DEV, raising=False)
    with pytest.raises(ValueError, match="modo desarrollo está apagado"):
        comandos.exigir_modo_desarrollo(cargar_del_directorio(tmp_path))
    monkeypatch.setenv(comandos.VARIABLE_DEV, "1")
    comandos.exigir_modo_desarrollo(cargar_del_directorio(tmp_path))
    monkeypatch.delenv(comandos.VARIABLE_DEV)
    (tmp_path / "project.toml").write_text("[dev]\nhabilitado = true\n", encoding="utf-8")
    comandos.exigir_modo_desarrollo(cargar_del_directorio(tmp_path))


def test_dev_oculto_en_la_ayuda() -> None:
    assert " dev " not in runner.invoke(app, ["--help"]).output
    assert "gdbserver" in runner.invoke(app, ["dev", "--help"]).output


def test_dev_apagado_sin_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(comandos.VARIABLE_DEV, raising=False)
    monkeypatch.delenv("P1_DEPURAR", raising=False)
    # Como el script instalado: TyperConErrores actúa en Typer.__call__, que CliRunner saltea.
    salida = subprocess.run([sys.executable, "-m", "construct", "dev", "gdbserver", "./programa"],
                            check=False, capture_output=True, text=True, encoding="utf-8")
    assert salida.returncode == 1
    assert "Traceback" not in salida.stderr
    assert "modo desarrollo está apagado" in salida.stderr
