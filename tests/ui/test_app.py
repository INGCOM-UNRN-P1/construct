"""El IDE de punta a punta con un núcleo falso (sin VM): atajos, eventos y modales."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import MappingProxyType
from typing import Any

from textual.widgets import Input, RichLog, Static, TabbedContent

from construct.core import events as ev
from construct.core.config import Config
from construct.core.sync_manager import VersionesEnConflicto
from construct.core.telemetry import Muestra
from construct.ui import keymap
from construct.ui.app import ConstructApp
from construct.ui.widgets.aviso_modal import AvisoModal
from construct.ui.widgets.bishop_view import BishopView, dibujar_memoria
from construct.ui.widgets.code_editor import CodeEditor
from construct.ui.widgets.hal_postmortem_modal import HalPostMortemModal
from construct.ui.widgets.hallazgos_view import HallazgosView
from construct.ui.widgets.sync_conflict_modal import SyncConflictModal
from construct.ui.widgets.treadstone_view import TreadstoneView

MAIN = "#include <stdio.h>\n\nint main(void)\n{\n    printf(\"hola\\n\");\n    return 0;\n}\n"
ERROR = MappingProxyType({"herramienta": "daedalus", "codigo": "C-ERR", "severidad": "error",
                          "mensaje": "falta un punto y coma", "archivo": "main.c", "linea": 5})
# kaneda llega por ripley con el nombre de su plugin, «security».
GETS = MappingProxyType({"herramienta": "security", "codigo": "KAN-001", "severidad": "error", "cwe": "CWE-242",
                         "mensaje": "gets() está prohibida", "archivo": "main.c", "linea": 5})


class Resolutor:
    def __init__(self) -> None:
        self.pendientes = ["main.c"]
        self.elegidos: list[tuple[str, str]] = []

    def conflictos(self) -> list[str]:
        return list(self.pendientes)

    def versiones(self, ruta: str) -> VersionesEnConflicto:
        return VersionesEnConflicto(ruta, "base\n", "del Host\n", "de la VM\n")

    def elegir(self, ruta: str, eleccion: str) -> None:
        self.elegidos.append((ruta, eleccion))
        self.pendientes.remove(ruta)


class NucleoFalso:
    """Responde a cada comando con los eventos que le da `guion` (por tipo de comando)."""

    def __init__(self, carpeta: Path, emitir: Callable[[ev.Evento], None],
                 guion: dict[type, list[ev.Evento]]) -> None:
        self.carpeta, self.config, self.emitir, self.guion = carpeta, Config(), emitir, guion
        self.descripcion = "x86_64 (KVM)"
        self.resolutor = Resolutor()
        self.recibidos: list[ev.Comando] = []

    async def atender(self, comandos: asyncio.Queue[ev.Comando | None]) -> None:
        while (comando := await comandos.get()) is not None:
            self.recibidos.append(comando)
            for evento in self.guion.get(type(comando), []):
                self.emitir(evento)

    async def cerrar(self) -> None:
        pass


def probar(tmp_path: Path, guion: dict[type, list[ev.Evento]],
           escena: Callable[[Any, ConstructApp, NucleoFalso], Awaitable[None]], *, fuente: str = MAIN) -> NucleoFalso:
    (tmp_path / "main.c").write_text(fuente, encoding="utf-8")
    (tmp_path / "lista.h").write_text("#pragma once\n", encoding="utf-8")
    creados: list[NucleoFalso] = []

    async def abrir(carpeta: Path, emitir: Callable[[ev.Evento], None], _aviso: Callable[[str], None]) -> NucleoFalso:
        creados.append(NucleoFalso(carpeta, emitir, guion))
        return creados[0]

    async def correr() -> None:
        app = ConstructApp(tmp_path, abrir_nucleo=abrir)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            await escena(pilot, app, creados[0])

    asyncio.run(correr())
    return creados[0]


def test_arranque_estado_y_editor(tmp_path: Path) -> None:
    async def escena(pilot: Any, app: ConstructApp, _nucleo: NucleoFalso) -> None:
        estado = str(app.query_one("#estado", Static).render())
        assert "[construct] VM: x86_64 (KVM)" in estado and "Perfil: estudiante" in estado
        editor = app.query_one(CodeEditor)
        assert editor.ruta == "main.c" and editor.text == MAIN
        assert app.query_one("#archivo").value == "main.c"  # type: ignore[attr-defined]

    probar(tmp_path, {}, escena)


def test_f7_guarda_compila_y_marca_el_hallazgo(tmp_path: Path) -> None:
    guion: dict[type, list[ev.Evento]] = {ev.Compilar: [
        ev.TareaIniciada(ev.Tarea.COMPILAR), ev.HallazgosRecibidos("daedalus", (ERROR,)),
        ev.TareaTerminada(ev.Tarea.COMPILAR, False, "No compila (1 error)")]}

    async def escena(pilot: Any, app: ConstructApp, nucleo: NucleoFalso) -> None:
        editor = app.query_one(CodeEditor)
        editor.insert("// cambio\n", (0, 0))
        await pilot.press("f7")
        await pilot.pause()
        assert nucleo.recibidos == [ev.Compilar()]
        assert (tmp_path / "main.c").read_text(encoding="utf-8").startswith("// cambio\n")  # guardó antes
        assert app.query_one(HallazgosView).todos == [ERROR]
        assert editor.marca(5) is not None and editor.marca(4) is None
        editor.cursor_location = (4, 0)
        await pilot.pause()
        assert "falta un punto y coma" in str(app.query_one("#pista", Static).render())

    probar(tmp_path, guion, escena)


def test_alternativas_del_keymap_y_paleta(tmp_path: Path) -> None:
    async def escena(pilot: Any, app: ConstructApp, nucleo: NucleoFalso) -> None:
        await pilot.press("ctrl+l")  # alternativa de F7
        await pilot.press("ctrl+b")  # alternativa de F9, con el cursor en la línea 1
        await pilot.pause()
        assert nucleo.recibidos == [ev.Compilar(), ev.AlternarBreakpoint("main.c", 1)]
        titulos = [c.title for c in app.get_system_commands(app.screen)]
        assert all(a.descripcion in titulos for a in keymap.ACCIONES)

    probar(tmp_path, {}, escena)


def test_kaneda_bloquea_la_ejecucion(tmp_path: Path) -> None:
    guion: dict[type, list[ev.Evento]] = {ev.Auditar: [ev.HallazgosRecibidos("ripley", (GETS,))]}

    async def escena(pilot: Any, app: ConstructApp, nucleo: NucleoFalso) -> None:
        await pilot.press("f8")
        await pilot.pause()
        await pilot.press("f5")
        await pilot.pause()
        assert isinstance(app.screen, AvisoModal) and "CWE-242" in app.screen.cuerpo
        assert not any(isinstance(c, ev.Ejecutar) for c in nucleo.recibidos)

    probar(tmp_path, guion, escena)


def test_ejecutar_consola_telemetria_y_post_mortem(tmp_path: Path) -> None:
    guion: dict[type, list[ev.Evento]] = {
        ev.Ejecutar: [ev.TareaIniciada(ev.Tarea.EJECUTAR), ev.SalidaPrograma(b"hola\n"),
                      ev.MuestraTelemetria(Muestra(0, 0, 2048, 1, 0, 3, 0, 0)),
                      ev.MuestraTelemetria(Muestra(100, 90, 4096, 1, 0, 4, 0, 0)),
                      ev.ProgramaTerminado(-1, "SEGV"),
                      ev.PostMortem(MappingProxyType({"causa_raiz_titulo": "Desreferencia de NULL",
                                                      "archivo": "main.c", "linea": 5}))],
        ev.GuardarReproductor: [ev.TareaTerminada(ev.Tarea.REPRODUCTOR, True, "guardado")],
    }

    async def escena(pilot: Any, app: ConstructApp, nucleo: NucleoFalso) -> None:
        await pilot.press("f5")
        await pilot.pause()
        consola = app.query_one("#consola", RichLog)
        assert any("hola" in linea.text for linea in consola.lines)
        assert "RAM: 4.0 MB | CPU: 90%" in str(app.query_one("#estado", Static).render())
        assert isinstance(app.screen, HalPostMortemModal)
        await pilot.click("#reproductor")
        await pilot.pause()
        assert nucleo.recibidos[-1] == ev.GuardarReproductor()

    probar(tmp_path, guion, escena)


def test_entrada_interactiva_y_ctrl_c(tmp_path: Path) -> None:
    async def escena(pilot: Any, app: ConstructApp, nucleo: NucleoFalso) -> None:
        await pilot.press("f5")
        await pilot.pause()
        assert app.focused is app.query_one("#entrada", Input)
        await pilot.press("4", "2", "enter")
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert nucleo.recibidos[1:] == [ev.EnviarEntrada(b"42\n"), ev.Interrumpir()]

    probar(tmp_path, {}, escena)


def test_depuracion_marca_la_linea_y_f5_continua(tmp_path: Path) -> None:
    detenido = ev.DepuracionDetenida("breakpoint-hit", "main.c", 5, "main",
                                     (ev.MarcoDePila(0, "main", "main.c", 5),), MappingProxyType({"n": "3"}))
    guion: dict[type, list[ev.Evento]] = {
        ev.AlternarBreakpoint: [ev.BreakpointsActualizados(frozenset({("main.c", 5)}))],
        ev.Depurar: [detenido],
        ev.Avanzar: [ev.DepuracionTerminada(0)],
    }

    async def escena(pilot: Any, app: ConstructApp, nucleo: NucleoFalso) -> None:
        editor = app.query_one(CodeEditor)
        editor.cursor_location = (4, 0)
        app.query_one("#entrada-programa").text = "3"  # type: ignore[attr-defined]
        await pilot.press("f9")
        await pilot.pause()
        assert editor.marca(5) is not None
        await pilot.press("ctrl+f5")
        await pilot.pause()
        assert nucleo.recibidos[-1] == ev.Depurar(entrada=b"3\n")
        assert app.depurando and editor.marca(5) == ("▶ ", editor.marca(5)[1])  # type: ignore[index]
        assert app.query_one("#q2", TabbedContent).active == "tab-registros"
        await pilot.press("f5")
        await pilot.pause()
        assert nucleo.recibidos[-1] == ev.Avanzar(ev.Paso.CONTINUAR)
        assert not app.depurando and editor.marca(5) is not None  # vuelve a la marca del breakpoint

    probar(tmp_path, guion, escena)


def test_sync_con_conflictos_abre_el_modal(tmp_path: Path) -> None:
    guion: dict[type, list[ev.Evento]] = {ev.Sincronizar: [ev.SincronizacionTerminada(False, True, ("main.c",))]}

    async def escena(pilot: Any, app: ConstructApp, nucleo: NucleoFalso) -> None:
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SyncConflictModal)
        assert "Sync: CONFLICTOS" in str(app.query_one("#estado", Static).render())
        await pilot.click("#vm-elegir")
        await pilot.pause()
        assert nucleo.resolutor.elegidos == [("main.c", "vm")]
        assert nucleo.recibidos[-1] == ev.Sincronizar()  # resueltos todos: vuelve a sincronizar

    probar(tmp_path, guion, escena)


def test_pruebas_y_memoria(tmp_path: Path) -> None:
    memoria = MappingProxyType({"stack": [{"funcion": "main", "variables": [
        {"nombre": "p", "tipo": "int *", "valor": "0x10", "es_puntero": True, "direccion_apuntada": "0x10"}]}],
        "heap": [{"direccion": "0x10", "tamanio_bytes": 4, "contenido": "7", "punteros_referenciantes": ["p"]}]})
    guion: dict[type, list[ev.Evento]] = {
        ev.Probar: [ev.ResultadoPrueba("tests/test_a.c", True, True, ""),
                    ev.ResultadoPrueba("tests/test_b.c", False, True, "Pista: Olvidaste llamar a bmp_destruir")],
        ev.InspeccionarMemoria: [ev.MemoriaBishop(memoria)],
    }

    async def escena(pilot: Any, app: ConstructApp, nucleo: NucleoFalso) -> None:
        await pilot.press("f6")
        await pilot.press("f12")
        await pilot.pause()
        vista = app.query_one(TreadstoneView)
        assert [r.ok for r in vista.resultados] == [True, False]
        assert app.query_one("#q2", TabbedContent).active == "tab-bishop"
        assert isinstance(nucleo.recibidos[-1], ev.InspeccionarMemoria) and nucleo.recibidos[-1].linea == 1
        assert app.query_one(BishopView)

    probar(tmp_path, guion, escena)


def test_dibujar_memoria_con_punteros() -> None:
    from rich.console import Console

    datos = {"stack": [{"funcion": "main", "linea_actual": 9, "variables": [
        {"nombre": "lista", "tipo": "nodo_t *", "valor": "0xa0", "es_puntero": True, "direccion_apuntada": "0xa0"}]}],
        "heap": [{"direccion": "0xa0", "tamanio_bytes": 16, "contenido": "{valor=1, sig=0xb0}", "tipo": "struct nodo",
                  "punteros_referenciantes": ["lista"], "punteros_salientes": [{"campo": "sig", "destino": "0xb0"}]},
                 {"direccion": "0xb0", "tamanio_bytes": 16, "contenido": "{valor=2, sig=0x0}", "tipo": "struct nodo",
                  "punteros_referenciantes": []}],
        "fugas_detectadas": 1}
    consola = Console(width=120, record=True)
    consola.print(dibujar_memoria(datos))
    texto = consola.export_text()
    assert "0xa0 ──▶ #1" in texto and "sig ──▶ #2" in texto
    assert "sin referencias (fuga)" in texto and "1 fuga(s)" in texto


def test_marca_de_antipatron_con_el_nombre_que_usa_ripley(tmp_path: Path) -> None:
    feof = MappingProxyType({"herramienta": "antipatterns", "codigo": "0x4006h", "severidad": "error",
                             "mensaje": "while (!feof(f))", "archivo": "main.c", "linea": 5})
    guion: dict[type, list[ev.Evento]] = {ev.Auditar: [ev.HallazgosRecibidos("ripley", (feof,))]}

    async def escena(pilot: Any, app: ConstructApp, _nucleo: NucleoFalso) -> None:
        await pilot.press("f8")
        await pilot.pause()
        assert app.query_one(CodeEditor).marca(5) == ("! ", app.query_one(CodeEditor).marca(5)[1])  # type: ignore[index]
        assert "(spunkmeyer: 0x4006h)" in str(app.query_one(HallazgosView).query_one("OptionList").get_option_at_index(0).prompt)

    probar(tmp_path, guion, escena)
