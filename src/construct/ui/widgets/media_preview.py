"""Visor de los artefactos `.bmp` (bitmaps) y `.wav` (sonido) que bajó la sincronización (SPEC §3.2).

El BMP se dibuja en el panel (TrueColor o 256 colores, según la terminal) y el WAV como forma de
onda con sus avisos; los dos se pueden abrir o reproducir con las herramientas del Host
(`platform.opener`).
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Static

from construct.ui import medios


class MediaPreview(Vertical):
    """Un tipo de artefacto (`.bmp` o `.wav`): muestra el último que llegó."""

    DEFAULT_CSS = """
    MediaPreview #titulo { height: auto; color: $text-muted; }
    MediaPreview VerticalScroll { height: 1fr; }
    MediaPreview Horizontal { height: auto; }
    MediaPreview Button { margin: 0 1 0 0; }
    """

    class Abrir(Message):
        def __init__(self, ruta: Path, reproducir: bool) -> None:
            super().__init__()
            self.ruta, self.reproducir = ruta, reproducir

    def __init__(self, extension: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.extension = extension
        self.ruta: Path | None = None
        self.profundidad = medios.profundidad_de_color(os.environ)

    def compose(self) -> ComposeResult:
        nombre = "imágenes .bmp (bitmaps)" if self.extension == ".bmp" else "sonidos .wav (sonido)"
        yield Static(f"Todavía no hay {nombre}: aparecen al ejecutar.", id="titulo")
        with VerticalScroll():
            yield Static(id="contenido")
        with Horizontal():
            yield Button("Abrir en el Host", id="abrir", disabled=True)
            if self.extension == ".wav":
                yield Button("Reproducir", id="reproducir", disabled=True)

    def mostrar(self, ruta: Path) -> None:
        self.ruta = ruta
        titulo, contenido = self.query_one("#titulo", Static), self.query_one("#contenido", Static)
        try:
            datos = ruta.read_bytes()
            if self.extension == ".bmp":
                imagen = medios.leer_bmp(datos)
                ancho = max(8, self.size.width - 2) if self.size.width else 60
                alto = max(4, self.size.height - 4) if self.size.height else 20
                contenido.update(medios.dibujar_bmp(medios.escalar(imagen, ancho, alto), self.profundidad))
                titulo.update(f"{escape(ruta.name)} · {imagen.ancho}×{imagen.alto}"
                              + ("" if self.profundidad == "truecolor" else " · 256 colores"))
            else:
                sonido = medios.leer_wav(datos)
                ancho = max(16, self.size.width - 2) if self.size.width else 60
                contenido.update(medios.dibujar_onda(sonido, ancho, 9))
                titulo.update(f"{escape(ruta.name)} · {escape(medios.describir_sonido(sonido))}")
        except (OSError, medios.ArchivoInvalido) as error:
            titulo.update(f"{escape(ruta.name)}")
            contenido.update(Text(str(error), style="red"))
        for boton in self.query(Button):
            boton.disabled = False

    def on_button_pressed(self, evento: Button.Pressed) -> None:
        evento.stop()
        if self.ruta is not None:
            self.post_message(self.Abrir(self.ruta, evento.button.id == "reproducir"))
