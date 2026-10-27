"""Artefactos de `bitmaps` y `sonido` de punta a punta (SPEC §3.2, RF-08, PLAN Fase 6).

El programa del alumno, enlazado con las librerías de la cátedra en la VM, genera un `.bmp` y un
`.wav`; la sincronización los baja a `.construct/artefactos/` y `media_preview` los puede leer.
Necesita `CONSTRUCT_IMAGEN_PRUEBA`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from construct.core import events as ev
from construct.ui import medios
from tests.vm_real import VMReal, correr, proyecto, vm_real  # noqa: F401 - fixture

BITMAP = """#include <bitmaps/libreria.h>

int main(void)
{
    bmp_t *img = bmp_crear(8, 4);
    if (img == NULL) {
        return 1;
    }
    for (int y = 0; y < 4; y++) {
        for (int x = 0; x < 8; x++) {
            coordenada_t c = {x, y};
            bmp_set_pixel(img, c, bmp_crear_color(255, (unsigned char)(x * 32), 0));
        }
    }
    int ok = bmp_guardar(img, "salida.bmp");
    bmp_destruir(img);
    return ok ? 0 : 1;
}
"""

SONIDO = """#include <sonido/libreria.h>

int main(void)
{
    wav_t *wav = wav_crear(8000, 1, 16, 8000);
    if (wav == NULL) {
        return 1;
    }
    int error = wav_generar_tono(wav, 440.0f, 1.0f, 0.5f);
    error = error || wav_guardar(wav, "tono.wav");
    wav_destruir(wav);
    return error;
}
"""


def artefactos(eventos: list[ev.Evento]) -> dict[str, Path]:
    return {e.ruta.name: e.ruta for e in eventos if isinstance(e, ev.ArtefactoDescargado)}


def generar_bmp(vm: VMReal, carpeta: Path) -> Path:
    async def prueba() -> dict[str, Path]:
        async with proyecto(vm, carpeta, {"main.c": BITMAP}) as t:
            await t.orquestador.ejecutar(ev.Ejecutar())
            assert t.de_tipo(ev.ProgramaTerminado) == [ev.ProgramaTerminado(0, None)]
            return artefactos(t.eventos)

    bajados = correr(prueba)
    assert isinstance(bajados, dict)
    return bajados["salida.bmp"]


def test_bmp_de_bitmaps(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    ruta = generar_bmp(vm_real, tmp_path)
    assert ruta == tmp_path / ".construct" / "artefactos" / "salida.bmp"
    imagen = medios.leer_bmp(ruta.read_bytes())
    assert (imagen.ancho, imagen.alto) == (8, 4)
    assert medios.dibujar_bmp(imagen).plain.count(medios.MEDIO_BLOQUE) == 16


@pytest.mark.xfail(strict=True, reason="bitmaps (9cce758) guarda los píxeles en orden RGB y el formato BMP es BGR: "
                                       "cualquier visor ve rojo y azul cambiados (bug de bitmaps, no de construct)")
def test_bmp_de_bitmaps_con_los_colores_del_formato(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    imagen = medios.leer_bmp(generar_bmp(vm_real, tmp_path).read_bytes())
    assert imagen.filas[0][0] == (255, 0, 0) and imagen.filas[3][7] == (255, 224, 0)


def test_wav_de_sonido(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> dict[str, Path]:
        async with proyecto(vm_real, tmp_path, {"main.c": SONIDO}) as t:
            await t.orquestador.ejecutar(ev.Ejecutar())
            assert t.de_tipo(ev.ProgramaTerminado) == [ev.ProgramaTerminado(0, None)]
            return artefactos(t.eventos)

    bajados = correr(prueba)
    assert isinstance(bajados, dict)
    sonido = medios.leer_wav(bajados["tono.wav"].read_bytes())
    assert (sonido.frecuencia, sonido.canales, sonido.bits) == (8000, 1, 16)
    assert abs(sonido.duracion_s - 1.0) < 0.01
    assert not sonido.silencio and sonido.recortadas == 0
    assert 0.3 < sonido.rms < 0.4  # un seno de amplitud 0,5 tiene RMS 0,354
