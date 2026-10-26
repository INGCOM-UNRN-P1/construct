"""Runner visual de las suites de treadstone (`p1_test.h`, SPEC §3.2 y §5 cuadrante 4).

Cada suite con `[PASS]`/`[FAIL]` y, si falló, lo que dijo: las pistas didácticas y el *diff* de
strings los escribe treadstone en su salida.
"""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from construct.core import events as ev

MAX_LINEAS = 40


def renglon(resultado: ev.ResultadoPrueba) -> str:
    marca = "[green][PASS][/green]" if resultado.ok else "[bold red][FAIL][/bold red]"
    texto = f"{marca} {escape(resultado.prueba)}"
    if not resultado.compila:
        texto += " [red](no compila)[/red]"
    elif resultado.senal:
        texto += f" [red](terminó por la señal {escape(resultado.senal)})[/red]"
    if not resultado.ok:
        lineas = resultado.salida.rstrip().splitlines()[-MAX_LINEAS:]
        texto += "".join(f"\n    {escape(linea)}" for linea in lineas)
    return texto


class TreadstoneView(VerticalScroll):
    DEFAULT_CSS = "TreadstoneView Static { width: 1fr; }"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.resultados: list[ev.ResultadoPrueba] = []

    def compose(self) -> ComposeResult:
        yield Static("[dim]Las pruebas (tests/test_*.c) se corren con F6.[/dim]")

    def empezar(self) -> None:
        self.resultados = []
        self.query_one(Static).update("[dim]treadstone: corriendo las pruebas…[/dim]")

    def agregar(self, resultado: ev.ResultadoPrueba) -> None:
        self.resultados.append(resultado)
        aprobadas = sum(1 for r in self.resultados if r.ok)
        cuerpo = "\n".join(renglon(r) for r in self.resultados)
        self.query_one(Static).update(f"{cuerpo}\n\n[b]{aprobadas}/{len(self.resultados)} suites aprobadas[/b]")
