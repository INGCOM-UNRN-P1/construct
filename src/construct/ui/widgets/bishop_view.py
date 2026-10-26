"""Memoria con bishop y registros de la depuración (SPEC §5 cuadrante 2, `bishop` View).

`dibujar_memoria` arma, con el snapshot de `bishop snapshot --json`, las cajas de cada frame del
Stack (variable, tipo y valor; los punteros con una flecha al bloque del Heap que apuntan) y los
bloques del Heap con su tamaño, contenido y si quedaron liberados o sin liberar.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from construct.core import events as ev

MAX_CONTENIDO = 80


def _campo(datos: Mapping[str, Any], *claves: str, defecto: Any = None) -> Any:
    """bishop manda las claves en castellano y en inglés; se toma la primera que esté."""
    for clave in claves:
        if datos.get(clave) is not None:
            return datos[clave]
    return defecto


def dibujar_memoria(datos: Mapping[str, Any]) -> RenderableType:
    heap = [b for b in _campo(datos, "heap", defecto=[]) if isinstance(b, dict)]
    numeros = {str(_campo(b, "direccion", "address")): i for i, b in enumerate(heap, 1)}

    def destino(direccion: Any) -> str:
        numero = numeros.get(str(direccion))
        return f" ──▶ #{numero}" if numero else (" ──▶ NULL" if str(direccion) in ("0x0", "(nil)") else "")

    partes: list[RenderableType] = []
    for frame in _campo(datos, "stack", "frames", defecto=[]):
        if not isinstance(frame, dict):
            continue
        funcion = escape(str(_campo(frame, "funcion", "function", defecto="?")))
        base = escape(str(_campo(frame, "direccion_base", "base_address", defecto="")))
        linea = _campo(frame, "linea_actual", "line")
        tabla = Table(title=f"STACK [b]{funcion}()[/b] · {base}" + (f" · línea {linea}" if linea else ""),
                      title_justify="left", show_header=True, header_style="dim", expand=True)
        tabla.add_column("variable")
        tabla.add_column("tipo", style="dim")
        tabla.add_column("valor")
        for v in _campo(frame, "variables", defecto=[]):
            valor = str(_campo(v, "valor", "value", defecto=""))
            if _campo(v, "es_puntero", "is_pointer", defecto=False):
                valor += destino(_campo(v, "direccion_apuntada", "target_address", defecto=valor))
            tabla.add_row(escape(str(_campo(v, "nombre", "name", defecto="?"))),
                          escape(str(_campo(v, "tipo", "type", defecto=""))), escape(valor))
        partes.append(tabla)

    if heap:
        tabla = Table(title="HEAP", title_justify="left", expand=True, header_style="dim")
        tabla.add_column("#", justify="right")
        tabla.add_column("bloque")
        tabla.add_column("bytes", justify="right")
        tabla.add_column("contenido")
        tabla.add_column("estado")
        for i, b in enumerate(heap, 1):
            liberado = _campo(b, "esta_liberado", "is_freed", defecto=False)
            referencias = _campo(b, "punteros_referenciantes", "pointers", defecto=[])
            salientes = ", ".join(f"{s.get('campo')} ──▶ #{numeros[str(s.get('destino'))]}"
                                  for s in _campo(b, "punteros_salientes", defecto=[])
                                  if isinstance(s, dict) and str(s.get("destino")) in numeros)
            estado = "[dim]liberado[/dim]" if liberado else (
                "activo" if referencias else "[bold red]sin referencias (fuga)[/bold red]")
            contenido = str(_campo(b, "contenido", "preview", defecto=""))
            contenido = escape(contenido if len(contenido) <= MAX_CONTENIDO else contenido[:MAX_CONTENIDO - 1] + "…")
            tabla.add_row(str(i), escape(f"<{_campo(b, 'direccion', 'address')}> {_campo(b, 'tipo', defecto='')}"),
                          str(_campo(b, "tamanio_bytes", "size", defecto="?")),
                          contenido + (f"\n[cyan]{escape(salientes)}[/cyan]" if salientes else ""), estado)
        partes.append(tabla)
    else:
        partes.append(Text("HEAP vacío: no hay bloques reservados en este punto.", style="dim"))

    fugas = _campo(datos, "fugas_detectadas", defecto=0) or 0
    activos = _campo(datos, "total_bytes_heap_activos", defecto=None)
    resumen = f"{len(heap)} bloque(s)" + (f", {activos} bytes activos" if activos is not None else "")
    partes.append(Text(resumen + (f", {fugas} fuga(s)" if fugas else ""), style="bold red" if fugas else "dim"))
    return Group(*partes)


def dibujar_registros(parada: ev.DepuracionDetenida | None) -> RenderableType:
    if parada is None:
        return Text("Sin depuración: poné un breakpoint (F9) y empezá con Ctrl+F5 o desde la paleta (Ctrl+P).",
                    style="dim")
    lugar = f"{parada.archivo or '?'}:{parada.linea or '?'}"
    tabla = Table(title=f"Detenido en {escape(lugar)} ({escape(parada.motivo)})", title_justify="left",
                  expand=True, header_style="dim")
    tabla.add_column("variable")
    tabla.add_column("valor")
    for nombre, valor in parada.variables.items():
        tabla.add_row(escape(nombre), escape(valor))
    return Group(tabla, _pila(parada.pila))


def _pila(pila: Sequence[ev.MarcoDePila]) -> RenderableType:
    texto = Text("Pila de llamadas\n", style="bold")
    for marco in pila:
        texto.append(f"  #{marco.nivel} {marco.funcion}", style="bold" if marco.nivel == 0 else "")
        texto.append(f"  {marco.archivo or '?'}:{marco.linea or '?'}\n", style="dim")
    return texto


class BishopView(VerticalScroll):
    DEFAULT_CSS = "BishopView Static { width: 1fr; }"

    def compose(self) -> ComposeResult:
        yield Static(Text("Memoria: elegí una línea y pedí la foto con F12 o desde la paleta (bishop).", style="dim"))

    def mostrar(self, datos: Mapping[str, Any]) -> None:
        self.query_one(Static).update(dibujar_memoria(datos))


class RegistrosView(VerticalScroll):
    DEFAULT_CSS = "RegistrosView Static { width: 1fr; }"

    def compose(self) -> ComposeResult:
        yield Static(dibujar_registros(None))

    def mostrar(self, parada: ev.DepuracionDetenida | None) -> None:
        self.query_one(Static).update(dibujar_registros(parada))
