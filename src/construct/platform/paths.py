"""Directorios del Host para imágenes, discos, estado, caché y logs (PLAN §2.2).

`platformdirs` da la ubicación convencional de cada sistema; `CONSTRUCT_HOME` la reemplaza por
una sola carpeta (para CI, tests o una instalación portable).
"""

from __future__ import annotations

import os
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
