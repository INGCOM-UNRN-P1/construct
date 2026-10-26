"""Panel de hallazgos de daedalus, ripley, tetsuo y la telemetría (SPEC §5 cuadrante 3).

Cada fuente reemplaza sus hallazgos anteriores. Elegir uno lleva el editor a su línea y muestra el
detalle: la explicación pedagógica (spunkmeyer), el CWE (kaneda) y la sugerencia.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from construct.core.tool_bridge import herramienta_de
from construct.ui.widgets.code_editor import ORDEN

Hallazgo = Mapping[str, Any]
COLORES = {"error": "bold red", "advertencia": "yellow", "estilo": "cyan", "info": "blue"}
ETIQUETAS = {"error": "ERROR", "advertencia": "WARN", "estilo": "ESTILO", "info": "INFO"}


def lugar(h: Hallazgo) -> str:
    texto = str(h.get("archivo") or "")
    if h.get("linea"):
        texto += f":{h['linea']}"
    return texto


def renglon(h: Hallazgo) -> str:
    severidad = str(h.get("severidad", "info"))
    color = COLORES.get(severidad, "white")
    codigo = h.get("codigo") or h.get("id") or ""
    return (f"[{color}][{ETIQUETAS.get(severidad, severidad.upper())}][/{color}] {escape(lugar(h))} "
            f"[dim]({escape(herramienta_de(h))}: {escape(str(codigo))})[/dim]\n"
            f"  {escape(str(h.get('mensaje', '')))}")


def detalle(h: Hallazgo) -> str:
    partes = [f"[b]{escape(str(h.get('mensaje', '')))}[/b]"]
    if h.get("cwe"):
        partes.append(f"[bold red]Seguridad: {escape(str(h['cwe']))}[/bold red]")
    if h.get("explicacion"):
        partes.append(escape(str(h["explicacion"])))
    if h.get("sugerencia"):
        partes.append(f"[green]→ {escape(str(h['sugerencia']))}[/green]")
    if h.get("enlace"):
        partes.append(f"[dim]{escape(str(h['enlace']))}[/dim]")
    return "\n".join(partes)


def es_bloqueante(h: Hallazgo) -> bool:
    """kaneda con severidad error: función prohibida (gets, strcpy, system…). Bloquea la ejecución."""
    return herramienta_de(h) == "kaneda" and h.get("severidad") == "error"


class HallazgosView(Vertical):
    DEFAULT_CSS = """
    HallazgosView OptionList { height: 1fr; border: none; }
    HallazgosView #detalle { height: auto; max-height: 8; padding: 0 1; border-top: solid $panel; }
    HallazgosView Horizontal { height: auto; }
    HallazgosView Button { margin: 0 1 0 0; min-width: 10; }
    """

    class Elegido(Message):
        def __init__(self, hallazgo: Hallazgo) -> None:
            super().__init__()
            self.hallazgo = hallazgo

    class Explicar(Message):
        def __init__(self, hallazgo: Hallazgo) -> None:
            super().__init__()
            self.hallazgo = hallazgo

    class Autofix(Message):
        pass

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._por_fuente: dict[str, list[Hallazgo]] = {}
        self._lista: list[Hallazgo] = []

    def compose(self) -> ComposeResult:
        yield OptionList(id="lista")
        yield Static("Todavía no hay hallazgos: compilá (F7) o auditá (F8).", id="detalle")
        with Horizontal():
            yield Button("Explicar (F1)", id="explicar", variant="primary", disabled=True)
            yield Button("Autofix de gaff", id="autofix")

    @property
    def todos(self) -> list[Hallazgo]:
        return list(self._lista)

    @property
    def elegido(self) -> Hallazgo | None:
        indice = self.query_one(OptionList).highlighted
        return self._lista[indice] if indice is not None and indice < len(self._lista) else None

    def actualizar(self, fuente: str, hallazgos: Iterable[Hallazgo]) -> None:
        self._por_fuente[fuente] = list(hallazgos)
        self._lista = sorted((h for lista in self._por_fuente.values() for h in lista),
                             key=lambda h: (ORDEN.get(str(h.get("severidad")), 9), lugar(h)))
        lista = self.query_one(OptionList)
        lista.clear_options()
        lista.add_options([Option(renglon(h)) for h in self._lista])
        self.query_one("#explicar", Button).disabled = not self._lista
        detalle_vacio = (f"{len(self._lista)} hallazgo(s): elegí uno para ver el detalle." if self._lista
                         else "[green]✓ Sin hallazgos.[/green]")
        self.query_one("#detalle", Static).update(detalle_vacio)

    def explicacion(self, texto: str) -> None:
        self.query_one("#detalle", Static).update(texto)

    def on_option_list_option_highlighted(self, evento: OptionList.OptionHighlighted) -> None:
        h = self.elegido
        if h is not None:
            self.query_one("#detalle", Static).update(detalle(h))

    def on_option_list_option_selected(self, evento: OptionList.OptionSelected) -> None:
        h = self.elegido
        if h is not None:
            self.post_message(self.Elegido(h))

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        evento.stop()
        if evento.button.id == "explicar" and self.elegido is not None:
            self.post_message(self.Explicar(self.elegido))
        elif evento.button.id == "autofix":
            self.post_message(self.Autofix())
