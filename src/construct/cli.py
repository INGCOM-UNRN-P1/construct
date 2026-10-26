"""Línea de comandos de construct (RF-02, LINEAMIENTOS §3.2).

Los subcomandos de la SPEC (`ui`, `jonesy`, `vm`, `sync`, `build`, `check`, `test`, `run`, `dev`)
se agregan a medida que se implementan sus fases del plan.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TaskID, TextColumn, TransferSpeedColumn
from yutani.cli import crear_app
from yutani.doctor import SCHEMA_VERSION, informe, mostrar

from construct import __version__
from construct.core import imagenes
from construct.core.config import ErrorDeConfiguracion, cargar_del_directorio
from construct.core.doctor import Entorno, chequeos
from construct.core.setup import ResultadoSetup, preparar
from construct.platform import host
from construct.platform.paths import directorios
from construct.platform.terminal import preparar_consola

AYUDA = (
    "IDE pedagógico de Programación 1: corre las herramientas del perfil estudiante dentro de una "
    "máquina virtual Alpine acelerada por hardware, igual en Windows, macOS y Linux."
)

app = crear_app("construct", __version__, AYUDA, errores_extra=(ErrorDeConfiguracion, imagenes.ErrorDeImagen))


def _entorno() -> Entorno:
    return Entorno(host=host(), config=cargar_del_directorio(Path.cwd()), directorios=directorios())


@app.command("doctor")
def doctor(
    json_salida: bool = typer.Option(False, "--json", help="Informe en JSON con schema_version."),
    dev: bool = typer.Option(False, "--dev", help="Verifica también lo que necesita el modo desarrollo (GDB del Host)."),
) -> None:
    """Verifica que el Host tenga lo necesario para correr la máquina virtual."""
    datos = informe("construct", __version__, chequeos(_entorno(), dev=dev))
    if json_salida:
        typer.echo(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        mostrar(datos)
    raise typer.Exit(0 if datos["ok"] else 1)


def _setup_json(resultado: ResultadoSetup) -> dict[str, Any]:
    imagen = resultado.imagen
    return {
        "schema_version": SCHEMA_VERSION,
        "herramienta": "construct",
        "version": __version__,
        "ok": resultado.ok,
        "chequeos": [asdict(c) for c in resultado.chequeos],
        "imagen": None if imagen is None else {
            "arquitectura": imagen.arquitectura.value, "version": imagen.version, "protocolo": imagen.protocolo,
            "carpeta": str(imagen.carpeta), "descargada": resultado.descargada,
        },
        "error_imagen": resultado.error_imagen,
    }


@app.command("setup")
def setup(
    manifiesto: str = typer.Option(None, "--manifiesto", "-m", show_default=False,
                                   help="URL o ruta del manifiesto de imágenes (por defecto, la última publicada)."),
    forzar: bool = typer.Option(False, "--forzar", help="Vuelve a bajar la imagen aunque ya esté instalada."),
    sin_imagen: bool = typer.Option(False, "--sin-imagen", help="Sólo prepara las carpetas y diagnostica el Host."),
    json_salida: bool = typer.Option(False, "--json", help="Resultado en JSON con schema_version."),
) -> None:
    """Prepara el Host: carpetas, diagnóstico con instrucciones y descarga de la imagen del Guest."""
    consola = Console(stderr=json_salida)
    with Progress(TextColumn("[bold]{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
                  console=consola, transient=True) as barra:
        tareas: dict[str, TaskID] = {}

        def progreso(nombre: str, bajados: int, total: int | None) -> None:
            if nombre not in tareas:
                tareas[nombre] = barra.add_task(f"Bajando {nombre}", total=total)
            barra.update(tareas[nombre], completed=bajados)

        resultado = preparar(_entorno(), manifiesto=imagenes.url_del_manifiesto(explicita=manifiesto),
                             forzar=forzar, sin_imagen=sin_imagen, progreso=progreso)

    if json_salida:
        typer.echo(json.dumps(_setup_json(resultado), ensure_ascii=False, indent=2))
        raise typer.Exit(0 if resultado.ok else 1)

    if resultado.faltantes:
        mostrar(informe("construct", __version__, resultado.chequeos), consola)
    else:
        consola.print("[green]✓[/green] El Host tiene todo lo necesario.")
    if resultado.error_imagen:
        consola.print(f"[bold red]✗ Imagen del Guest:[/bold red] {resultado.error_imagen}")
    elif resultado.imagen is not None:
        accion = "descargada" if resultado.descargada else "ya estaba instalada"
        consola.print(f"[green]✓[/green] Imagen {resultado.imagen.arquitectura.value} {resultado.imagen.version} "
                      f"{accion} en {resultado.imagen.carpeta}")
    elif sin_imagen:
        consola.print("[yellow]–[/yellow] Imagen del Guest sin descargar (--sin-imagen).")
    if resultado.ok:
        consola.print("[bold green]Listo: ya podés usar construct.[/bold green]")
    else:
        consola.print("[bold red]Resolvé lo marcado arriba y ejecutá `construct setup` de nuevo.[/bold red]")
    raise typer.Exit(0 if resultado.ok else 1)


def main() -> None:
    preparar_consola()
    app()
