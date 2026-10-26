"""Modal post-mortem de hal ante SIGSEGV, SIGABRT o SIGFPE (SPEC §3.1, RF-04).

Muestra la causa, el lugar, la explicación y qué corregir, con la pila del momento del fallo; el
botón «Guardar reproductor» devuelve `"reproductor"` para que la app pida `hal_reproducer.sh`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


def contenido(datos: Mapping[str, Any]) -> str:
    titulo = datos.get("causa_raiz_titulo") or datos.get("tipo_senal") or "El programa se cayó"
    lugar = f"{datos.get('archivo') or '?'}:{datos.get('linea') or '?'}"
    partes = [f"[bold red]{escape(str(titulo))}[/bold red]",
              f"en [b]{escape(lugar)}[/b], función {escape(str(datos.get('funcion_falla') or '?'))}"
              + (f" (señal {escape(str(datos['tipo_senal']))})" if datos.get("tipo_senal") else "")]
    if datos.get("variable_culpable"):
        partes.append(f"Variable involucrada: [b]{escape(str(datos['variable_culpable']))}[/b]")
    if datos.get("explicacion"):
        partes.append("\n" + escape(str(datos["explicacion"])))
    if datos.get("accion_correctiva"):
        partes.append(f"\n[green]{escape(str(datos['accion_correctiva']))}[/green]")
    frames = [f for f in datos.get("frames") or [] if isinstance(f, dict)]
    if frames:
        partes.append("\n[b]Pila al momento del fallo[/b]")
        for i, f in enumerate(frames):
            partes.append(f"  #{i} {escape(str(f.get('funcion') or f.get('function') or '?'))}  "
                          f"[dim]{escape(str(f.get('archivo') or f.get('file') or '?'))}:"
                          f"{escape(str(f.get('linea') or f.get('line') or '?'))}[/dim]")
    return "\n".join(partes)


class HalPostMortemModal(ModalScreen[str | None]):
    DEFAULT_CSS = """
    HalPostMortemModal { align: center middle; }
    HalPostMortemModal > Vertical { width: 90%; max-width: 100; height: auto; max-height: 90%;
                                    border: thick $error; background: $surface; padding: 1 2; }
    HalPostMortemModal VerticalScroll { height: auto; max-height: 30; }
    HalPostMortemModal Horizontal { height: auto; margin-top: 1; }
    HalPostMortemModal Button { margin-right: 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss(None)", "Cerrar")]

    def __init__(self, datos: Mapping[str, Any]) -> None:
        super().__init__()
        self.datos = datos

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b]Post-mortem de hal[/b]")
            with VerticalScroll():
                yield Static(contenido(self.datos), id="cuerpo")
            with Horizontal():
                yield Button("Guardar reproductor", id="reproductor", variant="warning")
                yield Button("Cerrar", id="cerrar", variant="primary")

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        self.dismiss("reproductor" if evento.button.id == "reproductor" else None)
