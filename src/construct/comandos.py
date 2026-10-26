"""Comandos de proyecto de la CLI: build, check, fix, run, test y sync (PLAN Fase 3).

Cada comando abre el proyecto (sesión de VM, canal y `construct sync subir` si `[sync] automatico`),
le pide el trabajo a las herramientas del Guest por medio de `core/tool_bridge.py` y, al terminar,
baja los artefactos. Acá vive sólo la presentación en la terminal; la lógica está en `core/`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from construct.consola import conectar_terminal
from construct.core import telemetry as telemetria
from construct.core import tool_bridge as puente
from construct.core.channel import Canal, ErrorRemoto, Resultado
from construct.core.cliente import asegurar_sesion, conectar_sesion
from construct.core.config import Config, cargar_del_directorio
from construct.core.protocolo import Marco, Tipo
from construct.core.sync_manager import ErrorDeSync, SyncManager
from construct.platform import binarios, host
from construct.platform.paths import directorios

consola = Console(stderr=True, highlight=False)
COLORES = {"error": "bold red", "advertencia": "yellow", "estilo": "cyan", "info": "blue"}


@dataclass
class Proyecto:
    carpeta: Path
    config: Config
    canal: Canal
    sync: SyncManager

    @property
    def vm(self) -> str:
        return self.sync.vm

    async def correr(self, argv: list[str], entrada: bytes | None = None) -> Resultado:
        return await self.canal.correr(argv, entrada=entrada, cwd=self.vm)


@asynccontextmanager
async def abrir_proyecto(*, subir: bool = True) -> AsyncIterator[Proyecto]:
    carpeta = Path.cwd()
    config = cargar_del_directorio(carpeta)
    git = binarios.buscar_git(host())
    if git is None:
        raise ErrorDeSync("no se encontró git, que construct usa para sincronizar: ejecutá `construct doctor`.")
    info = asegurar_sesion(directorios(), carpeta, aviso=lambda texto: consola.print(f"[dim]{texto}[/dim]"))
    canal = await conectar_sesion(info)
    proyecto = Proyecto(carpeta, config, canal, SyncManager(carpeta, git.ruta, canal))
    try:
        if subir and config.sync.automatico:
            await proyecto.sync.subir()
        yield proyecto
        nuevos = await proyecto.sync.bajar_artefactos()
        for artefacto in nuevos:
            consola.print(f"[dim]artefacto: {artefacto.relative_to(carpeta).as_posix()}[/dim]")
    finally:
        await canal.cerrar()


# --- Presentación -----------------------------------------------------------------------------


def mostrar_hallazgos(hallazgos: Sequence[dict[str, Any]]) -> None:
    for h in hallazgos:
        lugar = h.get("archivo") or ""
        if h.get("linea"):
            lugar += f":{h['linea']}" + (f":{h['columna']}" if h.get("columna") else "")
        severidad = str(h.get("severidad", "info"))
        consola.print(f"[{COLORES.get(severidad, 'white')}]{severidad.upper():<11}[/] {escape(lugar)}  "
                      f"[dim]{escape(str(h.get('id', '')))}[/dim]")
        consola.print(f"            {escape(str(h.get('mensaje', '')))}")
        if h.get("sugerencia"):
            consola.print(f"            [green]→ {escape(str(h['sugerencia']))}[/green]")


def resumen(hallazgos: Sequence[dict[str, Any]]) -> str:
    cuenta: dict[str, int] = {}
    for h in hallazgos:
        cuenta[str(h.get("severidad", "info"))] = cuenta.get(str(h.get("severidad", "info")), 0) + 1
    return ", ".join(f"{n} {s}" for s, n in sorted(cuenta.items())) or "sin hallazgos"


def emitir_json(datos: dict[str, Any]) -> None:
    print(json.dumps({"schema_version": "1.0.0", "herramienta": "construct", **datos}, ensure_ascii=False, indent=2))


def _salida_cruda(resultado: Resultado) -> None:
    """Lo que dijo la herramienta cuando no hay JSON que leer (falló antes de poder informarlo)."""
    sys.stderr.write(resultado.salida.decode(errors="replace") + resultado.errores.decode(errors="replace"))


# --- Comandos ---------------------------------------------------------------------------------


async def compilar(proyecto: Proyecto, fuentes: Sequence[str], salida: str, *,
                   sanitizers: bool = False) -> tuple[bool, list[dict[str, Any]]]:
    textos = [(proyecto.carpeta / f).read_text(encoding="utf-8", errors="replace") for f in fuentes
              if (proyecto.carpeta / f).is_file()]
    argv = puente.argv_build(fuentes, salida, puente.librerias_necesarias(textos))
    if sanitizers:
        argv += ["--flags", " ".join(puente.FLAGS_SANITIZERS)]
    resultado = await proyecto.correr(argv)
    leida = puente.leer_salida(resultado.texto(), proyecto.vm)
    if not leida.datos:
        _salida_cruda(resultado)
    return resultado.ok, leida.hallazgos


async def build(fuentes: Sequence[str], salida: str, como_json: bool) -> int:
    async with abrir_proyecto() as proyecto:
        fuentes = list(fuentes) or puente.fuentes_del_proyecto(proyecto.carpeta)
        if not fuentes:
            consola.print("[bold red]No hay archivos .c para compilar en esta carpeta.[/bold red]")
            return 1
        ok, hallazgos = await compilar(proyecto, fuentes, salida)
    if como_json:
        emitir_json({"ok": ok, "binario": salida if ok else None, "hallazgos": hallazgos})
    else:
        mostrar_hallazgos(hallazgos)
        if ok:
            consola.print(f"[green]✓[/green] Compilado: {salida} ({resumen(hallazgos)}).")
        else:
            consola.print(f"[bold red]✗ No compila[/bold red] ({resumen(hallazgos)}).")
    return 0 if ok else 1


async def check(objetivo: str, como_json: bool) -> int:
    async with abrir_proyecto() as proyecto:
        resultado = await proyecto.correr(puente.argv_check(objetivo))
    leida = puente.leer_salida(resultado.texto(), proyecto.vm)
    if not leida.datos:
        _salida_cruda(resultado)
        return resultado.codigo or 1
    errores = sum(1 for h in leida.hallazgos if h.get("severidad") == "error")
    if como_json:
        emitir_json({"ok": errores == 0, "hallazgos": leida.hallazgos})
    else:
        mostrar_hallazgos(leida.hallazgos)
        consola.print(f"{'[green]✓[/green]' if errores == 0 else '[bold red]✗[/bold red]'} ripley: {resumen(leida.hallazgos)}.")
    return 0 if errores == 0 else 1


async def fix(rutas: Sequence[str]) -> int:
    async with abrir_proyecto() as proyecto:
        rutas = list(rutas) or [*puente.fuentes_del_proyecto(proyecto.carpeta),
                                *(r.relative_to(proyecto.carpeta).as_posix() for r in proyecto.carpeta.rglob("*.h")
                                  if not r.relative_to(proyecto.carpeta).parts[0].startswith("."))]
        resultado = await proyecto.correr(puente.argv_fix(rutas))
        sys.stderr.write(resultado.texto())
        if not resultado.ok:
            sys.stderr.write(resultado.errores.decode(errors="replace"))
            return resultado.codigo or 1
        traidos, conflictos = await proyecto.sync.bajar()
    if conflictos:
        consola.print("[bold yellow]Los arreglos de gaff chocan con cambios tuyos en:[/bold yellow] "
                      + ", ".join(conflictos) + ". Resolvelos y ejecutá `construct sync`.")
        return 1
    consola.print("[green]✓[/green] " + ("Arreglos de gaff aplicados en tus archivos." if traidos
                                         else "gaff no encontró nada para arreglar."))
    return 0


async def _post_mortem(proyecto: Proyecto, binario: str, argumentos: Sequence[str], entrada: bytes) -> None:
    """RF-04: ante una señal fatal, hal explica la causa (vuelve a correr el programa con la misma entrada)."""
    consola.print("[dim]El programa terminó con un fallo: hal lo analiza…[/dim]")
    resultado = await proyecto.correr(puente.argv_hal(f"./{binario}", argumentos, entrada.decode(errors="replace")))
    datos = puente.extraer_json(resultado.texto()) or {}
    if not datos:
        _salida_cruda(resultado)
        return
    lugar = f"{datos.get('archivo') or '?'}:{datos.get('linea') or '?'}"
    consola.print(f"\n[bold red]{escape(str(datos.get('causa_raiz_titulo') or datos.get('tipo_senal') or 'Fallo'))}"
                  f"[/bold red] en {escape(lugar)} ({escape(str(datos.get('funcion_falla') or '?'))})")
    for clave, color in (("explicacion", "white"), ("accion_correctiva", "green")):
        if datos.get(clave):
            consola.print(f"[{color}]{escape(str(datos[clave]))}[/{color}]")


async def _informe_de_ejecucion(proyecto: Proyecto, ruta: str) -> list[dict[str, Any]]:
    """Lo que dejó libconstruct_preload (si el programa terminó normalmente), ubicado con addr2line."""
    try:
        datos = json.loads(await proyecto.canal.leer_archivo(ruta))
    except (ErrorRemoto, ValueError):
        return []  # terminó con una señal o sin el preload: no hay informe
    await proyecto.correr(["rm", "-f", ruta])
    ubicaciones: dict[tuple[str, str], telemetria.Ubicacion] = {}
    for modulo, offsets in telemetria.offsets_por_modulo(datos).items():
        ruta_modulo = modulo if modulo.startswith("/") else f"{proyecto.vm}/{modulo.removeprefix('./')}"
        salida = await proyecto.correr(telemetria.argv_addr2line(ruta_modulo, offsets))
        for offset, lugar in telemetria.parsear_addr2line(salida.texto(), offsets).items():
            ubicaciones[(modulo, offset)] = lugar
    return telemetria.analizar_informe(datos, ubicaciones, lambda r: puente.ruta_en_host(r, proyecto.vm))


async def run(binario: str | None, argumentos: Sequence[str], *, tetsuo: bool, aislado: bool) -> int:
    async with abrir_proyecto() as proyecto:
        fuentes = puente.fuentes_del_proyecto(proyecto.carpeta)
        if binario is None:
            ok, hallazgos = await compilar(proyecto, fuentes, puente.BINARIO_POR_DEFECTO)
            if not ok:
                mostrar_hallazgos(hallazgos)
                consola.print("[bold red]✗ No compila: corregí los errores antes de ejecutar.[/bold red]")
                return 1
            binario = puente.BINARIO_POR_DEFECTO
        entrada = b"" if sys.stdin.isatty() else sys.stdin.buffer.read() if (tetsuo or aislado) else None

        if tetsuo:
            # tetsuo traduce lo que informan los sanitizers: el binario tiene que estar instrumentado.
            ok, hallazgos = await compilar(proyecto, fuentes, puente.BINARIO_TETSUO, sanitizers=True)
            if not ok:
                mostrar_hallazgos(hallazgos)
                consola.print("[bold red]✗ No se pudo compilar con AddressSanitizer y UBSan.[/bold red]")
                return 1
            resultado = await proyecto.correr(puente.argv_tetsuo(f"./{puente.BINARIO_TETSUO}",
                                                                 (entrada or b"").decode(errors="replace")))
            leida = puente.leer_salida(resultado.texto(), proyecto.vm)
            if not leida.datos:
                _salida_cruda(resultado)
            mostrar_hallazgos(leida.hallazgos)
            consola.print(f"tetsuo: {resumen(leida.hallazgos)}.")
            return 0 if not leida.hallazgos else 1

        if aislado:
            resultado = await proyecto.correr(puente.argv_nostromo(
                f"./{binario}", argumentos, entrada=(entrada or b"").decode(errors="replace")))
            datos = puente.extraer_json(resultado.texto()) or {}
            if not datos:
                _salida_cruda(resultado)
                return resultado.codigo or 1
            sys.stdout.write(str(datos.get("stdout", "")))
            sys.stderr.write(str(datos.get("stderr", "")))
            codigo = int(datos.get("codigo_retorno", resultado.codigo))
            tipo = str(datos.get("error_tipo") or "")
            if tipo == "TIMEOUT":
                consola.print("[bold red]El programa superó el tiempo límite del sandbox.[/bold red]")
            elif tipo in ("MEMORY", "MEMORIA", "OOM"):
                consola.print("[bold red]El programa superó el límite de memoria del sandbox.[/bold red]")
            elif tipo:
                consola.print(f"[bold red]El programa terminó con código {codigo}[/bold red] [dim](nostromo: {escape(tipo)})[/dim]")
            return codigo

        informe = f"/tmp/construct-informe-{os.getpid()}-{id(proyecto)}.json"
        proceso = await proyecto.canal.ejecutar(
            [f"./{binario}", *argumentos], cwd=proyecto.vm, telemetria_ms=100,
            env={"LD_PRELOAD": telemetria.PRELOAD, "CONSTRUCT_INFORME": informe})
        enviada = bytearray()
        marcos: list[Marco] = []
        codigo = await conectar_terminal(proceso, registro_entrada=enviada, otros=marcos)
        if proceso.error:
            consola.print(f"[bold red]Error:[/bold red] {escape(proceso.error)}")
            return codigo
        hallazgos = telemetria.analizar_muestras(
            [telemetria.Muestra.desde(m.json()) for m in marcos if m.tipo is Tipo.TELEMETRIA])
        hallazgos += await _informe_de_ejecucion(proyecto, informe)
        if hallazgos:
            consola.print("\n[bold]Telemetría de la ejecución[/bold]")
            mostrar_hallazgos(hallazgos)
        if puente.es_crash(proceso.senal):
            await _post_mortem(proyecto, binario, argumentos, bytes(enviada))
        return codigo


async def test(como_json: bool) -> int:
    async with abrir_proyecto() as proyecto:
        pruebas = puente.pruebas_del_proyecto(proyecto.carpeta)
        if not pruebas:
            consola.print("No hay pruebas (tests/test_*.c) en este proyecto.")
            return 0
        fuentes = puente.fuentes_del_proyecto(proyecto.carpeta)
        resultados = []
        for prueba in pruebas:
            compilar_argv, binario = puente.argv_prueba(prueba, fuentes)
            compilacion = await proyecto.correr(compilar_argv)
            if not compilacion.ok:
                resultados.append({"prueba": prueba, "ok": False, "compila": False,
                                   "salida": compilacion.errores.decode(errors="replace")})
                continue
            ejecucion = await proyecto.correr([f"./{binario}"])
            resultados.append({"prueba": prueba, "ok": ejecucion.ok, "compila": True, "codigo": ejecucion.codigo,
                               "senal": ejecucion.senal,
                               "salida": (ejecucion.salida + ejecucion.errores).decode(errors="replace")})
    if como_json:
        emitir_json({"ok": all(r["ok"] for r in resultados), "pruebas": resultados})
    else:
        for r in resultados:
            marca = "[green]PASS[/green]" if r["ok"] else "[bold red]FAIL[/bold red]"
            consola.print(f"{marca} {r['prueba']}" + ("" if r["compila"] else " (no compila)"))
            if not r["ok"] or not r["compila"]:
                consola.print(escape(str(r["salida"]).rstrip()))
        aprobadas = sum(1 for r in resultados if r["ok"])
        consola.print(f"{aprobadas}/{len(resultados)} suites aprobadas.")
    return 0 if all(r["ok"] for r in resultados) else 1


async def sync(accion: str, como_json: bool) -> int:
    async with abrir_proyecto(subir=False) as proyecto:
        gestor = proyecto.sync
        if accion == "estado":
            estado = await gestor.estado()
            if como_json:
                emitir_json({"cambios_host": estado.cambios_host, "cambios_vm": estado.cambios_vm,
                             "fusion_pendiente": estado.fusion_pendiente})
            else:
                consola.print(f"Cambios del Host sin subir: {', '.join(estado.cambios_host) or 'ninguno'}")
                consola.print(f"Cambios de la VM sin bajar: {'sí' if estado.cambios_vm else 'no'}")
                if estado.fusion_pendiente:
                    consola.print("[yellow]Hay una fusión con conflictos sin terminar.[/yellow]")
            return 0
        if accion == "subir":
            consola.print("[green]✓[/green] " + ("VM actualizada." if await gestor.subir() else "La VM ya estaba al día."))
            return 0
        if accion == "bajar":
            traidos, conflictos = await gestor.bajar()
        elif accion == "artefactos":
            traidos, conflictos = False, []
        else:
            resultado = await gestor.sincronizar()
            traidos, conflictos = resultado.bajados, resultado.conflictos
            if resultado.subidos:
                consola.print("[green]✓[/green] VM actualizada.")
    if conflictos:
        consola.print("[bold yellow]Conflictos entre tus cambios y los de la VM en:[/bold yellow] " + ", ".join(conflictos))
        consola.print("Elegí en cada archivo qué queda (marcas <<<<<<< y >>>>>>>) y ejecutá `construct sync`.")
        return 1
    if traidos:
        consola.print("[green]✓[/green] Cambios de la VM incorporados.")
    return 0
