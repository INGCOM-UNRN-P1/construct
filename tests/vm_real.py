"""Una sesión de VM real para las suites de integración (PLAN Fase 6, §5.1 `e2e-kvm`).

Necesita la imagen armada con `vm_image/build_vm.sh` en `CONSTRUCT_IMAGEN_PRUEBA` (la misma
variable que `tests/e2e`) y QEMU; sin eso, las pruebas se saltean. La imagen se instala en un
`CONSTRUCT_HOME` temporal como lo haría `construct setup` y la sesión se arranca como la arranca
la CLI: un proceso desacoplado que queda vivo entre comandos.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from construct.core import events as ev
from construct.core import imagenes
from construct.core.cliente import arrancar_sesion, conectar_sesion, detener_sesion
from construct.core.config import Config
from construct.core.orquestador import Orquestador, Proyecto
from construct.core.sesion import InfoSesion
from construct.core.sync_manager import SyncManager
from construct.platform import host
from construct.platform.paths import directorios

GIT = shutil.which("git") or "git"


@dataclass
class VMReal:
    home: Path
    info: InfoSesion


@pytest.fixture(scope="session")
def vm_real(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VMReal]:
    origen = os.environ.get("CONSTRUCT_IMAGEN_PRUEBA")
    if not origen:
        pytest.skip("definí CONSTRUCT_IMAGEN_PRUEBA con la carpeta de la imagen")
    arquitectura = host().arquitectura
    if arquitectura is None or not shutil.which(f"qemu-system-{arquitectura.value}"):
        pytest.skip("hace falta QEMU para la arquitectura de este Host")
    home = tmp_path_factory.mktemp("construct-home")
    previo = os.environ.get("CONSTRUCT_HOME")
    os.environ["CONSTRUCT_HOME"] = str(home)  # lo hereda el proceso de la sesión
    try:
        dirs = directorios()
        dirs.crear()
        imagenes.instalar(imagenes.manifiesto_local(Path(origen), arquitectura), arquitectura, dirs.imagenes)
        info = arrancar_sesion(dirs, home)
        yield VMReal(home, info)
        asyncio.run(detener_sesion(info))
    finally:
        if previo is None:
            os.environ.pop("CONSTRUCT_HOME", None)
        else:
            os.environ["CONSTRUCT_HOME"] = previo


@dataclass
class Trabajo:
    orquestador: Orquestador
    eventos: list[ev.Evento]
    carpeta: Path

    def de_tipo[T](self, tipo: type[T]) -> list[T]:
        return [e for e in self.eventos if isinstance(e, tipo)]

    def hallazgos(self, fuente: str) -> list[ev.Hallazgo]:
        recibidos = [e for e in self.de_tipo(ev.HallazgosRecibidos) if e.fuente == fuente]
        return list(recibidos[-1].hallazgos) if recibidos else []

    @property
    def ultimo(self) -> ev.Evento:
        return self.eventos[-1]


@asynccontextmanager
async def proyecto(vm: VMReal, carpeta: Path, archivos: dict[str, str]) -> AsyncIterator[Trabajo]:
    """Un proyecto del alumno con `archivos`, conectado a la VM por el mismo orquestador que la TUI."""
    for nombre, texto in archivos.items():
        (carpeta / nombre).parent.mkdir(parents=True, exist_ok=True)
        (carpeta / nombre).write_text(texto, encoding="utf-8")
    canal = await conectar_sesion(vm.info)
    eventos: list[ev.Evento] = []
    try:
        sync = SyncManager(carpeta, GIT, canal)
        yield Trabajo(Orquestador(Proyecto(carpeta, Config(), canal, sync), eventos.append), eventos, carpeta)
    finally:
        await canal.cerrar()


def correr(corrutina_de: object) -> object:
    return asyncio.run(asyncio.wait_for(corrutina_de(), 600))  # type: ignore[operator]
