"""Acceso a la sesión de VM desde los comandos de la CLI (PLAN Fase 3).

`asegurar_sesion` devuelve la sesión corriendo o la arranca (desacoplada: sobrevive a este
comando) y espera a que el Guest responda; `conectar_sesion` abre un `Canal` hacia ella.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from construct.core.channel import Canal, conectar
from construct.core.config import NOMBRE_ARCHIVO
from construct.core.sesion import InfoSesion, leer_info
from construct.platform.paths import Directorios
from construct.platform.process import lanzar_desacoplado, proceso_vivo

ESPERA_ARRANQUE_S = 240


class ErrorDeArranque(ValueError):
    """La sesión de la VM no llegó a estar lista."""


def _ultimas_lineas(archivo: Path, cantidad: int = 8) -> str:
    try:
        lineas = archivo.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return "\n".join(lineas[-cantidad:])


def arrancar_sesion(dirs: Directorios, directorio: Path, *, inactividad_s: float | None = None,
                    aviso: Callable[[str], None] | None = None) -> InfoSesion:
    """Lanza la sesión de fondo y espera a que escriba `sesion.json`."""
    registro = dirs.logs / "sesion.log"
    argumentos = [sys.executable, "-m", "construct.core.sesion"]
    if (directorio / NOMBRE_ARCHIVO).is_file():
        argumentos += ["--config", str(directorio / NOMBRE_ARCHIVO)]
    if inactividad_s is not None:
        argumentos += ["--inactividad", str(inactividad_s)]
    inicio_registro = registro.stat().st_size if registro.exists() else 0
    pid = lanzar_desacoplado(argumentos, registro=registro, entorno=dict(os.environ))
    if aviso is not None:
        aviso("Arrancando la máquina virtual…")
    limite = time.monotonic() + ESPERA_ARRANQUE_S
    while time.monotonic() < limite:
        info = leer_info(dirs)
        if info is not None:
            return info
        if not proceso_vivo(pid):
            break
        time.sleep(0.2)
    detalle = _ultimas_lineas(registro)
    if registro.exists() and registro.stat().st_size >= inicio_registro:
        with registro.open(encoding="utf-8", errors="replace") as archivo:
            archivo.seek(inicio_registro)
            detalle = archivo.read().strip() or detalle
    raise ErrorDeArranque(f"la máquina virtual no arrancó.\n{detalle}\n(registro completo en {registro})")


def asegurar_sesion(dirs: Directorios, directorio: Path, *, aviso: Callable[[str], None] | None = None) -> InfoSesion:
    return leer_info(dirs) or arrancar_sesion(dirs, directorio, aviso=aviso)


async def conectar_sesion(info: InfoSesion) -> Canal:
    canal, _ = await conectar(info.puerto, info.token)
    return canal


async def detener_sesion(info: InfoSesion, *, timeout: float = 60) -> bool:
    """Pide el apagado y espera a que la sesión termine; False si no terminó a tiempo."""
    canal = await conectar_sesion(info)
    await canal.apagar()
    await canal.cerrar()
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if not proceso_vivo(info.pid):
            return True
        await asyncio.sleep(0.2)
    return False
