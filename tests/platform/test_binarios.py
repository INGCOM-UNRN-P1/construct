from __future__ import annotations

import pytest

from construct.platform.binarios import (
    buscar,
    buscar_gdb,
    buscar_git,
    buscar_qemu,
    carpetas_conocidas,
    parsear_version,
)
from construct.platform.host_info import Arquitectura, Sistema
from tests.conftest import crear_host, sondas_binarios


@pytest.mark.parametrize(("texto", "version"), [
    ("QEMU emulator version 10.2.2 (qemu-10.2.2-1.fc44)", (10, 2, 2)),
    ("QEMU emulator version 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1)", (8, 2, 2)),
    ("git version 2.47.1.windows.2", (2, 47, 1)),
    ("GNU gdb (GDB) 15.1", (15, 1, 0)),
    ("sin versión", None),
])
def test_parsear_version(texto: str, version: tuple[int, int, int] | None) -> None:
    assert parsear_version(texto) == version


def test_qemu_en_el_path() -> None:
    sondas = sondas_binarios({"/usr/bin/qemu-system-x86_64": "QEMU emulator version 9.2.0"},
                             path={"qemu-system-x86_64": "/usr/bin/qemu-system-x86_64"})
    encontrado = buscar_qemu(crear_host(), Arquitectura.X86_64, sondas=sondas)
    assert encontrado is not None
    assert encontrado.ruta == "/usr/bin/qemu-system-x86_64"
    assert encontrado.version == (9, 2, 0)


def test_qemu_en_program_files_aunque_no_este_en_el_path() -> None:
    ruta = r"C:\Program Files\qemu\qemu-system-x86_64.exe"
    sondas = sondas_binarios({ruta: "QEMU emulator version 9.1.0"}, entorno={"ProgramFiles": r"C:\Program Files"})
    encontrado = buscar_qemu(crear_host(Sistema.WINDOWS), Arquitectura.X86_64, sondas=sondas)
    assert encontrado is not None and encontrado.ruta == ruta


def test_qemu_aarch64_en_homebrew_de_apple_silicon() -> None:
    ruta = "/opt/homebrew/bin/qemu-system-aarch64"
    sondas = sondas_binarios({ruta: "QEMU emulator version 10.0.0"})
    encontrado = buscar_qemu(crear_host(Sistema.MACOS, Arquitectura.AARCH64), Arquitectura.AARCH64, sondas=sondas)
    assert encontrado is not None and encontrado.ruta == ruta


def test_la_ruta_configurada_tiene_prioridad() -> None:
    sondas = sondas_binarios({"/opt/qemu/bin/qemu-system-x86_64": "QEMU emulator version 9.0.0",
                              "/usr/bin/qemu-system-x86_64": "QEMU emulator version 8.0.0"},
                             path={"qemu-system-x86_64": "/usr/bin/qemu-system-x86_64"})
    encontrado = buscar_qemu(crear_host(), Arquitectura.X86_64, ruta_configurada="/opt/qemu/bin/qemu-system-x86_64",
                             sondas=sondas)
    assert encontrado is not None and encontrado.version == (9, 0, 0)


def test_no_encontrado() -> None:
    assert buscar(crear_host(), ["qemu-system-x86_64"], sondas=sondas_binarios({})) is None


def test_git_de_git_for_windows() -> None:
    ruta = r"C:\Program Files\Git\cmd\git.exe"
    sondas = sondas_binarios({ruta: "git version 2.47.1.windows.2"}, entorno={"ProgramFiles": r"C:\Program Files"})
    encontrado = buscar_git(crear_host(Sistema.WINDOWS), sondas=sondas)
    assert encontrado is not None and encontrado.version == (2, 47, 1)


def test_gdb_prefiere_multiarch() -> None:
    sondas = sondas_binarios({"/usr/bin/gdb-multiarch": "GNU gdb 15.1", "/usr/bin/gdb": "GNU gdb 15.1"},
                             path={"gdb-multiarch": "/usr/bin/gdb-multiarch", "gdb": "/usr/bin/gdb"})
    encontrado = buscar_gdb(crear_host(), sondas=sondas)
    assert encontrado is not None and encontrado.ruta == "/usr/bin/gdb-multiarch"


def test_carpetas_de_windows_sin_duplicados() -> None:
    carpetas = carpetas_conocidas(crear_host(Sistema.WINDOWS),
                                  {"ProgramFiles": r"C:\Program Files", "ProgramW6432": r"C:\Program Files"})
    assert len(carpetas) == len(set(carpetas))
    assert r"C:\Program Files\qemu" in carpetas
