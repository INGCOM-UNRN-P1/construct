"""Búsqueda de ejecutables del Host: QEMU, Git y (en modo desarrollo) GDB (PLAN §2.3).

En Windows y macOS los instaladores habituales no siempre agregan sus binarios al `PATH`
(QEMU en `C:\\Program Files\\qemu`, Homebrew en `/opt/homebrew/bin` cuando la terminal no lo
cargó), así que además del `PATH` se revisan las carpetas conocidas de cada sistema.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from construct.platform.host_info import Arquitectura, HostInfo, Sistema

Version = tuple[int, int, int]

VERSION_MINIMA_QEMU: Version = (8, 2, 0)
"""Provisoria: se fija con los resultados de la Fase 0 del plan."""
VERSION_MINIMA_GIT: Version = (2, 30, 0)


@dataclass(frozen=True)
class Ejecutable:
    ruta: str
    version: Version | None
    texto_version: str


@dataclass(frozen=True)
class SondasBinarios:
    which: Callable[[str], str | None]
    es_archivo: Callable[[str], bool]
    version: Callable[[str, Sequence[str]], str | None]
    """Primera línea de la salida de `ruta args...`."""
    entorno: Mapping[str, str]


def _primera_linea(ruta: str, argumentos: Sequence[str]) -> str | None:
    try:
        salida = subprocess.run([ruta, *argumentos], check=False, capture_output=True, text=True, timeout=10,
                                encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    lineas = (salida.stdout or salida.stderr).strip().splitlines()
    return lineas[0] if lineas else None


def sondas_reales() -> SondasBinarios:
    return SondasBinarios(which=shutil.which, es_archivo=lambda ruta: Path(ruta).is_file(),
                          version=_primera_linea, entorno=os.environ)


def parsear_version(texto: str) -> Version | None:
    coincidencia = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", texto)
    if coincidencia is None:
        return None
    mayor, menor, parche = coincidencia.groups()
    return int(mayor), int(menor), int(parche or 0)


def formatear_version(version: Version) -> str:
    return ".".join(str(parte) for parte in version)


def _unir(host: HostInfo, carpeta: str, nombre: str) -> str:
    if host.sistema is Sistema.WINDOWS:
        return str(PureWindowsPath(carpeta) / nombre)
    return f"{carpeta.rstrip('/')}/{nombre}"


def carpetas_conocidas(host: HostInfo, entorno: Mapping[str, str]) -> list[str]:
    """Carpetas donde suelen quedar QEMU, Git y GDB fuera del `PATH`."""
    if host.sistema is Sistema.WINDOWS:
        carpetas = []
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = entorno.get(variable)
            if base:
                carpetas += [str(PureWindowsPath(base) / "qemu"), str(PureWindowsPath(base) / "Git" / "cmd")]
        perfil = entorno.get("USERPROFILE")
        if perfil:
            carpetas += [str(PureWindowsPath(perfil) / "scoop" / "shims")]
        carpetas += [r"C:\msys64\ucrt64\bin", r"C:\msys64\mingw64\bin"]
        return list(dict.fromkeys(carpetas))
    if host.sistema is Sistema.MACOS:
        return ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"]
    return ["/usr/bin", "/usr/local/bin"]


def buscar(
    host: HostInfo,
    nombres: Sequence[str],
    *,
    argumentos_version: Sequence[str] = ("--version",),
    ruta_configurada: str | None = None,
    sondas: SondasBinarios | None = None,
) -> Ejecutable | None:
    """El primero de `nombres` que aparezca (ruta configurada, `PATH`, carpetas conocidas)."""
    sondas = sondas or sondas_reales()
    sufijo = ".exe" if host.sistema is Sistema.WINDOWS else ""
    candidatas: list[str] = []
    if ruta_configurada:
        candidatas.append(ruta_configurada)
    for nombre in nombres:
        encontrada = sondas.which(nombre)
        if encontrada:
            candidatas.append(encontrada)
    for carpeta in carpetas_conocidas(host, sondas.entorno):
        candidatas += [_unir(host, carpeta, nombre + sufijo) for nombre in nombres]
    for ruta in candidatas:
        if sondas.es_archivo(ruta):
            texto = sondas.version(ruta, argumentos_version) or ""
            return Ejecutable(ruta, parsear_version(texto), texto)
    return None


def buscar_qemu(host: HostInfo, arquitectura: Arquitectura, *, ruta_configurada: str | None = None,
                sondas: SondasBinarios | None = None) -> Ejecutable | None:
    return buscar(host, [f"qemu-system-{arquitectura.value}"], ruta_configurada=ruta_configurada, sondas=sondas)


def buscar_git(host: HostInfo, *, sondas: SondasBinarios | None = None) -> Ejecutable | None:
    return buscar(host, ["git"], sondas=sondas)


def buscar_gdb(host: HostInfo, *, sondas: SondasBinarios | None = None) -> Ejecutable | None:
    """GDB del Host para el modo desarrollo (PLAN §3.4): primero `gdb-multiarch`."""
    return buscar(host, ["gdb-multiarch", "gdb"], sondas=sondas)
