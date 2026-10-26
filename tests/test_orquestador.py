"""Orquestador de la interfaz contra el agente real, con herramientas falsas en el PATH del Guest."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from construct.core import events as ev
from construct.core import telemetry
from construct.core.config import Config
from construct.core.orquestador import Orquestador, Proyecto
from construct.core.sync_manager import SyncManager
from tests.conftest import FUENTES_AGENTE
from tests.test_canal_sesion import GIT, agente, correr

pytestmark = pytest.mark.skipif(shutil.which("cc") is None, reason="hace falta cc en el Host de pruebas")

# daedalus compile F… [-l…] -o SALIDA --json: compila con cc y responde con hallazgos como el real.
DAEDALUS = r"""#!/bin/sh
shift
fuentes=""; salida=programa
while [ $# -gt 0 ]; do
    case "$1" in
    -o) salida="$2"; shift 2 ;;
    --flags) shift 2 ;;
    --json|-l*) shift ;;
    *) fuentes="$fuentes $1"; shift ;;
    esac
done
if cc -g -O0 -o "$salida" $fuentes 2>/dev/null; then
    echo '{"hallazgos": []}'
else
    printf '{"hallazgos": [{"herramienta": "daedalus", "codigo": "C-ERR", "severidad": "error", "mensaje": "no compila", "archivo": "%s/main.c", "linea": 3}]}\n' "$PWD"
    exit 1
fi
"""

# hal run BINARIO … --json [--stdin ENTRADA]: devuelve lo que recibió por --stdin para verificarlo.
HAL = r"""#!/bin/sh
entrada=""
while [ $# -gt 0 ]; do
    [ "$1" = --stdin ] && entrada="$2"
    shift
done
printf '{"tipo_senal": "SIGSEGV", "archivo": "main.c", "linea": 5, "entrada": "%s"}\n' "$entrada"
"""

# tetsuo run … --json como lo devuelve cuando no pudo leer a los sanitizers (sin hallazgos, ok: false).
TETSUO = r"""#!/bin/sh
echo '{"ok": false, "passed": false, "hallazgos": [], "raw_output": "\n"}'
"""

ECO = """#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    char linea[64];
    int *fuga = malloc(16);
    (void)fuga;
    while (fgets(linea, sizeof linea, stdin) != NULL) {
        printf("eco: %s", linea);
    }
    return 0;
}
"""

CRASH = """#include <stdio.h>

