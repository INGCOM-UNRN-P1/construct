"""Artefactos de `bitmaps` (.bmp) y `sonido` (.wav) para `media_preview` (SPEC §3.2, PLAN Fase 4).

Sin Textual: se prueba solo. Los BMP son los de la cátedra (24 bits, sin comprimir) y se dibujan
con medios bloques `▀` (cada celda, dos píxeles: arriba el color del texto, abajo el del fondo), en
TrueColor o en la paleta de 256 colores si la terminal no da más. Los WAV son PCM de 8, 16, 24 o 32
bits; de cada uno salen la forma de onda, la duración y si hay *clipping* o silencio.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass

from rich.color import Color
from rich.style import Style
from rich.text import Text

MEDIO_BLOQUE = "▀"
UMBRAL_CLIPPING = 0.999
UMBRAL_SILENCIO = 0.001
"""Nivel RMS (de 0 a 1) por debajo del cual el audio se considera silencio."""


class ArchivoInvalido(ValueError):
    """El archivo no es un BMP o un WAV de los que se pueden mostrar."""


# --- BMP --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Imagen:
    ancho: int
    alto: int
    filas: tuple[tuple[tuple[int, int, int], ...], ...]
    """De arriba hacia abajo, cada píxel como (r, g, b)."""


def leer_bmp(datos: bytes) -> Imagen:
    if len(datos) < 54 or datos[:2] != b"BM":
        raise ArchivoInvalido("no es un BMP (falta la firma «BM»).")
    inicio, = struct.unpack_from("<I", datos, 10)
    ancho, alto, _planos, bits, compresion = struct.unpack_from("<iiHHI", datos, 18)
    if bits != 24 or compresion != 0:
        raise ArchivoInvalido(f"el BMP es de {bits} bits{' comprimido' if compresion else ''}: "
                              "sólo se muestran los de 24 bits sin comprimir (los de bitmaps.h).")
    if ancho <= 0 or alto == 0:
        raise ArchivoInvalido(f"el BMP mide {ancho}×{alto}.")
    paso = (ancho * 3 + 3) & ~3
    alto_abs = abs(alto)
    if inicio + paso * alto_abs > len(datos):
        raise ArchivoInvalido("el BMP está cortado: faltan píxeles.")
    filas = []
    for fila in range(alto_abs):
        base = inicio + paso * fila
        filas.append(tuple((datos[base + 3 * x + 2], datos[base + 3 * x + 1], datos[base + 3 * x])
                           for x in range(ancho)))
    if alto > 0:  # los BMP guardan las filas de abajo hacia arriba, salvo con alto negativo
        filas.reverse()
    return Imagen(ancho, alto_abs, tuple(filas))


def profundidad_de_color(entorno: Mapping[str, str]) -> str:
    """`truecolor` o `256`, según lo que anuncia la terminal (COLORTERM, Windows Terminal, …)."""
    if entorno.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return "truecolor"
    if entorno.get("WT_SESSION") or entorno.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm", "vscode"):
        return "truecolor"
    return "256"


_NIVELES = (0, 95, 135, 175, 215, 255)


def a_256(rgb: tuple[int, int, int]) -> int:
    """El índice de la paleta xterm de 256 colores más cercano (cubo 6×6×6 o escala de grises)."""
    def nivel(v: int) -> int:
        return min(range(6), key=lambda i: abs(_NIVELES[i] - v))

    r, g, b = rgb
    cubo = 16 + 36 * nivel(r) + 6 * nivel(g) + nivel(b)
    cr, cg, cb = (_NIVELES[nivel(v)] for v in rgb)
    gris = min(23, max(0, round((sum(rgb) / 3 - 8) / 10)))
    vg = 8 + 10 * gris
    distancia_cubo = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
    distancia_gris = (r - vg) ** 2 + (g - vg) ** 2 + (b - vg) ** 2
    return 232 + gris if distancia_gris < distancia_cubo else cubo


def _color(rgb: tuple[int, int, int], profundidad: str) -> Color:
    return Color.from_rgb(*rgb) if profundidad == "truecolor" else Color.from_ansi(a_256(rgb))


def escalar(imagen: Imagen, columnas: int, filas_texto: int) -> Imagen:
    """La imagen achicada (vecino más cercano, sin deformar) para entrar en columnas × filas_texto."""
    factor = min(1.0, columnas / imagen.ancho, (2 * filas_texto) / imagen.alto)
    ancho, alto = max(1, int(imagen.ancho * factor)), max(1, int(imagen.alto * factor))
    if (ancho, alto) == (imagen.ancho, imagen.alto):
        return imagen
    filas = tuple(tuple(imagen.filas[y * imagen.alto // alto][x * imagen.ancho // ancho] for x in range(ancho))
                  for y in range(alto))
    return Imagen(ancho, alto, filas)


def dibujar_bmp(imagen: Imagen, profundidad: str = "truecolor") -> Text:
    """Dos filas de píxeles por línea de texto: `▀` con el de arriba como texto y el de abajo como fondo."""
    texto = Text()
    for y in range(0, imagen.alto, 2):
        arriba = imagen.filas[y]
        abajo = imagen.filas[y + 1] if y + 1 < imagen.alto else None
        for x, pixel in enumerate(arriba):
            fondo = _color(abajo[x], profundidad) if abajo is not None else None
            texto.append(MEDIO_BLOQUE, Style(color=_color(pixel, profundidad), bgcolor=fondo))
        if y + 2 < imagen.alto:
            texto.append("\n")
    return texto


# --- WAV --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Sonido:
    frecuencia: int
    canales: int
    bits: int
    muestras: tuple[float, ...]
    """Del primer canal, normalizadas a [-1, 1]."""

    @property
    def duracion_s(self) -> float:
        return len(self.muestras) / self.frecuencia if self.frecuencia else 0.0

    @property
    def recortadas(self) -> int:
        """Muestras en el tope (o casi): *clipping*."""
        return sum(1 for m in self.muestras if abs(m) >= UMBRAL_CLIPPING)

    @property
    def rms(self) -> float:
        return math.sqrt(sum(m * m for m in self.muestras) / len(self.muestras)) if self.muestras else 0.0

    @property
    def silencio(self) -> bool:
        return self.rms < UMBRAL_SILENCIO


def _muestra(datos: bytes, posicion: int, bits: int) -> float:
    if bits == 8:  # sin signo, centrado en 128
        return (datos[posicion] - 128) / 128
    if bits == 16:
        return struct.unpack_from("<h", datos, posicion)[0] / 32768
    if bits == 24:
        crudo = int.from_bytes(datos[posicion:posicion + 3], "little", signed=True)
        return crudo / 8388608
    return struct.unpack_from("<i", datos, posicion)[0] / 2147483648


def leer_wav(datos: bytes) -> Sonido:
    if len(datos) < 12 or datos[:4] != b"RIFF" or datos[8:12] != b"WAVE":
        raise ArchivoInvalido("no es un WAV (faltan las firmas «RIFF» y «WAVE»).")
    posicion, formato, cuerpo = 12, None, None
    while posicion + 8 <= len(datos):
        nombre, largo = datos[posicion:posicion + 4], struct.unpack_from("<I", datos, posicion + 4)[0]
        contenido = datos[posicion + 8:posicion + 8 + largo]
        if nombre == b"fmt ":
            formato = contenido
        elif nombre == b"data":
            cuerpo = contenido
        posicion += 8 + largo + (largo & 1)
    if formato is None or cuerpo is None or len(formato) < 16:
        raise ArchivoInvalido("el WAV no tiene las secciones «fmt » y «data».")
    codigo, canales, frecuencia, _bytes_s, bloque, bits = struct.unpack_from("<HHIIHH", formato)
    if codigo not in (1, 0xFFFE) or bits not in (8, 16, 24, 32) or canales < 1:
        raise ArchivoInvalido(f"el WAV no es PCM de 8, 16, 24 o 32 bits (formato {codigo}, {bits} bits).")
    bloque = bloque or canales * bits // 8
    muestras = tuple(_muestra(cuerpo, i, bits) for i in range(0, len(cuerpo) - bloque + 1, bloque))
    return Sonido(frecuencia, canales, bits, muestras)


def envolvente(sonido: Sonido, columnas: int) -> list[tuple[float, float]]:
    """(mínimo, máximo) de cada columna de la forma de onda."""
    if not sonido.muestras or columnas <= 0:
        return []
    columnas = min(columnas, len(sonido.muestras))
    tramos = []
    for c in range(columnas):
        desde = c * len(sonido.muestras) // columnas
        hasta = max(desde + 1, (c + 1) * len(sonido.muestras) // columnas)
        tramo = sonido.muestras[desde:hasta]
        tramos.append((min(tramo), max(tramo)))
    return tramos


def dibujar_onda(sonido: Sonido, columnas: int, filas: int) -> Text:
    """La forma de onda con `█`; en rojo las columnas con *clipping*."""
    filas = max(2, filas)
    tramos = envolvente(sonido, columnas)
    lineas = [[" "] * len(tramos) for _ in range(filas)]
    recortes = {c for c, (bajo, alto) in enumerate(tramos) if max(abs(bajo), abs(alto)) >= UMBRAL_CLIPPING}

    def fila_de(valor: float) -> int:
        return min(filas - 1, max(0, round((1 - valor) / 2 * (filas - 1))))

    for c, (bajo, alto) in enumerate(tramos):
        for f in range(fila_de(alto), fila_de(bajo) + 1):
            lineas[f][c] = "█"
    texto = Text()
    for f, linea in enumerate(lineas):
        for c, caracter in enumerate(linea):
            texto.append(caracter, "bold red" if c in recortes else "cyan")
        if f + 1 < filas:
            texto.append("\n")
    return texto


def describir_sonido(sonido: Sonido) -> str:
    partes = [f"{sonido.duracion_s:.2f} s", f"{sonido.frecuencia} Hz", f"{sonido.bits} bits",
              "mono" if sonido.canales == 1 else f"{sonido.canales} canales"]
    if sonido.silencio:
        partes.append("silencio: ¿se escribieron las muestras?")
    if sonido.recortadas:
        partes.append(f"clipping en {sonido.recortadas} muestra(s): bajá la amplitud")
    return " · ".join(partes)
