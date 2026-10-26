"""Editor C11 con el gutter de `hallazgos.json` (SPEC §5 cuadrante 1, PLAN Fase 4).

Sobre el `TextArea` de Textual: el gutter se ensancha dos celdas y ahí va la marca de cada línea
(breakpoint, línea donde está detenida la depuración o la severidad del hallazgo más grave), y la
línea de un hallazgo se subraya con su color. Guarda siempre en UTF-8 y con fin de línea LF, así
las columnas de los hallazgos son las mismas en las tres plataformas.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.strip import Strip
from textual.widgets import TextArea

from construct.core.tool_bridge import herramienta_de

ANCHO_MARCA = 2
ORDEN = {"error": 0, "advertencia": 1, "estilo": 2, "info": 3}
MARCAS = {
    "error": ("● ", Style(color="red", bold=True)),
    "advertencia": ("▲ ", Style(color="yellow", bold=True)),
    "estilo": ("· ", Style(color="cyan", bold=True)),
    "info": ("i ", Style(color="blue")),
}
ANTIPATRON = ("! ", Style(color="magenta", bold=True))
"""spunkmeyer: el ícono pedagógico `[!]` de la SPEC."""
BREAKPOINT = ("◉ ", Style(color="red", bold=True))
DETENIDO = ("▶ ", Style(color="black", bgcolor="yellow", bold=True))
SUBRAYADOS = {"error": Style(underline=True, color="red"), "advertencia": Style(underline=True),
              "estilo": Style(underline=True, dim=True)}


def leer_fuente(ruta: Path) -> str:
    """El texto con LF (un archivo con CRLF de Windows se ve y se guarda igual que en Linux)."""
    return ruta.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def guardar_fuente(ruta: Path, texto: str) -> None:
    ruta.write_text(texto.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


class CodeEditor(TextArea):
    """Un archivo del proyecto a la vez; `ruta` es relativa a la carpeta del proyecto."""

    DEFAULT_CSS = """
    CodeEditor { height: 1fr; }
    """

    def __init__(self, carpeta: Path, **kwargs: Any) -> None:
        super().__init__("", show_line_numbers=True, tab_behavior="indent", soft_wrap=False, **kwargs)
        self.indent_width = 4
        self.carpeta = carpeta
        self.ruta: str | None = None
        self._guardado = ""
        self._hallazgos: dict[int, list[Mapping[str, Any]]] = {}
        self._breakpoints: set[int] = set()
        self._detenido: int | None = None

    # --- Archivo --------------------------------------------------------------------------------

    def abrir(self, ruta: str) -> None:
        texto = leer_fuente(self.carpeta / ruta)
        self.ruta, self._guardado = ruta, texto
        self.load_text(texto)
        self.refresh()

    @property
    def modificado(self) -> bool:
        return self.ruta is not None and self.text != self._guardado

    def guardar(self) -> bool:
        """Escribe el archivo si cambió; True si escribió."""
        if not self.modificado or self.ruta is None:
            return False
        guardar_fuente(self.carpeta / self.ruta, self.text)
        self._guardado = self.text
        return True

    def recargar_si_cambio(self) -> bool:
        """Después de un sync o de gaff: trae lo nuevo del disco si no hay cambios sin guardar."""
        if self.ruta is None or self.modificado or not (self.carpeta / self.ruta).is_file():
            return False
        texto = leer_fuente(self.carpeta / self.ruta)
        if texto == self._guardado:
            return False
        fila, columna = self.cursor_location
        self.abrir(self.ruta)
        self.cursor_location = (min(fila, self.document.line_count - 1), columna)
        return True

    # --- Marcas -----------------------------------------------------------------------------------

    def mostrar_hallazgos(self, hallazgos: Iterable[Mapping[str, Any]]) -> None:
        """Los hallazgos de este archivo (los demás se ignoran), por línea (1 = primera)."""
        self._hallazgos = {}
        for h in hallazgos:
            if h.get("archivo") == self.ruta and isinstance(h.get("linea"), int):
                self._hallazgos.setdefault(int(h["linea"]), []).append(h)
        self._invalidar()

    def mostrar_breakpoints(self, lineas: Iterable[int]) -> None:
        self._breakpoints = set(lineas)
        self._invalidar()

    def mostrar_detenido(self, linea: int | None) -> None:
        self._detenido = linea
        if linea is not None:
            self.cursor_location = (linea - 1, 0)
            self.scroll_cursor_visible(center=True)
        self._invalidar()

    def _invalidar(self) -> None:
        """El TextArea guarda las líneas ya dibujadas: sin vaciar ese caché no se ven las marcas nuevas."""
        cache = getattr(self, "_line_cache", None)
        if cache is not None:
            cache.clear()
        self.refresh()

    def hallazgos_en(self, linea: int) -> list[Mapping[str, Any]]:
        return sorted(self._hallazgos.get(linea, []), key=lambda h: ORDEN.get(str(h.get("severidad")), 9))

    def marca(self, linea: int) -> tuple[str, Style] | None:
        """La marca del gutter de una línea (1 = primera): depuración, breakpoint o hallazgo."""
        if linea == self._detenido:
            return DETENIDO
        if linea in self._breakpoints:
            return BREAKPOINT
        hallazgos = self.hallazgos_en(linea)
        if not hallazgos:
            return None
        if any(herramienta_de(h) == "spunkmeyer" for h in hallazgos):
            return ANTIPATRON
        return MARCAS.get(str(hallazgos[0].get("severidad")), MARCAS["info"])

    @property
    def gutter_width(self) -> int:
        ancho = super().gutter_width
        return ancho + ANCHO_MARCA if ancho else 0

    def get_line(self, line_index: int) -> Text:
        linea = super().get_line(line_index)
        hallazgos = self.hallazgos_en(line_index + 1)
        if hallazgos:
            estilo = SUBRAYADOS.get(str(hallazgos[0].get("severidad")))
            if estilo is not None:
                inicio = len(linea.plain) - len(linea.plain.lstrip())
                linea.stylize(estilo, inicio, len(linea.plain))
        return linea

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        if not self.show_line_numbers:
            return strip
        try:
            fila, seccion = self.wrapped_document._offset_to_line_info[y + self.scroll_offset.y]
        except (IndexError, AttributeError):
            return strip
        marca = self.marca(fila + 1) if seccion == 0 else None
        if marca is None:
            return strip
        texto, estilo = marca
        resto = strip.crop(ANCHO_MARCA, strip.cell_length)
        return Strip([Segment(texto, estilo), *resto], strip.cell_length)
