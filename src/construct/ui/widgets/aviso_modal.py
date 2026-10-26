"""Aviso modal simple: el bloqueo de seguridad de kaneda (SPEC §3.1) y la explicación de yutani.ia."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class AvisoModal(ModalScreen[None]):
    DEFAULT_CSS = """
    AvisoModal { align: center middle; }
    AvisoModal > Vertical { width: 80%; max-width: 90; height: auto; max-height: 80%;
                            border: thick $primary; background: $surface; padding: 1 2; }
    AvisoModal.peligro > Vertical { border: thick $error; }
    AvisoModal VerticalScroll { height: auto; max-height: 25; }
    AvisoModal Button { margin-top: 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss(None)", "Cerrar")]

    def __init__(self, titulo: str, cuerpo: str, *, peligro: bool = False) -> None:
        super().__init__(classes="peligro" if peligro else "")
        self.titulo, self.cuerpo = titulo, cuerpo

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"[b]{self.titulo}[/b]")
            with VerticalScroll():
                yield Static(self.cuerpo, id="cuerpo")
            yield Button("Entendido", variant="primary")

    def on_button_pressed(self, _evento: Button.Pressed) -> None:
        self.dismiss(None)
