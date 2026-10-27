"""Línea de comandos de construct (RF-02, LINEAMIENTOS §3.2).

Los subcomandos de la SPEC (`ui`, `jonesy`, `vm`, `sync`, `build`, `check`, `test`, `run`, `dev`)
se agregan a medida que se implementan sus fases del plan.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from collections.abc import Coroutine
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TaskID, TextColumn, TransferSpeedColumn
from yutani.cli import CONTEXTO, TyperConErrores, crear_app
from yutani.doctor import SCHEMA_VERSION, Chequeo, informe, mostrar

from construct import __version__, comandos
from construct.consola import conectar_terminal
from construct.core import imagenes
from construct.core.channel import ErrorDeCanal, ErrorRemoto
from construct.core.cliente import ErrorDeArranque, asegurar_sesion, conectar_sesion, detener_sesion
from construct.core.config import ErrorDeConfiguracion, cargar_del_directorio
from construct.core.doctor import Entorno, chequeos, consultar_guest, guest_sin_consultar
from construct.core.sesion import InfoSesion, leer_info
from construct.core.setup import ResultadoSetup, preparar
from construct.platform import host
from construct.platform.paths import directorios
from construct.platform.terminal import preparar_consola

AYUDA = (
    "IDE pedagógico de Programación 1: corre las herramientas del perfil estudiante dentro de una "
    "máquina virtual Debian acelerada por hardware, igual en Windows, macOS y Linux."
)

app = crear_app("construct", __version__, AYUDA, errores_extra=(
    ErrorDeConfiguracion, imagenes.ErrorDeImagen, ErrorDeArranque, ErrorDeCanal, ErrorRemoto, OSError))
vm_app = TyperConErrores(name="vm", context_settings=dict(CONTEXTO), no_args_is_help=True,
                         help="Máquina virtual: arrancarla, apagarla, ver su estado o ejecutar algo adentro.")
app.add_typer(vm_app, name="vm")
consola_err = Console(stderr=True)


def _correr(corrutina: Coroutine[object, object, int]) -> int:
    try:
        return asyncio.run(corrutina)
    except KeyboardInterrupt:
        return 130


def _sesion() -> InfoSesion:
    return asegurar_sesion(directorios(), Path.cwd(),
                           aviso=lambda texto: consola_err.print(f"[dim]{texto}[/dim]"))


def _entorno() -> Entorno:
    return Entorno(host=host(), config=cargar_del_directorio(Path.cwd()), directorios=directorios())


@app.command("ui")
def ui() -> None:
    """Abre el IDE en la terminal: editor, hallazgos, memoria, consola y pruebas sobre la VM."""
    from construct.ui.app import ConstructApp  # Textual sólo se carga para la interfaz
    from construct.ui.keymap import armar

    carpeta = Path.cwd()
    ConstructApp(carpeta, teclas=armar(cargar_del_directorio(carpeta).teclas)).run()


@app.command("doctor")
def doctor(
    json_salida: bool = typer.Option(False, "--json", help="Informe en JSON con schema_version."),
    dev: bool = typer.Option(False, "--dev", help="Verifica también lo que necesita el modo desarrollo (GDB del Host)."),
) -> None:
    """Verifica el Host y, si la máquina virtual está corriendo, las herramientas de adentro."""
    datos = informe("construct", __version__, [*chequeos(_entorno(), dev=dev), *_chequeos_del_guest()])
    if json_salida:
        typer.echo(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        mostrar(datos)
    raise typer.Exit(0 if datos["ok"] else 1)


def _chequeos_del_guest() -> list[Chequeo]:
    info = leer_info(directorios())
    if info is None:
        return [guest_sin_consultar("la VM está apagada: no se consultó mother doctor",
                                    "Arrancala con `construct vm start` y volvé a ejecutar `construct doctor`.")]

    async def consultar() -> list[Chequeo]:
        canal = await conectar_sesion(info)
        try:
            return await consultar_guest(canal)
        finally:
            await canal.cerrar()

    try:
        return asyncio.run(consultar())
    except (ErrorDeCanal, OSError) as error:
        return [guest_sin_consultar(f"no se pudo hablar con la VM: {error}",
                                    "Reiniciala con `construct vm stop` y `construct vm start`.")]


def _describir(info: InfoSesion) -> dict[str, object]:
    return {"pid": info.pid, "qemu_pid": info.qemu_pid, "arquitectura": info.arquitectura,
            "acelerador": info.acelerador, "imagen": info.imagen, "agente": info.agente,
            "activa_hace_s": round(time.time() - info.inicio)}


@vm_app.command("start")
def vm_start() -> None:
    """Arranca la máquina virtual (si no estaba corriendo) y deja la sesión lista."""
    info = _sesion()
    consola_err.print(f"[green]✓[/green] VM {info.arquitectura} con {info.acelerador.upper()} "
                      f"(imagen {info.imagen}, sesión {info.pid}).")


@vm_app.command("stop")
def vm_stop() -> None:
    """Apaga la máquina virtual de forma ordenada."""
    info = leer_info(directorios())
    if info is None:
        consola_err.print("No hay una máquina virtual corriendo.")
        raise typer.Exit(0)
    if not _correr(_detener(info)):
        consola_err.print("[bold red]La VM no se apagó a tiempo.[/bold red]")
        raise typer.Exit(1)
    consola_err.print("[green]✓[/green] Máquina virtual apagada.")


async def _detener(info: InfoSesion) -> int:
    return int(await detener_sesion(info))


@vm_app.command("status")
def vm_status(json_salida: bool = typer.Option(False, "--json", help="Estado en JSON.")) -> None:
    """Muestra si la máquina virtual está corriendo y con qué acelerador."""
    info = leer_info(directorios())
    datos = {"schema_version": SCHEMA_VERSION, "herramienta": "construct", "corriendo": info is not None,
             "vm": _describir(info) if info else None}
    if json_salida:
        typer.echo(json.dumps(datos, ensure_ascii=False, indent=2))
    elif info is None:
        consola_err.print("No hay una máquina virtual corriendo (se arranca sola con cualquier comando).")
    else:
        d = _describir(info)
        consola_err.print(f"VM {d['arquitectura']} con {str(d['acelerador']).upper()}, imagen {d['imagen']}, "
                          f"agente {d['agente']}, activa hace {d['activa_hace_s']} s (sesión {d['pid']}).")


@vm_app.command("exec", context_settings={"allow_interspersed_args": False})
def vm_exec(
    comando: list[str] = typer.Argument(..., help="Comando y argumentos a ejecutar en la VM."),
    root: bool = typer.Option(False, "--root", help="Como root en lugar del usuario estudiante."),
    cwd: str = typer.Option(None, "--cwd", show_default=False, help="Directorio de trabajo en la VM."),
) -> None:
    """Ejecuta un comando dentro de la VM conectado a esta terminal (para diagnóstico)."""
    info = _sesion()
    raise typer.Exit(_correr(_exec(info, comando, root=root, cwd=cwd)))


async def _exec(info: InfoSesion, comando: list[str], *, root: bool = False, cwd: str | None = None) -> int:
    canal = await conectar_sesion(info)
    try:
        proceso = await canal.ejecutar(comando, root=root, cwd=cwd)
        codigo = await conectar_terminal(proceso)
        if proceso.error:
            consola_err.print(f"[bold red]Error:[/bold red] {proceso.error}")
        return codigo
    finally:
        await canal.cerrar()


@app.command("build")
def build(
    fuentes: list[str] = typer.Argument(None, help="Archivos .c (por defecto, todos los del proyecto salvo test_*.c)."),
    salida: str = typer.Option("programa", "--output", "-o", help="Nombre del ejecutable."),
    json_salida: bool = typer.Option(False, "--json", help="Resultado y hallazgos en JSON."),
) -> None:
    """Compila con daedalus en la VM (F7): -Wall -Wextra -Werror -pedantic -std=c11, errores en español."""
    raise typer.Exit(_correr(comandos.build(fuentes or [], salida, json_salida)))


@app.command("check")
def check(
    objetivo: str = typer.Argument(".", help="Archivo o carpeta a auditar."),
    json_salida: bool = typer.Option(False, "--json", help="Hallazgos en JSON."),
) -> None:
    """Audita con ripley (F8): estilo (gaff), antipatrones (spunkmeyer) y seguridad (kaneda)."""
    raise typer.Exit(_correr(comandos.check(objetivo, json_salida)))


@app.command("fix")
def fix(rutas: list[str] = typer.Argument(None, help="Archivos a corregir (por defecto, todos los .c y .h).")) -> None:
    """Aplica los arreglos automáticos de gaff en la VM y los trae a tus archivos."""
    raise typer.Exit(_correr(comandos.fix(rutas or [])))


@app.command("run", context_settings={"allow_interspersed_args": False})
def run(
    binario: str = typer.Argument(None, show_default=False,
                                  help="Ejecutable ya compilado en la VM (por defecto, compila el proyecto y lo corre)."),
    argumentos: list[str] = typer.Argument(None, help="Argumentos para el programa."),
    tetsuo: bool = typer.Option(False, "--tetsuo", help="Con AddressSanitizer y UBSan explicados por tetsuo (Shift+F5)."),
    aislado: bool = typer.Option(False, "--aislado", help="Confinado por nostromo, con límites de tiempo y memoria."),
) -> None:
    """Ejecuta el programa en la VM conectado a esta terminal (F5); si falla con una señal, lo explica hal."""
    raise typer.Exit(_correr(comandos.run(binario, argumentos or [], tetsuo=tetsuo, aislado=aislado)))


@app.command("test")
def test_cmd(json_salida: bool = typer.Option(False, "--json", help="Resultados en JSON.")) -> None:
    """Compila y corre las pruebas de treadstone (tests/test_*.c) con tu código (F6)."""
    raise typer.Exit(_correr(comandos.test(json_salida)))


@app.command("jonesy", context_settings={"allow_interspersed_args": False, "ignore_unknown_options": True})
def jonesy(argumentos: list[str] = typer.Argument(None, help="Argumentos para jonesy (por ejemplo, un archivo).")) -> None:
    """Abre jonesy, el IDE de terminal de la cátedra, dentro de la VM y en esta terminal (RF-05)."""
    raise typer.Exit(_correr(comandos.jonesy(argumentos or [])))


@app.command("sync")
def sync(
    accion: str = typer.Argument("todo", metavar="[subir|bajar|artefactos|estado]",
                                 help="Sin acción: baja, fusiona, sube y trae artefactos."),
    json_salida: bool = typer.Option(False, "--json", help="Estado en JSON (con `estado`)."),
) -> None:
    """Sincroniza tu carpeta con la VM (único responsable de mover archivos entre los dos)."""
    if accion not in ("todo", "subir", "bajar", "artefactos", "estado"):
        raise typer.BadParameter("tiene que ser subir, bajar, artefactos o estado", param_hint="ACCIÓN")
    raise typer.Exit(_correr(comandos.sync(accion, json_salida)))


dev_app = TyperConErrores(name="dev", context_settings=dict(CONTEXTO), no_args_is_help=True,
                          help="Modo desarrollo (PLAN §3.4): GDB del Host contra el gdbserver del Guest.")
app.add_typer(dev_app, name="dev", hidden=True)


@dev_app.command("gdbserver", context_settings={"allow_interspersed_args": False})
def dev_gdbserver(binario: str = typer.Argument(..., help="Programa en la VM."),
                  argumentos: list[str] = typer.Argument(None, help="Argumentos del programa.")) -> None:
    """gdbserver del Guest por esta entrada/salida estándar: `target remote | construct dev gdbserver …`."""
    raise typer.Exit(_correr(comandos.dev_gdbserver(binario, argumentos or [])))


@dev_app.command("gdb", context_settings={"allow_interspersed_args": False})
def dev_gdb(binario: str = typer.Argument(None, show_default=False, help="Programa en la VM (por defecto, compila)."),
            argumentos: list[str] = typer.Argument(None, help="Argumentos del programa.")) -> None:
    """Abre el GDB del Host conectado al programa en la VM (con sysroot y rutas de los fuentes)."""
    comando = asyncio.run(comandos.preparar_dev_gdb(binario, argumentos or []))
    if comando is None:
        raise typer.Exit(1)
    raise typer.Exit(subprocess.run(comando, check=False).returncode)  # GDB queda dueño de la terminal


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
    imagen: Path = typer.Option(None, "--imagen", "-i", show_default=False, file_okay=False,
                                help="Instala una imagen construida en esta máquina (la carpeta que deja "
                                     "vm_image/build_vm.sh) en lugar de bajarla."),
    forzar: bool = typer.Option(False, "--forzar", help="Vuelve a bajar la imagen aunque ya esté instalada."),
    sin_imagen: bool = typer.Option(False, "--sin-imagen", help="Sólo prepara las carpetas y diagnostica el Host."),
    json_salida: bool = typer.Option(False, "--json", help="Resultado en JSON con schema_version."),
) -> None:
    """Prepara el Host: carpetas, diagnóstico con instrucciones y descarga de la imagen del Guest."""
    consola = Console(stderr=json_salida)
    if imagen is not None and (manifiesto is not None or sin_imagen):
        consola.print("[bold red]✗[/bold red] --imagen no se combina con --manifiesto ni con --sin-imagen.")
        raise typer.Exit(2)
    with Progress(TextColumn("[bold]{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
                  console=consola, transient=True) as barra:
        tareas: dict[str, TaskID] = {}

        def progreso(nombre: str, bajados: int, total: int | None) -> None:
            if nombre not in tareas:
                tareas[nombre] = barra.add_task(f"{'Copiando' if imagen is not None else 'Bajando'} {nombre}",
                                                total=total)
            barra.update(tareas[nombre], completed=bajados)

        resultado = preparar(_entorno(), manifiesto=imagenes.url_del_manifiesto(explicita=manifiesto),
                             forzar=forzar, sin_imagen=sin_imagen, progreso=progreso, imagen_local=imagen)

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
        accion = ("instalada" if imagen is not None else "descargada") if resultado.descargada \
            else "ya estaba instalada"
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
