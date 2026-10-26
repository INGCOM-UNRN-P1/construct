from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from construct.core import telemetry as t
from construct.core.protocolo import Tipo
from tests.conftest import FUENTES_AGENTE
from tests.test_canal_sesion import agente, correr


def muestras(*filas: tuple[int, int, int, int]) -> list[t.Muestra]:
    """(t_ms, cpu_ms, lectura, hilos_en_futex) con 1 hilo salvo que haya hilos en futex."""
    return [t.Muestra(ms, cpu, 1000, max(1, futex), futex, 3, lectura, 0) for ms, cpu, lectura, futex in filas]


def test_espera_activa() -> None:
    ocupado = muestras(*[(i * 100, i * 98, 0, 0) for i in range(30)])
    codigos = [h["codigo"] for h in t.analizar_muestras(ocupado)]
    assert codigos == ["TLM-CPU-01"]


def test_cpu_alta_con_e_s_no_es_espera_activa() -> None:
    con_lecturas = muestras(*[(i * 100, i * 98, i * 10, 0) for i in range(30)])
    assert t.analizar_muestras(con_lecturas) == []


def test_breve_no_alcanza() -> None:
    assert t.analizar_muestras(muestras(*[(i * 100, i * 99, 0, 0) for i in range(10)])) == []


def test_deadlock() -> None:
    trabado = [t.Muestra(i * 100, 50, 1000, 2, 2, 3, 0, 0) for i in range(30)]
    assert [h["codigo"] for h in t.analizar_muestras(trabado)] == ["TLM-LOCK-01"]


def test_addr2line() -> None:
    texto = "main\n/workspace/tp-1/main.c:14\ncopiar\n/workspace/tp-1/main.c:7 (discriminator 1)\n??\n??:0\n"
    ubicaciones = t.parsear_addr2line(texto, ["0x1", "0x2", "0x3"])
    assert ubicaciones["0x1"] == t.Ubicacion("main", "/workspace/tp-1/main.c", 14)
    assert ubicaciones["0x2"].linea == 7
    assert ubicaciones["0x3"] == t.Ubicacion(None, None, None)


def test_informe_ignora_lo_del_sistema() -> None:
    informe = {
        "archivos_abiertos": [{"ruta": "datos.txt", "modo": "r", "llamador": {"modulo": "./programa", "offset": "0x10"}}],
        "heap": {"bloques": 2, "bytes": 500, "sitios": [
            {"bloques": 1, "bytes": 472, "llamador": {"modulo": "/lib/x86_64-linux-gnu/libc.so.6", "offset": "0x9"}},
            {"bloques": 1, "bytes": 28, "llamador": {"modulo": "./programa", "offset": "0x20"}}]},
    }
    assert t.offsets_por_modulo(informe) == {"./programa": ["0x10", "0x20"]}
    ubicar = {("./programa", "0x10"): t.Ubicacion("main", "main.c", 6),
              ("./programa", "0x20"): t.Ubicacion("copiar", "main.c", 3)}
    hallazgos = t.analizar_informe(informe, ubicar)
    assert [(h["codigo"], h["archivo"], h["linea"]) for h in hallazgos] == [("TLM-FD-01", "main.c", 6),
                                                                            ("TLM-MEM-01", "main.c", 3)]
    assert "28 bytes" in hallazgos[1]["mensaje"] and "copiar()" in hallazgos[1]["mensaje"]
    assert all(h["explicacion"] for h in hallazgos)


def test_sin_fugas_no_hay_hallazgos() -> None:
    assert t.analizar_informe({"archivos_abiertos": [], "heap": {"bloques": 0, "bytes": 0, "sitios": []}}, {}) == []


# --- De punta a punta con el agente y el preload compilados en el Host ------------------------

ESPERA_ACTIVA = """#include <time.h>
int main(void)
{
    time_t inicio = time(NULL);
    volatile unsigned long n = 0;
    while (time(NULL) - inicio < 3) {
        n++;
    }
    return 0;
}
"""

FUGAS = """#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    FILE *f = fopen("datos.txt", "r");
    int *v = malloc(40);
    (void)f;
    (void)v;
    return 0;
}
"""


@pytest.fixture(scope="module")
def preload(agente_compilado: Path) -> Path:
    salida = agente_compilado.parent / "libconstruct_preload.so"
    subprocess.run(["make", "-s", "-C", str(FUENTES_AGENTE), f"PRELOAD={salida}", str(salida)], check=True)
    return salida


def compilar(tmp_path: Path, fuente: str) -> None:
    (tmp_path / "main.c").write_text(fuente, encoding="utf-8")
    subprocess.run(["cc", "-g", "-O0", "-o", str(tmp_path / "programa"), str(tmp_path / "main.c")], check=True)


def test_muestras_reales_detectan_espera_activa(agente_compilado: Path, tmp_path: Path) -> None:
    compilar(tmp_path, ESPERA_ACTIVA)

    async def prueba() -> list[dict[str, object]]:
        async with agente(agente_compilado, tmp_path) as canal:
            proceso = await canal.ejecutar(["./programa"], cwd=str(tmp_path), telemetria_ms=100)
            recibidas = []
            while (marco := await proceso.siguiente()) is not None:
                if marco.tipo is Tipo.TELEMETRIA:
                    recibidas.append(t.Muestra.desde(marco.json()))
            assert len(recibidas) >= 20 and recibidas[-1].rss_kb > 0 and recibidas[-1].fds >= 3
            return t.analizar_muestras(recibidas)

    assert [h["codigo"] for h in correr(prueba)] == ["TLM-CPU-01"]


@pytest.mark.skipif(shutil.which("addr2line") is None, reason="hace falta addr2line")
def test_preload_informa_archivo_abierto_y_fuga_con_su_linea(agente_compilado: Path, preload: Path,
                                                             tmp_path: Path) -> None:
    compilar(tmp_path, FUGAS)
    (tmp_path / "datos.txt").write_text("x")
    informe = tmp_path / "informe.json"

    async def prueba() -> dict[str, object]:
        async with agente(agente_compilado, tmp_path) as canal:
            resultado = await canal.correr(["./programa"], cwd=str(tmp_path),
                                           env={"LD_PRELOAD": str(preload), "CONSTRUCT_INFORME": str(informe)})
            assert resultado.ok
            return json.loads(await canal.leer_archivo(str(informe)))

    datos = correr(prueba)
    ubicaciones = {}
    for modulo, offsets in t.offsets_por_modulo(datos).items():
        ruta = str(tmp_path / modulo.removeprefix("./")) if not modulo.startswith("/") else modulo
        salida = subprocess.run(t.argv_addr2line(ruta, offsets), capture_output=True, text=True, check=True).stdout
        ubicaciones.update({(modulo, o): u for o, u in t.parsear_addr2line(salida, offsets).items()})
    hallazgos = t.analizar_informe(datos, ubicaciones, lambda r: Path(r).name if r else r)
    assert [(h["codigo"], h["archivo"], h["linea"]) for h in hallazgos] == [("TLM-FD-01", "main.c", 6),
                                                                            ("TLM-MEM-01", "main.c", 7)]
