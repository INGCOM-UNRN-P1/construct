"""BMP y WAV de la cátedra para media_preview (SPEC §3.2)."""

from __future__ import annotations

import math
import struct

import pytest
from rich.console import Console
from rich.style import Style
from rich.text import Text

from construct.ui import medios


def estilo(texto: Text, posicion: int) -> Style:
    return texto.get_style_at_offset(Console(), posicion)


def bmp(filas: list[list[tuple[int, int, int]]], *, abajo_arriba: bool = True, bits: int = 24) -> bytes:
    """Un BMP como el de bitmaps.h; `filas` de arriba hacia abajo en (r, g, b)."""
    alto, ancho = len(filas), len(filas[0])
    paso = (ancho * 3 + 3) & ~3
    cuerpo = b""
    for fila in (reversed(filas) if abajo_arriba else filas):
        linea = b"".join(bytes((b, g, r)) for r, g, b in fila)
        cuerpo += linea + b"\0" * (paso - len(linea))
    cabecera = struct.pack("<2sIHHI", b"BM", 54 + len(cuerpo), 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII", 40, ancho, alto if abajo_arriba else -alto, 1, bits, 0, len(cuerpo), 0, 0, 0, 0)
    return cabecera + dib + cuerpo


def wav(muestras: list[float], *, bits: int = 16, frecuencia: int = 8000) -> bytes:
    if bits == 16:
        cuerpo = b"".join(struct.pack("<h", max(-32768, min(32767, round(m * 32767)))) for m in muestras)
    else:
        cuerpo = bytes(max(0, min(255, round(m * 127 + 128))) for m in muestras)
    fmt = struct.pack("<HHIIHH", 1, 1, frecuencia, frecuencia * bits // 8, bits // 8, bits)
    datos = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(cuerpo)) + cuerpo
    return b"RIFF" + struct.pack("<I", len(datos)) + datos


ROJO, VERDE, AZUL, BLANCO = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)


@pytest.mark.parametrize("abajo_arriba", [True, False])
def test_leer_bmp_con_relleno_y_orden(abajo_arriba: bool) -> None:
    filas = [[ROJO, VERDE, AZUL], [BLANCO, ROJO, VERDE]]  # 3 píxeles: 9 bytes + 3 de relleno
    imagen = medios.leer_bmp(bmp(filas, abajo_arriba=abajo_arriba))
    assert (imagen.ancho, imagen.alto) == (3, 2)
    assert [list(f) for f in imagen.filas] == filas


@pytest.mark.parametrize(("datos", "mensaje"), [
    (b"no es nada", "firma"),
    (bmp([[ROJO]], bits=32), "32 bits"),
    (bmp([[ROJO, ROJO]])[:-4], "cortado"),
])
def test_bmp_invalidos(datos: bytes, mensaje: str) -> None:
    with pytest.raises(medios.ArchivoInvalido, match=mensaje):
        medios.leer_bmp(datos)


def test_dibujar_bmp_dos_pixeles_por_celda() -> None:
    imagen = medios.leer_bmp(bmp([[ROJO, VERDE], [AZUL, BLANCO], [VERDE, ROJO]]))
    texto = medios.dibujar_bmp(imagen)
    assert texto.plain == "▀▀\n▀▀"
    primero = estilo(texto, 0)
    assert primero.color is not None and primero.color.triplet == ROJO
    assert primero.bgcolor is not None and primero.bgcolor.triplet == AZUL
    ultimo = estilo(texto, 4)
    assert ultimo.bgcolor is None  # alto impar: la última fila no tiene píxel de abajo


def test_256_colores() -> None:
    assert medios.a_256(ROJO) == 196 and medios.a_256((0, 0, 0)) in (16, 232) and medios.a_256((128, 128, 128)) == 244
    texto = medios.dibujar_bmp(medios.leer_bmp(bmp([[ROJO], [AZUL]])), "256")
    color = estilo(texto, 0).color
    assert color is not None and color.number == 196


def test_profundidad_de_color() -> None:
    assert medios.profundidad_de_color({"COLORTERM": "truecolor"}) == "truecolor"
    assert medios.profundidad_de_color({"WT_SESSION": "x"}) == "truecolor"
    assert medios.profundidad_de_color({"TERM_PROGRAM": "Apple_Terminal"}) == "256"


def test_escalar_sin_deformar() -> None:
    imagen = medios.leer_bmp(bmp([[ROJO] * 40 for _ in range(20)]))
    chica = medios.escalar(imagen, 10, 50)
    assert (chica.ancho, chica.alto) == (10, 5)
    assert medios.escalar(imagen, 100, 100) is imagen


def test_leer_wav_y_describir() -> None:
    tono = [0.5 * math.sin(2 * math.pi * 440 * i / 8000) for i in range(8000)]
    sonido = medios.leer_wav(wav(tono))
    assert (sonido.frecuencia, sonido.bits, sonido.canales, len(sonido.muestras)) == (8000, 16, 1, 8000)
    assert sonido.duracion_s == 1 and not sonido.silencio and sonido.recortadas == 0
    assert medios.describir_sonido(sonido) == "1.00 s · 8000 Hz · 16 bits · mono"


def test_wav_con_clipping_y_silencio() -> None:
    saturado = medios.leer_wav(wav([1.5, -1.5, 0.2, 0.0]))
    assert saturado.recortadas == 2 and "clipping en 2" in medios.describir_sonido(saturado)
    mudo = medios.leer_wav(wav([0.0] * 100, bits=8))
    assert mudo.silencio and "silencio" in medios.describir_sonido(mudo)


def test_wav_invalido() -> None:
    with pytest.raises(medios.ArchivoInvalido, match="RIFF"):
        medios.leer_wav(b"MThd")


def test_dibujar_onda() -> None:
    sonido = medios.leer_wav(wav([1.0, -1.0] * 50 + [0.0] * 100))
    texto = medios.dibujar_onda(sonido, 4, 5)
    lineas = texto.plain.split("\n")
    assert len(lineas) == 5 and all(len(linea) == 4 for linea in lineas)
    assert [linea[0] for linea in lineas] == ["█"] * 5  # la primera columna va de -1 a 1
    assert [linea[3] for linea in lineas] == [" ", " ", "█", " ", " "]  # silencio: sólo el centro
    assert estilo(texto, 0) == estilo(texto, 1)
