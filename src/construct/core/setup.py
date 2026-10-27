"""`construct setup`: deja el Host listo para correr la VM (PLAN Fase 1).

Crea las carpetas, diagnostica el Host y baja la imagen del Guest que corresponde a su
arquitectura. Nunca instala nada con privilegios: lo que falta del sistema (QEMU, git, la
aceleración) se informa con el comando exacto para resolverlo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yutani.doctor import Chequeo

from construct.core import imagenes
from construct.core.doctor import Entorno, chequeos, resolver_aceleracion


@dataclass(frozen=True)
class ResultadoSetup:
    chequeos: list[Chequeo]
    imagen: imagenes.ImagenInstalada | None
    descargada: bool
    error_imagen: str | None

    @property
    def faltantes(self) -> list[Chequeo]:
        return [c for c in self.chequeos if c.requerido and not c.ok]

    @property
    def ok(self) -> bool:
        return not self.faltantes and self.imagen is not None


def preparar(
    entorno: Entorno,
    *,
    manifiesto: str,
    forzar: bool = False,
    sin_imagen: bool = False,
    progreso: imagenes.Progreso | None = None,
    obtener: Callable[[str], imagenes.Manifiesto] = imagenes.obtener_manifiesto,
    imagen_local: Path | None = None,
) -> ResultadoSetup:
    """Con `imagen_local`, instala la imagen de esa carpeta en lugar de la del manifiesto."""
    entorno.directorios.crear()
    lista = chequeos(entorno, imagen=False)
    if sin_imagen:
        previa = None
        try:
            previa = imagenes.instalada(entorno.directorios.imagenes, resolver_aceleracion(entorno).arquitectura_guest)
        except (ValueError, imagenes.ErrorDeImagen):
            pass  # El chequeo de aceleración ya informa la configuración inválida.
        return ResultadoSetup(lista, previa, False, None)
    try:
        arquitectura = resolver_aceleracion(entorno).arquitectura_guest
    except ValueError as error:
        return ResultadoSetup(lista, None, False, f"no se puede elegir la imagen: {error}")
    try:
        elegido = (imagenes.manifiesto_local(imagen_local, arquitectura) if imagen_local is not None
                   else obtener(manifiesto))
        imagen, descargada = imagenes.instalar(elegido, arquitectura, entorno.directorios.imagenes,
                                               forzar=forzar, progreso=progreso)
    except imagenes.ErrorDeImagen as error:
        return ResultadoSetup(lista, None, False, str(error))
    return ResultadoSetup(lista, imagen, descargada, None)
