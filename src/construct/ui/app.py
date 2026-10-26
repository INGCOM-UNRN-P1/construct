"""IDE de terminal de construct (`construct ui`, SPEC §5, PLAN Fase 4).

Cuatro cuadrantes: [1] editor/depurador, [2] memoria (bishop), registros y artefactos, [3]
hallazgos y [4] consola, entrada, pruebas y recursos; arriba, la barra de estado con el acelerador
real. La app no habla con la VM: manda comandos de `core/events.py` al `Nucleo` y dibuja los eventos
que vuelven. Todas las acciones están en la paleta (`Ctrl+P`) además de sus atajos (`ui/keymap.py`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from rich.markup import escape
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Grid, Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, RichLog, Select, Static, TabbedContent, TabPane, TextArea

from construct.core import events as ev
from construct.core import explicar as ia
from construct.core import tool_bridge as puente
from construct.platform import host, opener
from construct.ui import keymap
from construct.ui.nucleo import AbrirNucleo, Nucleo, NucleoVM
from construct.ui.widgets.aviso_modal import AvisoModal
from construct.ui.widgets.bishop_view import BishopView, RegistrosView
from construct.ui.widgets.code_editor import CodeEditor
from construct.ui.widgets.hal_postmortem_modal import HalPostMortemModal
from construct.ui.widgets.hallazgos_view import HallazgosView, es_bloqueante
from construct.ui.widgets.media_preview import MediaPreview
from construct.ui.widgets.resource_monitor import ResourceMonitor, cpu_porcentaje
from construct.ui.widgets.sync_conflict_modal import SyncConflictModal
from construct.ui.widgets.treadstone_view import TreadstoneView

TAREAS = {ev.Tarea.COMPILAR: "compilando (daedalus)", ev.Tarea.AUDITAR: "auditando (ripley)",
          ev.Tarea.AUTOFIX: "aplicando el autofix (gaff)", ev.Tarea.EJECUTAR: "ejecutando",
          ev.Tarea.PROBAR: "probando (treadstone)", ev.Tarea.SINCRONIZAR: "sincronizando",
          ev.Tarea.DEPURAR: "depurando (gdb)", ev.Tarea.MEMORIA: "tomando la foto de memoria (bishop)",
          ev.Tarea.REPRODUCTOR: "generando el reproductor (hal)"}


def archivos_editables(carpeta: Path) -> list[str]:
    fuentes = puente.fuentes_del_proyecto(carpeta)
    cabeceras = sorted(r.relative_to(carpeta).as_posix() for r in carpeta.rglob("*.h")
                       if not any(p.startswith(".") for p in r.relative_to(carpeta).parts))
    pruebas = [p for p in puente.pruebas_del_proyecto(carpeta) if p not in fuentes]
    return [*fuentes, *cabeceras, *pruebas]


class EventoDelNucleo(Message):
    def __init__(self, evento: ev.Evento) -> None:
        super().__init__()
        self.evento = evento


class ConstructApp(App[None]):
    TITLE = "construct"
    CSS = """
    #estado { height: 1; background: $primary-background; color: $text; padding: 0 1; }
    #cuadrantes { grid-size: 2 2; grid-columns: 1fr 1fr; grid-rows: 3fr 2fr; height: 1fr; }
    #cuadrantes > * { border: round $panel; }
    #cuadrantes > *:focus-within { border: round $accent; }
    #q1 #archivo { width: 1fr; }
    #q1 #pista { height: auto; max-height: 3; color: $warning; }
    #q4 RichLog { height: 1fr; }
    #q4 #linea-entrada { height: auto; }
    #q4 #linea-entrada Input { width: 1fr; }
    #q4 #entrada-programa { height: 1fr; }
    """

    def __init__(self, carpeta: Path, *, abrir_nucleo: AbrirNucleo = NucleoVM.abrir,
                 teclas: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self.teclas = dict(teclas) if teclas is not None else keymap.armar()
        super().__init__()
        self.carpeta = carpeta
        self._abrir_nucleo = abrir_nucleo
        self.nucleo: Nucleo | None = None
        self._comandos: asyncio.Queue[ev.Comando | None] = asyncio.Queue()
        self._atencion: asyncio.Task[None] | None = None
        self.breakpoints: frozenset[tuple[str, int]] = frozenset()
        self.depurando = False
        self.corriendo = False
        self.sync = "—"
        self.recursos = ""
        self.tarea = ""
        # Prioritarias: el editor no se queda con F6/F7 (selección) ni con Ctrl+K y compañía.
        for accion in keymap.ACCIONES:
            teclas_accion = self.teclas[accion.nombre]
            self._bindings.bind(",".join(teclas_accion), accion.nombre, accion.descripcion, accion.visible,
                                keymap.describir(teclas_accion[0]), priority=True)
        self._bindings.bind("ctrl+c", "interrumpir", "Interrumpir el programa", False, "Ctrl+C", priority=True)

    # --- Armado ---------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(escape("[construct] conectando con la VM…"), id="estado")
        with Grid(id="cuadrantes"):
            with Vertical(id="q1"):
                yield Select[str]([], prompt="Archivo", id="archivo", allow_blank=True)
                yield CodeEditor(self.carpeta, id="editor")
                yield Static("", id="pista")
            with TabbedContent(id="q2"):
                with TabPane("bishop", id="tab-bishop"):
                    yield BishopView()
                with TabPane("Registros", id="tab-registros"):
                    yield RegistrosView()
                with TabPane("BMP", id="tab-bmp"):
                    yield MediaPreview(".bmp", id="bmp")
                with TabPane("WAV", id="tab-wav"):
                    yield MediaPreview(".wav", id="wav")
            yield HallazgosView(id="q3")
            with TabbedContent(id="q4"):
                with TabPane("Consola", id="tab-consola"):
                    yield RichLog(id="consola", wrap=True, markup=True, highlight=False)
                    with Horizontal(id="linea-entrada"):
                        yield Input(placeholder="Entrada para el programa (Enter manda la línea)", id="entrada")
                        yield Button("Fin de la entrada", id="eof")
                with TabPane("Entrada", id="tab-entrada"):
                    yield Static("[dim]Entrada completa para depurar, tetsuo y bishop (que no son interactivos):[/dim]")
                    yield TextArea("", id="entrada-programa")
                with TabPane("Pruebas", id="tab-pruebas"):
                    yield TreadstoneView(id="pruebas")
                with TabPane("Recursos", id="tab-recursos"):
                    yield ResourceMonitor(id="recursos")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#q1").border_title = "[1] Editor / Depurador C11"
        self.query_one("#q2").border_title = "[2] Memoria (bishop) y artefactos"
        self.query_one("#q3").border_title = "[3] Hallazgos (ripley) y telemetría"
        self.query_one("#q4").border_title = "[4] Consola / treadstone"
        archivos = archivos_editables(self.carpeta)
        selector = self.query_one("#archivo", Select)
        selector.set_options([(a, a) for a in archivos])
        if archivos:
            selector.value = "main.c" if "main.c" in archivos else archivos[0]
        self.query_one(CodeEditor).focus()
        self._conectar()

    @work(exclusive=True, group="nucleo")
    async def _conectar(self) -> None:
        try:
            self.nucleo = await self._abrir_nucleo(self.carpeta, self._emitir, self._aviso)
        except (ValueError, OSError, ConnectionError) as error:
            self.query_one("#estado", Static).update(f"{escape('[construct]')} [bold red]sin VM:[/bold red] "
                                                     f"{escape(str(error))}")
            self.notify(str(error), title="No se pudo conectar con la VM", severity="error", timeout=15)
            return
        self._atencion = asyncio.create_task(self.nucleo.atender(self._comandos))
        self._actualizar_estado()

    def _emitir(self, evento: ev.Evento) -> None:
        self.post_message(EventoDelNucleo(evento))

    def _aviso(self, texto: str) -> None:
        self.call_from_thread(self.query_one("#estado", Static).update, escape(f"[construct] {texto}"))

    async def on_unmount(self) -> None:
        if self._atencion is not None:
            await self._comandos.put(None)
            try:
                await asyncio.wait_for(self._atencion, 10)
            except (TimeoutError, asyncio.CancelledError):
                self._atencion.cancel()
        if self.nucleo is not None:
            await self.nucleo.cerrar()

    # --- Estado -----------------------------------------------------------------------------------

    @property
    def editor(self) -> CodeEditor:
        return self.query_one(CodeEditor)

    @property
    def hallazgos(self) -> HallazgosView:
        return self.query_one(HallazgosView)

    def _actualizar_estado(self) -> None:
        vm = self.nucleo.descripcion if self.nucleo is not None else "conectando…"
        partes = [f"[b]{escape('[construct]')}[/b] VM: {escape(vm)}", "Perfil: estudiante", f"Sync: {escape(self.sync)}"]
        if self.recursos:
            partes.append(self.recursos)
        if self.depurando:
            partes.append("[yellow]depurando[/yellow]")
        if self.tarea:
            partes.append(f"[cyan]{escape(self.tarea)}…[/cyan]")
        self.query_one("#estado", Static).update(" | ".join(partes))

    def _consola(self, texto: str) -> None:
        self.query_one("#consola", RichLog).write(texto)

    def _entrada_programa(self) -> bytes:
        texto = self.query_one("#entrada-programa", TextArea).text
        return (texto if not texto or texto.endswith("\n") else texto + "\n").encode()

    def _enviar(self, comando: ev.Comando) -> None:
        if self.nucleo is None:
            self.notify("La VM todavía no está lista.", severity="warning")
            return
        self._comandos.put_nowait(comando)

    def _guardar(self) -> None:
        if self.editor.guardar():
            self.notify(f"{self.editor.ruta} guardado.", timeout=2)

    def _abrir(self, ruta: str, linea: int | None = None) -> None:
        if not (self.carpeta / ruta).is_file():
            return
        if self.editor.ruta != ruta:
            self._guardar()
            self.editor.abrir(ruta)
            selector = self.query_one("#archivo", Select)
            if selector.value != ruta:
                with selector.prevent(Select.Changed):
                    selector.value = ruta
            self._refrescar_marcas()
        if linea is not None:
            self.editor.cursor_location = (max(0, linea - 1), 0)
            self.editor.scroll_cursor_visible(center=True)

    def _refrescar_marcas(self) -> None:
        self.editor.mostrar_hallazgos(self.hallazgos.todos)
        self.editor.mostrar_breakpoints(l for a, l in self.breakpoints if a == self.editor.ruta)

    @on(Select.Changed, "#archivo")
    def _cambio_de_archivo(self, evento: Select.Changed) -> None:
        if isinstance(evento.value, str):
            self._abrir(evento.value)

    @on(TextArea.SelectionChanged, "#editor")
    def _pista(self, _evento: TextArea.SelectionChanged) -> None:
        hallazgos = self.editor.hallazgos_en(self.editor.cursor_location[0] + 1)
        texto = ""
        if hallazgos:
            h = hallazgos[0]
            texto = escape(f"[{puente.herramienta_de(h)} L{h.get('linea')}]: {h.get('mensaje')}")
            if puente.herramienta_de(h) == "spunkmeyer" and h.get("explicacion"):
                texto += f"\n[dim]{escape(str(h['explicacion']))}[/dim]"
        self.query_one("#pista", Static).update(texto)

    # --- Acciones (atajos y paleta) -----------------------------------------------------------

    def get_system_commands(self, screen: Screen[Any]) -> Iterable[SystemCommand]:
        for accion in keymap.ACCIONES:
            teclas = keymap.describir_todas(self.teclas[accion.nombre])
            yield SystemCommand(accion.descripcion, f"Atajo: {teclas}", getattr(self, f"action_{accion.nombre}"))
        yield SystemCommand("Ver los conflictos de la sincronización", "Elegir versión por archivo",
                            self._abrir_conflictos)
        yield from super().get_system_commands(screen)

    def action_compilar(self) -> None:
        self._guardar()
        self._enviar(ev.Compilar())

    def action_auditar(self) -> None:
        self._guardar()
        self._enviar(ev.Auditar())

    def action_probar(self) -> None:
        self._guardar()
        self.query_one(TreadstoneView).empezar()
        self.query_one("#q4", TabbedContent).active = "tab-pruebas"
        self._enviar(ev.Probar())

    def _bloqueado_por_kaneda(self) -> bool:
        prohibidas = [h for h in self.hallazgos.todos if es_bloqueante(h)]
        if not prohibidas:
            return False
        cuerpo = "\n\n".join(f"[b]{escape(str(h.get('archivo')))}:{h.get('linea')}[/b] "
                             f"[bold red]{escape(str(h.get('cwe') or h.get('codigo')))}[/bold red]\n"
                             f"{escape(str(h.get('mensaje')))}"
                             + (f"\n[green]→ {escape(str(h['sugerencia']))}[/green]" if h.get("sugerencia") else "")
                             for h in prohibidas)
        self.push_screen(AvisoModal("kaneda bloqueó la ejecución: hay funciones prohibidas", cuerpo, peligro=True))
        return True

    def _preparar_ejecucion(self) -> bool:
        self._guardar()
        if self._bloqueado_por_kaneda():
            return False
        self.query_one("#consola", RichLog).clear()
        self.query_one(ResourceMonitor).empezar()
        self.query_one("#q4", TabbedContent).active = "tab-consola"
        return True

    def action_ejecutar(self) -> None:
        if self.depurando:
            self._enviar(ev.Avanzar(ev.Paso.CONTINUAR))
            return
        if self._preparar_ejecucion():
            self.corriendo = True
            self._enviar(ev.Ejecutar())
            self.query_one("#entrada", Input).focus()

    def action_ejecutar_tetsuo(self) -> None:
        if self._preparar_ejecucion():
            self._enviar(ev.Ejecutar(modo=ev.ModoEjecucion.TETSUO, entrada=self._entrada_programa()))

    def action_depurar(self) -> None:
        if self._preparar_ejecucion():
            self._enviar(ev.Depurar(entrada=self._entrada_programa()))

    def action_terminar_depuracion(self) -> None:
        if self.depurando:
            self._enviar(ev.TerminarDepuracion())

    def action_breakpoint(self) -> None:
        if self.editor.ruta is not None:
            self._enviar(ev.AlternarBreakpoint(self.editor.ruta, self.editor.cursor_location[0] + 1))

    def action_paso_sobre(self) -> None:
        self._paso(ev.Paso.SIGUIENTE)

    def action_paso_dentro(self) -> None:
        self._paso(ev.Paso.ENTRAR)

    def _paso(self, paso: ev.Paso) -> None:
        if not self.depurando:
            self.notify("No hay una depuración en curso: empezala con Ctrl+F5 o desde la paleta.", severity="warning")
            return
        self._enviar(ev.Avanzar(paso))

    def action_memoria(self) -> None:
        if self.editor.ruta is not None:
            self._guardar()
            self._enviar(ev.InspeccionarMemoria(self.editor.ruta, self.editor.cursor_location[0] + 1,
                                                self._entrada_programa()))

    def action_sincronizar(self) -> None:
        self._guardar()
        self._enviar(ev.Sincronizar())
        self._enviar(ev.Auditar())  # ripley corre al guardar (SPEC §3.1)

    def action_autofix(self) -> None:
        self._guardar()
        self._enviar(ev.AplicarAutofix((self.editor.ruta,) if self.editor.ruta else ()))

    def action_salir(self) -> None:
        self._guardar()
        self.exit()

    def action_explicar(self) -> None:
        hallazgo = self.hallazgos.elegido
        if hallazgo is None or self.focused is self.editor:
            en_cursor = self.editor.hallazgos_en(self.editor.cursor_location[0] + 1)
            hallazgo = en_cursor[0] if en_cursor else hallazgo
        if hallazgo is None:
            self.notify("Elegí un hallazgo (en el panel o con el cursor en su línea).", severity="warning")
            return
        self._explicar(hallazgo)

    @work(thread=True, exclusive=True, group="ia")
    def _explicar(self, hallazgo: Mapping[str, Any]) -> None:
        self.call_from_thread(self.notify, "Consultando a yutani.ia…", timeout=3)
        try:
            texto = ia.explicar(self.carpeta, hallazgo)
        except ValueError as error:
            self.call_from_thread(self.notify, str(error), title="yutani.ia", severity="error", timeout=10)
            return
        titulo = f"{hallazgo.get('herramienta')} {hallazgo.get('codigo') or ''} — {hallazgo.get('archivo')}:{hallazgo.get('linea')}"
        self.call_from_thread(self.push_screen, AvisoModal(escape(titulo), escape(texto)))

    # --- Mensajes de los widgets ---------------------------------------------------------------

    @on(HallazgosView.Elegido)
    def _ir_al_hallazgo(self, mensaje: HallazgosView.Elegido) -> None:
        archivo, linea = mensaje.hallazgo.get("archivo"), mensaje.hallazgo.get("linea")
        if isinstance(archivo, str):
            self._abrir(archivo, linea if isinstance(linea, int) else None)
            self.editor.focus()

    @on(HallazgosView.Explicar)
    def _explicar_desde_panel(self, mensaje: HallazgosView.Explicar) -> None:
        self._explicar(mensaje.hallazgo)

    @on(HallazgosView.Autofix)
    def _autofix_desde_panel(self, _mensaje: HallazgosView.Autofix) -> None:
        self.action_autofix()

    @on(Input.Submitted, "#entrada")
    def _mandar_linea(self, evento: Input.Submitted) -> None:
        self._enviar(ev.EnviarEntrada((evento.value + "\n").encode()))
        self._consola(f"[dim]> {escape(evento.value)}[/dim]")
        evento.input.value = ""

    @on(Button.Pressed, "#eof")
    def _fin_de_entrada(self, _evento: Button.Pressed) -> None:
        self._enviar(ev.EnviarEntrada(b""))

    def action_interrumpir(self) -> None:
        """Ctrl+C con el programa corriendo lo interrumpe en la VM (nunca es una señal del Host); si no,
        copia en el editor o hace lo de siempre de Textual."""
        if self.corriendo:
            self._enviar(ev.Interrumpir())
        elif isinstance(self.focused, TextArea):
            self.focused.action_copy()
        else:
            self.action_help_quit()

    @on(MediaPreview.Abrir)
    def _abrir_en_el_host(self, mensaje: MediaPreview.Abrir) -> None:
        try:
            if mensaje.reproducir:
                opener.reproducir_wav(host(), mensaje.ruta)
            else:
                opener.abrir(host(), mensaje.ruta)
        except (opener.SinHerramientaDelHost, OSError) as error:
            self.notify(str(error), severity="error")

    def _abrir_conflictos(self) -> None:
        if self.nucleo is None or not self.nucleo.resolutor.conflictos():
            self.notify("No hay conflictos pendientes.")
            return

        def al_cerrar(resultado: str | None) -> None:
            if resultado == "sincronizar":
                self._enviar(ev.Sincronizar())
            elif resultado and resultado.startswith("editar:"):
                self._abrir(resultado.removeprefix("editar:"))
                self.notify("Borrá las marcas <<<<<<< ======= >>>>>>> dejando lo que va y guardá con Ctrl+S.",
                            timeout=10)

        self.push_screen(SyncConflictModal(self.nucleo.resolutor), al_cerrar)

    # --- Eventos del núcleo ------------------------------------------------------------------

    def on_evento_del_nucleo(self, mensaje: EventoDelNucleo) -> None:
        self.atender(mensaje.evento)

    def atender(self, evento: ev.Evento) -> None:
        match evento:
            case ev.TareaIniciada(tarea):
                self.tarea = TAREAS.get(tarea, str(tarea))
            case ev.TareaTerminada(tarea, ok, resumen):
                self.tarea = ""
                if tarea is ev.Tarea.EJECUTAR:
                    self.corriendo = False
                if resumen:
                    self.notify(resumen, severity="information" if ok else "warning", timeout=4)
            case ev.ErrorDeTarea(tarea, mensaje):
                self.tarea = ""
                self.corriendo = False
                self.notify(mensaje, title=f"No se pudo {TAREAS.get(tarea, str(tarea)).split(' (')[0]}",
                            severity="error", timeout=10)
            case ev.HallazgosRecibidos(fuente, hallazgos):
                self.hallazgos.actualizar(fuente, hallazgos)
                self._refrescar_marcas()
            case ev.SalidaDeHerramienta(herramienta, texto):
                self._consola(f"[dim]{escape(herramienta)}:[/dim] {escape(texto.rstrip())}")
            case ev.SalidaPrograma(datos, errores):
                texto = escape(datos.decode(errors="replace").rstrip("\n"))
                self._consola(f"[red]{texto}[/red]" if errores else texto)
            case ev.ProgramaTerminado(codigo, senal, detalle):
                self.corriendo = False
                fin = f"señal {senal}" if senal else f"código {codigo}"
                self._consola(f"[{'green' if codigo == 0 and not senal else 'bold red'}]— terminó con {fin}"
                              f"{(': ' + escape(detalle)) if detalle else ''}[/]")
            case ev.MuestraTelemetria(muestra):
                monitor = self.query_one(ResourceMonitor)
                anterior = monitor.ultima[0] if monitor.ultima else None
                monitor.agregar(muestra)
                self.recursos = f"RAM: {muestra.rss_kb / 1024:.1f} MB | CPU: {cpu_porcentaje(anterior, muestra):.0f}%"
            case ev.PostMortem(datos):
                self.push_screen(HalPostMortemModal(datos),
                                 lambda r: self._enviar(ev.GuardarReproductor()) if r == "reproductor" else None)
            case ev.ResultadoPrueba():
                self.query_one(TreadstoneView).agregar(evento)
            case ev.SincronizacionTerminada(_subidos, bajados, conflictos):
                self.sync = "CONFLICTOS" if conflictos else "OK"
                if bajados and self.editor.recargar_si_cambio():
                    self.notify(f"{self.editor.ruta} cambió en la VM (gaff): recargado.", timeout=4)
                if conflictos:
                    self._abrir_conflictos()
            case ev.ArtefactoDescargado(ruta):
                self._artefacto(ruta)
            case ev.BreakpointsActualizados(breakpoints):
                self.breakpoints = breakpoints
                self._refrescar_marcas()
            case ev.DepuracionDetenida():
                self.depurando = True
                if evento.archivo:
                    self._abrir(evento.archivo)
                self.editor.mostrar_detenido(evento.linea)
                self.query_one(RegistrosView).mostrar(evento)
                self.query_one("#q2", TabbedContent).active = "tab-registros"
            case ev.DepuracionTerminada(codigo, senal):
                self.depurando = False
                self.editor.mostrar_detenido(None)
                self.query_one(RegistrosView).mostrar(None)
                fin = f"señal {senal}" if senal else (f"código {codigo}" if codigo is not None else "detenida")
                self._consola(f"[yellow]— depuración terminada ({fin})[/yellow]")
            case ev.MemoriaBishop(datos):
                self.query_one(BishopView).mostrar(datos)
                self.query_one("#q2", TabbedContent).active = "tab-bishop"
        self._actualizar_estado()

    def _artefacto(self, ruta: Path) -> None:
        nombre = ruta.name
        if ruta.suffix.lower() in (".bmp", ".wav"):
            visor = self.query_one("#bmp" if ruta.suffix.lower() == ".bmp" else "#wav", MediaPreview)
            visor.mostrar(ruta)
            self.query_one("#q2", TabbedContent).active = f"tab-{ruta.suffix.lower().lstrip('.')}"
        self.notify(f"Artefacto nuevo: {nombre}", timeout=3)
