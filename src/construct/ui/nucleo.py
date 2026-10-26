"""Lo que la interfaz necesita del núcleo, y su implementación sobre la VM (SPEC §4).

La app sólo conoce `Nucleo`: pone comandos en una cola, recibe eventos por `emitir` y, para el modal
de conflictos, usa el `Resolutor` del `SyncManager`. Los tests de la interfaz le pasan uno falso.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from construct.core import events as ev
from construct.core.channel import Canal
from construct.core.cliente import asegurar_sesion, conectar_sesion
from construct.core.config import Config, cargar_del_directorio
from construct.core.orquestador import Orquestador, Proyecto
from construct.core.sync_manager import ErrorDeSync, SyncManager
from construct.platform import binarios, host
from construct.platform.paths import directorios
from construct.ui.widgets.sync_conflict_modal import Resolutor


class Nucleo(Protocol):
    carpeta: Path
    config: Config
    descripcion: str
    """Lo que muestra la barra de estado: `x86_64 (KVM)` o `x86_64 (TCG — modo compatibilidad)`."""
    resolutor: Resolutor

    async def atender(self, comandos: asyncio.Queue[ev.Comando | None]) -> None: ...
    async def cerrar(self) -> None: ...


AbrirNucleo = Callable[[Path, Callable[[ev.Evento], None], Callable[[str], None]], Awaitable[Nucleo]]
"""(carpeta, emitir, aviso) → el núcleo conectado; `aviso` muestra el progreso del arranque."""


def describir_acelerador(arquitectura: str, acelerador: str) -> str:
    if acelerador.lower() == "tcg":
        return f"{arquitectura} (TCG — modo compatibilidad)"
    return f"{arquitectura} ({acelerador.upper()})"


class NucleoVM:
    def __init__(self, carpeta: Path, config: Config, canal: Canal, orquestador: Orquestador, descripcion: str) -> None:
        self.carpeta, self.config, self.canal = carpeta, config, canal
        self.orquestador = orquestador
        self.descripcion = descripcion
        self.resolutor: Resolutor = orquestador.proyecto.sync

    @classmethod
    async def abrir(cls, carpeta: Path, emitir: Callable[[ev.Evento], None],
                    aviso: Callable[[str], None]) -> NucleoVM:
        """Arranca (o reusa) la sesión de VM y se conecta; ValueError con el motivo si no puede."""
        config = cargar_del_directorio(carpeta)
        git = binarios.buscar_git(host())
        if git is None:
            raise ErrorDeSync("no se encontró git, que construct usa para sincronizar: ejecutá `construct doctor`.")
        info = await asyncio.to_thread(asegurar_sesion, directorios(), carpeta, aviso=aviso)
        canal = await conectar_sesion(info)
        proyecto = Proyecto(carpeta, config, canal, SyncManager(carpeta, git.ruta, canal))
        return cls(carpeta, config, canal, Orquestador(proyecto, emitir),
                   describir_acelerador(info.arquitectura, info.acelerador))

    async def atender(self, comandos: asyncio.Queue[ev.Comando | None]) -> None:
        await self.orquestador.atender(comandos)

    async def cerrar(self) -> None:
        await self.canal.cerrar()
