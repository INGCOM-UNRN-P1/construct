"""Resolución interactiva de los conflictos de la fusión de 3 vías (RF-08, SPEC §7).

Para cada archivo en conflicto muestra la versión del Host y la de la VM lado a lado (con la base
común debajo) y deja elegir una, o resolverlo a mano en el editor (borrando las marcas). Sólo
trabaja en el Host: cuando no quedan conflictos, la app vuelve a sincronizar y eso cierra la fusión.
"""

from __future__ import annotations

from typing import ClassVar, Protocol

from rich.markup import escape
from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static

from construct.core.sync_manager import VersionesEnConflicto


class Resolutor(Protocol):
    def conflictos(self) -> list[str]: ...
    def versiones(self, ruta: str) -> VersionesEnConflicto: ...
    def elegir(self, ruta: str, eleccion: str) -> None: ...


def _codigo(texto: str | None) -> Syntax | str:
    if texto is None:
        return "[dim](en esta versión el archivo no existe)[/dim]"
    return Syntax(texto, "c", line_numbers=True, word_wrap=True, theme="ansi_dark")


class SyncConflictModal(ModalScreen[str | None]):
    """Devuelve `"sincronizar"` si quedaron todos resueltos, `"editar:<ruta>"` o None."""

    DEFAULT_CSS = """
    SyncConflictModal { align: center middle; }
    SyncConflictModal > Vertical { width: 95%; height: 90%; border: thick $warning; background: $surface;
                                   padding: 0 1; }
    SyncConflictModal #archivos { height: auto; max-height: 6; }
    SyncConflictModal #lados { height: 1fr; }
    SyncConflictModal #lados VerticalScroll { width: 1fr; border: round $panel; }
    SyncConflictModal #base { height: auto; max-height: 8; }
    SyncConflictModal Horizontal.botones { height: auto; }
    SyncConflictModal Button { margin-right: 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss(None)", "Cerrar")]

    def __init__(self, resolutor: Resolutor) -> None:
        super().__init__()
        self.resolutor = resolutor
        self.pendientes = resolutor.conflictos()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b]Conflictos entre tus cambios y los de la VM[/b] — elegí qué versión queda en cada archivo.")
            yield OptionList(*self.pendientes, id="archivos")
            with Horizontal(id="lados"):
                with VerticalScroll():
                    yield Static("[b]Tu versión (Host)[/b]")
                    yield Static(id="host")
                with VerticalScroll():
                    yield Static("[b]La de la VM[/b] (por ejemplo, de gaff)")
                    yield Static(id="vm")
            yield Static(id="base")
            with Horizontal(classes="botones"):
                yield Button("Quedarme con la mía", id="host-elegir", variant="primary")
                yield Button("Usar la de la VM", id="vm-elegir", variant="warning")
                yield Button("Resolver a mano", id="editar")
                yield Button("Cerrar", id="cerrar")

    def on_mount(self) -> None:
        if self.pendientes:
            self.query_one(OptionList).highlighted = 0
            self._mostrar(self.pendientes[0])

    @property
    def actual(self) -> str | None:
        indice = self.query_one(OptionList).highlighted
        return self.pendientes[indice] if indice is not None and indice < len(self.pendientes) else None

    def _mostrar(self, ruta: str) -> None:
        versiones = self.resolutor.versiones(ruta)
        self.query_one("#host", Static).update(_codigo(versiones.host))
        self.query_one("#vm", Static).update(_codigo(versiones.vm))
        base = "(sin base común)" if versiones.base is None else f"{len(versiones.base.splitlines())} líneas"
        self.query_one("#base", Static).update(f"[dim]Base común de {escape(ruta)}: {base}[/dim]")

    def on_option_list_option_highlighted(self, evento: OptionList.OptionHighlighted) -> None:
        if self.actual is not None:
            self._mostrar(self.actual)

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        ruta = self.actual
        if evento.button.id == "cerrar" or ruta is None:
            self.dismiss(None)
        elif evento.button.id == "editar":
            self.dismiss(f"editar:{ruta}")
        else:
            self.resolutor.elegir(ruta, "host" if evento.button.id == "host-elegir" else "vm")
            self.pendientes = self.resolutor.conflictos()
            if not self.pendientes:
                self.dismiss("sincronizar")
                return
            lista = self.query_one(OptionList)
            lista.clear_options()
            lista.add_options(self.pendientes)
            lista.highlighted = 0
            self._mostrar(self.pendientes[0])