int main(void)
{
    char linea[64];
    int *p = NULL;
    if (fgets(linea, sizeof linea, stdin) != NULL) {
        *p = 1;
    }
    return 0;
}
"""

ETERNO = """int main(void)
{
    volatile int n = 0;
    while (1) {
        n++;
    }
    return 0;
}
"""


@pytest.fixture
def herramientas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    carpeta = tmp_path / "herramientas"
    carpeta.mkdir()
    for nombre, guion in (("daedalus", DAEDALUS), ("hal", HAL), ("tetsuo", TETSUO)):
        (carpeta / nombre).write_text(guion, encoding="utf-8")
        (carpeta / nombre).chmod(0o755)
    monkeypatch.setenv("PATH", f"{carpeta}{os.pathsep}{os.environ['PATH']}")  # lo hereda el agente
    return carpeta


@asynccontextmanager
async def orquestador(binario: Path, tmp_path: Path, fuente: str | None = None,
                      ) -> AsyncIterator[tuple[Orquestador, list[ev.Evento], Path]]:
    carpeta = tmp_path / "proyecto"
    carpeta.mkdir()
    if fuente is not None:
        (carpeta / "main.c").write_text(fuente, encoding="utf-8")
    (tmp_path / "vm").mkdir()
    eventos: list[ev.Evento] = []
    async with agente(binario, tmp_path) as canal:
        sync = SyncManager(carpeta, GIT, canal, raiz_vm=str(tmp_path / "vm"))
        yield Orquestador(Proyecto(carpeta, Config(), canal, sync), eventos.append), eventos, Path(sync.vm)


def de_tipo[T](eventos: Sequence[ev.Evento], tipo: type[T]) -> list[T]:
    return [e for e in eventos if isinstance(e, tipo)]


async def atender(orq: Orquestador, *comandos: ev.Comando, pausa: float = 0) -> None:
    cola: asyncio.Queue[ev.Comando | None] = asyncio.Queue()
    atencion = asyncio.create_task(orq.atender(cola))
    for comando in comandos:
        await cola.put(comando)
        await asyncio.sleep(pausa)
    await cola.put(None)
    await atencion


def test_compilar_con_errores(agente_compilado: Path, herramientas: Path, tmp_path: Path) -> None:
    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path, "int main(void) { return x; }\n") as (orq, eventos, _):
            await orq.ejecutar(ev.Compilar())
            return eventos

    eventos = correr(prueba)
    assert eventos[0] == ev.TareaIniciada(ev.Tarea.COMPILAR)
    [recibidos] = de_tipo(eventos, ev.HallazgosRecibidos)
    assert recibidos.fuente == "daedalus"
    assert [(h["archivo"], h["linea"]) for h in recibidos.hallazgos] == [("main.c", 3)]  # ruta del Host
    with pytest.raises(TypeError):
        recibidos.hallazgos[0]["linea"] = 4  # type: ignore[index]
    assert eventos[-1] == ev.TareaTerminada(ev.Tarea.COMPILAR, False, "No compila (1 error)")


def test_ejecutar_con_entrada_y_telemetria(agente_compilado: Path, herramientas: Path, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    preload = tmp_path / "libconstruct_preload.so"
    subprocess.run(["make", "-s", "-C", str(FUENTES_AGENTE), f"PRELOAD={preload}", str(preload)], check=True)
    monkeypatch.setattr(telemetry, "PRELOAD", str(preload))

    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path, ECO) as (orq, eventos, _):
            await atender(orq, ev.Ejecutar(), ev.EnviarEntrada(b"hola\n"), ev.EnviarEntrada(b""), pausa=0.5)
            return eventos

    eventos = correr(prueba)
    assert b"".join(e.datos for e in de_tipo(eventos, ev.SalidaPrograma) if not e.errores) == b"eco: hola\n"
    assert de_tipo(eventos, ev.ProgramaTerminado) == [ev.ProgramaTerminado(0, None)]
    assert de_tipo(eventos, ev.MuestraTelemetria)
    telemetria = [e for e in de_tipo(eventos, ev.HallazgosRecibidos) if e.fuente == "telemetria"]
    assert [h["codigo"] for h in telemetria[0].hallazgos] == ["TLM-MEM-01"]
    assert eventos[-1] == ev.TareaTerminada(ev.Tarea.EJECUTAR, True, "el programa terminó con código 0")


def test_crash_abre_el_post_mortem_con_la_misma_entrada(agente_compilado: Path, herramientas: Path,
                                                        tmp_path: Path) -> None:
    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path, CRASH) as (orq, eventos, _):
            await atender(orq, ev.Ejecutar(), ev.EnviarEntrada(b"7"), ev.EnviarEntrada(b""), pausa=0.5)
            return eventos

    eventos = correr(prueba)
    assert de_tipo(eventos, ev.ProgramaTerminado)[0].senal == "SEGV"
    [post_mortem] = de_tipo(eventos, ev.PostMortem)
    assert post_mortem.datos["linea"] == 5 and post_mortem.datos["entrada"] == "7"
    assert not de_tipo(eventos, ev.TareaTerminada)[0].ok


def test_interrumpir(agente_compilado: Path, herramientas: Path, tmp_path: Path) -> None:
    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path, ETERNO) as (orq, eventos, _):
            await atender(orq, ev.Ejecutar(), ev.Interrumpir(), pausa=1)
            return eventos

    assert de_tipo(correr(prueba), ev.ProgramaTerminado)[0].senal == "INT"


def test_trabajos_en_orden_y_artefactos(agente_compilado: Path, herramientas: Path, tmp_path: Path) -> None:
    async def prueba() -> tuple[list[ev.Evento], Path]:
        async with orquestador(agente_compilado, tmp_path, "int main(void) { return 0; }\n") as (orq, eventos, vm):
            await orq.ejecutar(ev.Sincronizar(ev.AccionSync.SUBIR))
            (vm / "salida.bmp").write_bytes(b"BM")
            await atender(orq, ev.Compilar(), ev.Sincronizar(ev.AccionSync.ARTEFACTOS))
            return eventos, orq.proyecto.carpeta

    eventos, carpeta = correr(prueba)
    terminadas = [e.tarea for e in de_tipo(eventos, ev.TareaTerminada)]
    assert terminadas == [ev.Tarea.SINCRONIZAR, ev.Tarea.COMPILAR, ev.Tarea.SINCRONIZAR]
    [artefacto] = de_tipo(eventos, ev.ArtefactoDescargado)
    assert artefacto.ruta == carpeta / ".construct" / "artefactos" / "salida.bmp"


def test_sin_fuentes_es_un_error_de_tarea(agente_compilado: Path, herramientas: Path, tmp_path: Path) -> None:
    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path) as (orq, eventos, _):
            await orq.ejecutar(ev.Compilar())
            return eventos

    eventos = correr(prueba)
    assert eventos[-1] == ev.ErrorDeTarea(ev.Tarea.COMPILAR, "no hay archivos .c para compilar en esta carpeta.")


def test_tetsuo_que_no_pasa_sin_hallazgos_es_una_falla(agente_compilado: Path, herramientas: Path,
                                                      tmp_path: Path) -> None:
    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path, CRASH) as (orq, eventos, _):
            await orq.ejecutar(ev.Ejecutar(modo=ev.ModoEjecucion.TETSUO))
            return eventos

    eventos = correr(prueba)
    assert eventos[-1] == ev.TareaTerminada(ev.Tarea.EJECUTAR, False,
                                            "tetsuo: el programa falló, pero no trajo el detalle de los sanitizers")
    assert not de_tipo(eventos, ev.SalidaDeHerramienta)  # raw_output en blanco: nada que mostrar
