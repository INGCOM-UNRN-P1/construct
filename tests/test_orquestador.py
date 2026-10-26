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


# --- Depuración (GDB del Guest, acá el del Host de pruebas) y bishop --------------------------

LISTA = """#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    int n = 0;
    int suma = 0;
    if (scanf("%d", &n) != 1) {
        return 1;
    }
    for (int i = 0; i < n; i++) {
        suma += i;
    }
    printf("suma %d\\n", suma);
    return 4;
}
"""

BISHOP = r"""#!/bin/sh
read -r n
printf '{"schema_version": "1.0.0", "linea": %s, "total_bloques_heap": 1, "fugas_detectadas": 0, "entrada": "%s", "heap": []}\n' "$4" "$n"
"""


@pytest.mark.skipif(shutil.which("gdb") is None, reason="hace falta gdb en el Host de pruebas")
def test_depurar_breakpoints_pasos_y_final(agente_compilado: Path, herramientas: Path, tmp_path: Path) -> None:
    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path, LISTA) as (orq, eventos, _):
            await orq.ejecutar(ev.AlternarBreakpoint("main.c", 12))  # antes de empezar: queda pendiente
            await orq.ejecutar(ev.Depurar(entrada=b"3\n"))
            await orq.ejecutar(ev.Avanzar(ev.Paso.SIGUIENTE))
            await orq.ejecutar(ev.AlternarBreakpoint("main.c", 12))  # lo saca con la depuración en curso
            await orq.ejecutar(ev.Avanzar(ev.Paso.CONTINUAR))
            await orq.ejecutar(ev.Avanzar(ev.Paso.CONTINUAR))  # ya terminó: es un error de tarea
            return eventos

    eventos = correr(prueba)
    assert [e.breakpoints for e in de_tipo(eventos, ev.BreakpointsActualizados)] == [
        frozenset({("main.c", 12)}), frozenset()]
    primera, segunda = de_tipo(eventos, ev.DepuracionDetenida)
    assert (primera.motivo, primera.archivo, primera.linea, primera.funcion) == ("breakpoint-hit", "main.c", 12, "main")
    assert primera.variables["n"] == "3" and primera.pila[0].funcion == "main"
    assert segunda.linea == 11  # vuelve a la condición del for
    assert de_tipo(eventos, ev.DepuracionTerminada) == [ev.DepuracionTerminada(4, None)]
    assert b"suma 3" in b"".join(e.datos for e in de_tipo(eventos, ev.SalidaPrograma))
    assert ev.TareaTerminada(ev.Tarea.DEPURAR, True, "Depurando") in eventos
    assert isinstance(eventos[-1], ev.ErrorDeTarea) and "no hay una depuración" in eventos[-1].mensaje


@pytest.mark.skipif(shutil.which("gdb") is None, reason="hace falta gdb en el Host de pruebas")
def test_depurar_sin_breakpoints_frena_en_main(agente_compilado: Path, herramientas: Path, tmp_path: Path) -> None:
    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path, LISTA) as (orq, eventos, _):
            await orq.ejecutar(ev.Depurar(entrada=b"2\n"))
            await orq.ejecutar(ev.TerminarDepuracion())
            return eventos

    eventos = correr(prueba)
    [parada] = de_tipo(eventos, ev.DepuracionDetenida)
    assert parada.funcion == "main" and parada.linea == 6
    assert de_tipo(eventos, ev.DepuracionTerminada) == [ev.DepuracionTerminada(None, None)]


def test_memoria_con_bishop(agente_compilado: Path, herramientas: Path, tmp_path: Path) -> None:
    (herramientas / "bishop").write_text(BISHOP, encoding="utf-8")
    (herramientas / "bishop").chmod(0o755)

    async def prueba() -> list[ev.Evento]:
        async with orquestador(agente_compilado, tmp_path, LISTA) as (orq, eventos, _):
            await orq.ejecutar(ev.InspeccionarMemoria("main.c", 14, b"5\n"))
            return eventos

    eventos = correr(prueba)
    [memoria] = de_tipo(eventos, ev.MemoriaBishop)
    assert memoria.datos["linea"] == 14 and memoria.datos["entrada"] == "5"
    assert eventos[-1] == ev.TareaTerminada(ev.Tarea.MEMORIA, True, "bishop: 1 bloque(s) en el heap")
