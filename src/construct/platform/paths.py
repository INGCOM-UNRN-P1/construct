"""Directorios del Host para imágenes, discos, estado, caché y logs (PLAN §2.2).

`platformdirs` da la ubicación convencional de cada sistema; `CONSTRUCT_HOME` la reemplaza por
una sola carpeta (para CI, tests o una instalación portable).
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APLICACION = "construct"
VARIABLE_HOME = "CONSTRUCT_HOME"


@dataclass(frozen=True)
class Directorios:
    datos: Path
    imagenes: Path
    """Imágenes base del Guest (solo lectura)."""
    vms: Path
    """Discos de trabajo: overlays del sistema y `workspace.qcow2`."""
    estado: Path
    """Pidfile, puerto, token y locks de la sesión de VM."""
    cache: Path
    logs: Path

    def todos(self) -> tuple[Path, ...]:
        return self.datos, self.imagenes, self.vms, self.estado, self.cache, self.logs

    def crear(self) -> None:
        for directorio in self.todos():
            directorio.mkdir(parents=True, exist_ok=True)


def directorios(entorno: Mapping[str, str] | None = None) -> Directorios:
    entorno = os.environ if entorno is None else entorno
    base = entorno.get(VARIABLE_HOME)
    if base:
        raiz = Path(base)
        return Directorios(raiz, raiz / "imagenes", raiz / "vms", raiz / "estado", raiz / "cache", raiz / "logs")
    datos = platformdirs.user_data_path(APLICACION, appauthor=False)
    estado = platformdirs.user_state_path(APLICACION, appauthor=False)
    if estado == datos:  # Windows y macOS no distinguen datos de estado.
        estado = datos / "estado"
    return Directorios(
        datos=datos,
        imagenes=datos / "imagenes",
        vms=datos / "vms",
        estado=estado,
        cache=platformdirs.user_cache_path(APLICACION, appauthor=False),
        logs=platformdirs.user_log_path(APLICACION, appauthor=False),
    )


def ancestro_existente(ruta: Path) -> Path:
    """La carpeta existente más cercana (para medir espacio libre antes de crear los directorios)."""
    for candidata in (ruta, *ruta.parents):
        if candidata.exists():
            return candidata
    return Path(ruta.anchor) if ruta.anchor else Path.cwd()


def ruta_corta(ruta: Path) -> Path:
    """En Windows, el nombre 8.3 de una ruta existente (sin espacios ni tildes) para pasársela a QEMU.

    Si el volumen no tiene nombres cortos habilitados, Windows devuelve la ruta larga tal cual. En el
    resto de los sistemas es la identidad.
    """
    if sys.platform != "win32":
        return ruta
    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009 - sólo existe en Windows
    largo = kernel32.GetShortPathNameW(str(ruta), None, 0)
    if largo == 0:
        return ruta
    destino = ctypes.create_unicode_buffer(largo)
    if kernel32.GetShortPathNameW(str(ruta), destino, largo) == 0:
        return ruta
    return Path(destino.value)
