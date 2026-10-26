"""Trabajo de proyecto sobre la VM, compartido por la CLI y la interfaz (SPEC §4, PLAN Fase 3).

Las funciones de arriba (`compilar`, `auditar`, `correr_pruebas`, ...) hacen el trabajo y devuelven
datos; `comandos.py` los muestra en la terminal. `Orquestador` atiende los comandos de
`core/events.py` y publica los eventos para la TUI: un trabajo a la vez (en orden de llegada),
salvo `EnviarEntrada` e `Interrumpir`, que van directo al programa que está corriendo.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from construct.core import events as ev
from construct.core import telemetry as telemetria
from construct.core import tool_bridge as puente
from construct.core.channel import Canal, ErrorDeCanal, ErrorRemoto, ProcesoRemoto, Resultado
from construct.core.config import Config
from construct.core.protocolo import Tipo
from construct.core.sync_manager import ErrorDeSync, SyncManager

ERRORES_DE_TAREA = (ErrorDeSync, ErrorDeCanal, ErrorRemoto, OSError, ValueError)
_informes = itertools.count()


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


@dataclass(frozen=True)
class Lectura:
    """Lo que devolvió una herramienta: sus hallazgos y, si no informó JSON, su salida tal cual."""

    ok: bool
    hallazgos: list[dict[str, Any]]
    datos: dict[str, Any] = field(default_factory=dict)
    crudo: str | None = None
    codigo: int = 0


def _crudo(resultado: Resultado) -> str:
    return resultado.salida.decode(errors="replace") + resultado.errores.decode(errors="replace")


def _leer(resultado: Resultado, vm: str) -> tuple[puente.SalidaHerramienta, str | None]:
    leida = puente.leer_salida(resultado.texto(), vm)
    return leida, None if leida.datos else _crudo(resultado)


# --- Trabajos ---------------------------------------------------------------------------------


async def compilar(proyecto: Proyecto, fuentes: Sequence[str], salida: str, *, sanitizers: bool = False) -> Lectura:
    textos = [(proyecto.carpeta / f).read_text(encoding="utf-8", errors="replace") for f in fuentes
              if (proyecto.carpeta / f).is_file()]
    argv = puente.argv_build(fuentes, salida, puente.librerias_necesarias(textos))
    if sanitizers:
        argv += ["--flags", " ".join(puente.FLAGS_SANITIZERS)]
    resultado = await proyecto.correr(argv)
    leida, crudo = _leer(resultado, proyecto.vm)
    return Lectura(resultado.ok, leida.hallazgos, leida.datos, crudo, resultado.codigo)


async def auditar(proyecto: Proyecto, objetivo: str = ".") -> Lectura:
    """ripley: sin errores es ok (las advertencias no lo impiden)."""
    resultado = await proyecto.correr(puente.argv_check(objetivo))
    leida, crudo = _leer(resultado, proyecto.vm)
    errores = sum(1 for h in leida.hallazgos if h.get("severidad") == "error")
    return Lectura(crudo is None and errores == 0, leida.hallazgos, leida.datos, crudo, resultado.codigo or 1)


async def tetsuo(proyecto: Proyecto, binario: str, entrada: bytes) -> Lectura:
    """Falla con hallazgos o si tetsuo dice que no pasó (aunque no haya podido leer a los sanitizers)."""
    resultado = await proyecto.correr(puente.argv_tetsuo(f"./{binario}", entrada.decode(errors="replace")))
    leida, crudo = _leer(resultado, proyecto.vm)
    paso = leida.datos.get("ok", leida.datos.get("passed", True)) is not False
    return Lectura(paso and not leida.hallazgos, leida.hallazgos, leida.datos, crudo, resultado.codigo)


def resumen_tetsuo(lectura: Lectura) -> str:
    if lectura.ok or lectura.hallazgos:
        return f"tetsuo: {resumen(lectura.hallazgos)}"
    return "tetsuo: el programa falló, pero no trajo el detalle de los sanitizers"


async def nostromo(proyecto: Proyecto, binario: str, argumentos: Sequence[str], entrada: bytes) -> Lectura:
    """El programa en el sandbox: `datos` trae stdout, stderr, codigo_retorno y error_tipo."""
    resultado = await proyecto.correr(puente.argv_nostromo(f"./{binario}", argumentos,
                                                           entrada=entrada.decode(errors="replace")))
    datos = puente.extraer_json(resultado.texto()) or {}
    codigo = int(datos.get("codigo_retorno", resultado.codigo)) if datos else (resultado.codigo or 1)
    return Lectura(codigo == 0, [], datos, None if datos else _crudo(resultado), codigo)


def explicar_nostromo(tipo: str, codigo: int) -> str:
    if tipo == "TIMEOUT":
        return "El programa superó el tiempo límite del sandbox."
    if tipo in ("MEMORY", "MEMORIA", "OOM"):
        return "El programa superó el límite de memoria del sandbox."
    return f"El programa terminó con código {codigo} (nostromo: {tipo})." if tipo else ""


async def diagnosticar_crash(proyecto: Proyecto, binario: str, argumentos: Sequence[str], entrada: bytes) -> Lectura:
    """RF-04: hal vuelve a correr el programa con la misma entrada y explica la señal."""
    resultado = await proyecto.correr(puente.argv_hal(f"./{binario}", argumentos, entrada.decode(errors="replace")))
    datos = puente.extraer_json(resultado.texto()) or {}
    return Lectura(bool(datos), [], datos, None if datos else _crudo(resultado), resultado.codigo)


def ruta_de_informe() -> str:
    return f"/tmp/construct-informe-{next(_informes)}-{id(_informes)}.json"


def entorno_de_telemetria(informe: str) -> dict[str, str]:
    return {"LD_PRELOAD": telemetria.PRELOAD, "CONSTRUCT_INFORME": informe}


async def informe_de_ejecucion(proyecto: Proyecto, ruta: str) -> list[dict[str, Any]]:
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


async def correr_pruebas(proyecto: Proyecto, al_terminar: Callable[[ev.ResultadoPrueba], None] | None = None,
                         ) -> list[ev.ResultadoPrueba]:
    fuentes = puente.fuentes_del_proyecto(proyecto.carpeta)
    resultados = []
    for prueba in puente.pruebas_del_proyecto(proyecto.carpeta):
        compilar_argv, binario = puente.argv_prueba(prueba, fuentes)
        compilacion = await proyecto.correr(compilar_argv)
        if not compilacion.ok:
            resultado = ev.ResultadoPrueba(prueba, False, False, compilacion.errores.decode(errors="replace"))
        else:
            ejecucion = await proyecto.correr([f"./{binario}"])
            resultado = ev.ResultadoPrueba(prueba, ejecucion.ok, True,
                                           (ejecucion.salida + ejecucion.errores).decode(errors="replace"),
                                           ejecucion.codigo, ejecucion.senal)
        resultados.append(resultado)
        if al_terminar is not None:
            al_terminar(resultado)
    return resultados


def resumen(hallazgos: Sequence[Mapping[str, Any]]) -> str:
    cuenta: dict[str, int] = {}
    for h in hallazgos:
        cuenta[str(h.get("severidad", "info"))] = cuenta.get(str(h.get("severidad", "info")), 0) + 1
    return ", ".join(f"{n} {s}" for s, n in sorted(cuenta.items())) or "sin hallazgos"


def congelar(hallazgos: Sequence[Mapping[str, Any]]) -> tuple[ev.Hallazgo, ...]:
    return tuple(MappingProxyType(dict(h)) for h in hallazgos)


# --- Orquestador de la interfaz ---------------------------------------------------------------


class Orquestador:
    """Atiende la `cmd_queue` de la interfaz y publica en `emitir` (normalmente, `evt_queue.put_nowait`)."""

    def __init__(self, proyecto: Proyecto, emitir: Callable[[ev.Evento], None]) -> None:
        self.proyecto = proyecto
        self.emitir = emitir
        self._trabajos: asyncio.Queue[ev.Comando | None] = asyncio.Queue()
        self._proceso: ProcesoRemoto | None = None
        self._enviada = bytearray()  # la entrada del programa, para repetírsela a hal

    async def atender(self, comandos: asyncio.Queue[ev.Comando | None]) -> None:
        """Hasta recibir None; los trabajos pendientes terminan antes de volver."""
        trabajador = asyncio.create_task(self._trabajar())
        try:
            while (comando := await comandos.get()) is not None:
                await self._despachar(comando)
        finally:
            await self._trabajos.put(None)
            await trabajador

    async def _despachar(self, comando: ev.Comando) -> None:
        if isinstance(comando, ev.EnviarEntrada):
            if self._proceso is not None:
                with contextlib.suppress(ErrorDeCanal):
                    if comando.datos:
                        self._enviada += comando.datos
                        await self._proceso.escribir(comando.datos)
                    else:
                        await self._proceso.cerrar_entrada()
        elif isinstance(comando, ev.Interrumpir):
            if self._proceso is not None:
                with contextlib.suppress(ErrorDeCanal):
                    await self._proceso.enviar_senal(comando.senal)
        else:
            await self._trabajos.put(comando)

    async def _trabajar(self) -> None:
        while (comando := await self._trabajos.get()) is not None:
            await self.ejecutar(comando)

    async def ejecutar(self, comando: ev.Comando) -> None:
        """Un trabajo completo: TareaIniciada, sus eventos y TareaTerminada (o ErrorDeTarea)."""
        tarea, trabajo = self._trabajo(comando)
        self.emitir(ev.TareaIniciada(tarea))
        try:
            if tarea is not ev.Tarea.SINCRONIZAR and self.proyecto.config.sync.automatico:
                await self.proyecto.sync.subir()
            ok, texto = await trabajo()
            if tarea is not ev.Tarea.SINCRONIZAR:
                await self._artefactos()
        except ERRORES_DE_TAREA as error:
            self.emitir(ev.ErrorDeTarea(tarea, str(error)))
            return
        self.emitir(ev.TareaTerminada(tarea, ok, texto))

    def _trabajo(self, comando: ev.Comando) -> tuple[ev.Tarea, Callable[[], Any]]:
        match comando:
            case ev.Compilar():
                return ev.Tarea.COMPILAR, lambda: self._compilar(comando)
            case ev.Auditar():
                return ev.Tarea.AUDITAR, lambda: self._auditar(comando)
            case ev.AplicarAutofix():
                return ev.Tarea.AUTOFIX, lambda: self._autofix(comando)
            case ev.Ejecutar():
                return ev.Tarea.EJECUTAR, lambda: self._ejecutar(comando)
            case ev.Probar():
                return ev.Tarea.PROBAR, self._probar
            case ev.Sincronizar():
                return ev.Tarea.SINCRONIZAR, lambda: self._sincronizar(comando)
        raise TypeError(f"comando desconocido: {comando!r}")

    async def _artefactos(self) -> None:
        for ruta in await self.proyecto.sync.bajar_artefactos():
            self.emitir(ev.ArtefactoDescargado(ruta))

    def _publicar(self, fuente: str, lectura: Lectura) -> None:
        if lectura.crudo is not None:
            self.emitir(ev.SalidaDeHerramienta(fuente, lectura.crudo))
        self.emitir(ev.HallazgosRecibidos(fuente, congelar(lectura.hallazgos)))

    async def _compilar_para(self, salida: str, *, sanitizers: bool = False) -> Lectura:
        fuentes = puente.fuentes_del_proyecto(self.proyecto.carpeta)
        if not fuentes:
            raise ValueError("no hay archivos .c para compilar en esta carpeta.")
        lectura = await compilar(self.proyecto, fuentes, salida, sanitizers=sanitizers)
        self._publicar("daedalus", lectura)
        return lectura

    async def _compilar(self, comando: ev.Compilar) -> tuple[bool, str]:
        lectura = await self._compilar_para(comando.salida)
        return lectura.ok, ("Compilado" if lectura.ok else "No compila") + f" ({resumen(lectura.hallazgos)})"

    async def _auditar(self, comando: ev.Auditar) -> tuple[bool, str]:
        lectura = await auditar(self.proyecto, comando.objetivo)
        self._publicar("ripley", lectura)
        return lectura.ok, f"ripley: {resumen(lectura.hallazgos)}"

    async def _autofix(self, comando: ev.AplicarAutofix) -> tuple[bool, str]:
        rutas = list(comando.rutas) or puente.fuentes_del_proyecto(self.proyecto.carpeta)
        resultado = await self.proyecto.correr(puente.argv_fix(rutas))
        if not resultado.ok:
            self.emitir(ev.SalidaDeHerramienta("gaff", _crudo(resultado)))
            return False, "gaff no pudo aplicar los arreglos"
        traidos, conflictos = await self.proyecto.sync.bajar()
        self.emitir(ev.SincronizacionTerminada(False, traidos, tuple(conflictos)))
        if conflictos:
            return False, "los arreglos de gaff chocan con cambios tuyos en " + ", ".join(conflictos)
        return True, "Arreglos de gaff aplicados" if traidos else "gaff no encontró nada para arreglar"

    async def _ejecutar(self, comando: ev.Ejecutar) -> tuple[bool, str]:
        binario = puente.BINARIO_POR_DEFECTO
        if comando.modo is ev.ModoEjecucion.TETSUO:
            if not (await self._compilar_para(puente.BINARIO_TETSUO, sanitizers=True)).ok:
                return False, "No se pudo compilar con AddressSanitizer y UBSan"
            lectura = await tetsuo(self.proyecto, puente.BINARIO_TETSUO, comando.entrada)
            self._publicar("tetsuo", lectura)
            if not lectura.ok and not lectura.hallazgos and str(lectura.datos.get("raw_output") or "").strip():
                self.emitir(ev.SalidaDeHerramienta("tetsuo", str(lectura.datos["raw_output"])))
            return lectura.ok, resumen_tetsuo(lectura)
        if not (await self._compilar_para(binario)).ok:
            return False, "No compila: corregí los errores antes de ejecutar"
        if comando.modo is ev.ModoEjecucion.AISLADO:
            lectura = await nostromo(self.proyecto, binario, comando.argumentos, comando.entrada)
            if lectura.crudo is not None:
                self.emitir(ev.SalidaDeHerramienta("nostromo", lectura.crudo))
            for clave, errores in (("stdout", False), ("stderr", True)):
                if lectura.datos.get(clave):
                    self.emitir(ev.SalidaPrograma(str(lectura.datos[clave]).encode(), errores))
            detalle = explicar_nostromo(str(lectura.datos.get("error_tipo") or ""), lectura.codigo)
            self.emitir(ev.ProgramaTerminado(lectura.codigo, None, detalle))
            return lectura.ok, detalle or f"código {lectura.codigo}"
        return await self._ejecutar_con_telemetria(binario, comando.argumentos)

    async def _ejecutar_con_telemetria(self, binario: str, argumentos: Sequence[str]) -> tuple[bool, str]:
        informe = ruta_de_informe()
        proceso = await self.proyecto.canal.ejecutar([f"./{binario}", *argumentos], cwd=self.proyecto.vm,
                                                     telemetria_ms=100, env=entorno_de_telemetria(informe))
        if proceso.error:
            raise ValueError(proceso.error)
        muestras = []
        self._proceso, self._enviada = proceso, bytearray()
        try:
            while (marco := await proceso.siguiente()) is not None:
                if marco.tipo in (Tipo.SALIDA, Tipo.ERRORES):
                    self.emitir(ev.SalidaPrograma(marco.datos, marco.tipo is Tipo.ERRORES))
                elif marco.tipo is Tipo.TELEMETRIA:
                    muestra = telemetria.Muestra.desde(marco.json())
                    muestras.append(muestra)
                    self.emitir(ev.MuestraTelemetria(muestra))
        finally:
            self._proceso = None
        codigo = proceso.codigo
        self.emitir(ev.ProgramaTerminado(codigo, proceso.senal))
        hallazgos = telemetria.analizar_muestras(muestras) + await informe_de_ejecucion(self.proyecto, informe)
        self.emitir(ev.HallazgosRecibidos(telemetria.HERRAMIENTA, congelar(hallazgos)))
        if puente.es_crash(proceso.senal):
            lectura = await diagnosticar_crash(self.proyecto, binario, argumentos, bytes(self._enviada))
            if lectura.ok:
                self.emitir(ev.PostMortem(MappingProxyType(lectura.datos)))
            elif lectura.crudo is not None:
                self.emitir(ev.SalidaDeHerramienta("hal", lectura.crudo))
            return False, f"el programa terminó por la señal {proceso.senal}"
        return codigo == 0, f"el programa terminó con código {codigo}"

    async def _probar(self) -> tuple[bool, str]:
        resultados = await correr_pruebas(self.proyecto, self.emitir)
        aprobadas = sum(1 for r in resultados if r.ok)
        if not resultados:
            return True, "No hay pruebas (tests/test_*.c) en este proyecto"
        return aprobadas == len(resultados), f"{aprobadas}/{len(resultados)} suites aprobadas"

    async def _sincronizar(self, comando: ev.Sincronizar) -> tuple[bool, str]:
        gestor = self.proyecto.sync
        subidos, bajados, conflictos = False, False, list[str]()
        if comando.accion is ev.AccionSync.SUBIR:
            subidos = await gestor.subir()
        elif comando.accion is ev.AccionSync.BAJAR:
            bajados, conflictos = await gestor.bajar()
        elif comando.accion is ev.AccionSync.COMPLETA:
            resultado = await gestor.sincronizar()
            subidos, bajados, conflictos = resultado.subidos, resultado.bajados, resultado.conflictos
            for ruta in resultado.artefactos:
                self.emitir(ev.ArtefactoDescargado(ruta))
        if comando.accion is not ev.AccionSync.COMPLETA:
            await self._artefactos()
        self.emitir(ev.SincronizacionTerminada(subidos, bajados, tuple(conflictos)))
        if conflictos:
            return False, "Conflictos en " + ", ".join(conflictos)
        return True, "Sincronizado"
