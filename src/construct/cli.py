"""Línea de comandos de construct (RF-02, LINEAMIENTOS §3.2).

Los subcomandos de la SPEC (`ui`, `jonesy`, `vm`, `sync`, `build`, `check`, `test`, `run`, `setup`)
se agregan a medida que se implementan sus fases del plan.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from yutani.cli import crear_app
from yutani.doctor import informe, mostrar

from construct import __version__
from construct.core.config import ErrorDeConfiguracion, cargar_del_directorio
from construct.core.doctor import Entorno, chequeos
from construct.platform import host
from construct.platform.paths import directorios
from construct.platform.terminal import preparar_consola

AYUDA = (
    "IDE pedagógico de Programación 1: corre las herramientas del perfil estudiante dentro de una "
    "máquina virtual Alpine acelerada por hardware, igual en Windows, macOS y Linux."
)

app = crear_app("construct", __version__, AYUDA, errores_extra=(ErrorDeConfiguracion,))


@app.command("doctor")
def doctor(
    json_salida: bool = typer.Option(False, "--json", help="Informe en JSON con schema_version."),
    dev: bool = typer.Option(False, "--dev", help="Verifica también lo que necesita el modo desarrollo (GDB del Host)."),
) -> None:
    """Verifica que el Host tenga lo necesario para correr la máquina virtual."""
    entorno = Entorno(host=host(), config=cargar_del_directorio(Path.cwd()), directorios=directorios())
    datos = informe("construct", __version__, chequeos(entorno, dev=dev))
    if json_salida:
        typer.echo(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        mostrar(datos)
    raise typer.Exit(0 if datos["ok"] else 1)


def main() -> None:
    preparar_consola()
    app()
