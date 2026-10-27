"""Integración real del perfil estudiante en el IDE (SPEC §3.1, PLAN Fase 6).

Cada prueba es un proyecto de alumno que pasa por el mismo orquestador que usa `construct ui`,
contra la VM de verdad: daedalus, ripley (gaff, spunkmeyer, kaneda), gaff fix, la telemetría,
hal, tetsuo, bishop, treadstone, GDB y mother doctor. Necesita `CONSTRUCT_IMAGEN_PRUEBA`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from construct.core import events as ev
from construct.core.doctor import consultar_guest
from construct.core.tool_bridge import herramienta_de
from construct.ui.widgets.hallazgos_view import es_bloqueante
from tests.vm_real import VMReal, correr, proyecto, vm_real  # noqa: F401 - fixture

SIN_PUNTO_Y_COMA = """#include <stdio.h>

int main(void)
{
    int n = 3
    printf("%d\\n", n);
    return 0;
}
"""

FEOF_Y_GETS = """#include <stdio.h>

int main(void)
{
    char linea[32];
    FILE *f = fopen("datos.txt", "r");
    if (f == NULL) {
        return 1;
    }
    while (!feof(f)) {
        fgetc(f);
    }
    fclose(f);
    gets(linea);
    return 0;
}
"""

MAL_FORMATEADO = "#include <stdio.h>\nint main(void){\nint x=1;\nif(x){printf(\"%d\\n\",x);}\nreturn 0;}\n"

RECURSOS = """#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    FILE *f = fopen("main.c", "r");
    int *v = malloc(10 * sizeof *v);
    if (f == NULL || v == NULL) {
        return 1;
    }
    v[0] = fgetc(f);
    printf("%d\\n", v[0] > 0);
    return 0;
}
"""

CRASH = """#include <stdio.h>

int main(void)
{
    int n = 0;
    int *p = NULL;
    if (scanf("%d", &n) == 1) {
        *p = n;
    }
    return 0;
}
"""

DESBORDE = """#include <stdlib.h>

int main(void)
{
    int *v = malloc(4 * sizeof *v);
    if (v == NULL) {
        return 1;
    }
    v[4] = 1;
    free(v);
    return 0;
}
"""

LISTA = """#include <stdio.h>
#include <stdlib.h>

typedef struct nodo {
    int valor;
    struct nodo *sig;
} nodo_t;

int main(void)
{
    nodo_t *lista = NULL;
    for (int i = 0; i < 3; i++) {
        nodo_t *nuevo = malloc(sizeof *nuevo);
        if (nuevo == NULL) {
            return 1;
        }
        nuevo->valor = i;
        nuevo->sig = lista;
        lista = nuevo;
    }
    printf("%d\\n", lista->valor);
    return 0;
}
"""

SUMA_H = "#ifndef SUMA_H\n#define SUMA_H\n\nint suma(int a, int b);\n\n#endif\n"
SUMA_C = '#include "suma.h"\n\nint suma(int a, int b)\n{\n    return a + b + (a == 7);\n}\n'
SUMA_MAIN = '#include <stdio.h>\n#include "suma.h"\n\nint main(void)\n{\n    printf("%d\\n", suma(1, 2));\n    return 0;\n}\n'
SUITE = """#include "p1_test.h"
#include "../suma.h"

TEST(test_suma_chicos)
{
    ASSERT_INT_EQ(5, suma(2, 3));
}

TEST(test_suma_con_siete)
{
    ASSERT_INT_EQ(8, suma(7, 1));
}

int main(void)
{
    TEST_SUITE_BEGIN("suma");
    RUN_TEST(test_suma_chicos);
    RUN_TEST(test_suma_con_siete);
    return TEST_REPORT();
}
"""


def test_daedalus_errores_con_la_linea_del_host(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, {"main.c": SIN_PUNTO_Y_COMA}) as t:
            await t.orquestador.ejecutar(ev.Compilar())
            errores = [h for h in t.hallazgos("daedalus") if h.get("severidad") == "error"]
            assert errores and all(h.get("archivo") == "main.c" for h in errores)
            assert {h.get("linea") for h in errores} & {5, 6}
            assert isinstance(t.ultimo, ev.TareaTerminada) and not t.ultimo.ok

    correr(prueba)


def test_ripley_antipatrones_y_bloqueo_de_kaneda(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, {"main.c": FEOF_Y_GETS, "datos.txt": "x\n"}) as t:
            await t.orquestador.ejecutar(ev.Auditar())
            hallazgos = t.hallazgos("ripley")
            herramientas = {herramienta_de(h) for h in hallazgos}
            assert {"gaff", "kaneda"} <= herramientas, herramientas
            assert any(h.get("linea") == 10 and "feof" in str(h.get("mensaje")) for h in hallazgos)  # lo marca gaff
            bloqueantes = [h for h in hallazgos if es_bloqueante(h)]
            assert bloqueantes and bloqueantes[0].get("linea") == 14

    correr(prueba)


def test_autofix_de_gaff_vuelve_al_host(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, {"main.c": MAL_FORMATEADO}) as t:
            await t.orquestador.ejecutar(ev.AplicarAutofix(("main.c",)))
            [sync] = t.de_tipo(ev.SincronizacionTerminada)
            assert sync.bajados and not sync.conflictos
            texto = (t.carpeta / "main.c").read_text(encoding="utf-8")
            assert texto != MAL_FORMATEADO and "if (x)" in texto

    correr(prueba)


def test_ejecucion_con_telemetria(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, {"main.c": RECURSOS}) as t:
            await t.orquestador.ejecutar(ev.Ejecutar())
            assert b"1" in b"".join(e.datos for e in t.de_tipo(ev.SalidaPrograma))
            assert t.de_tipo(ev.ProgramaTerminado) == [ev.ProgramaTerminado(0, None)]
            codigos = {(h["codigo"], h.get("archivo"), h.get("linea")) for h in t.hallazgos("telemetria")}
            assert {("TLM-FD-01", "main.c", 6), ("TLM-MEM-01", "main.c", 7)} <= codigos

    correr(prueba)


def test_crash_post_mortem_de_hal_y_reproductor(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, {"main.c": CRASH}) as t:
            cola: asyncio.Queue[ev.Comando | None] = asyncio.Queue()
            for comando in (ev.Ejecutar(), ev.EnviarEntrada(b"5\n"), ev.EnviarEntrada(b""), ev.GuardarReproductor(),
                            None):
                await cola.put(comando)
            await t.orquestador.atender(cola)
            assert t.de_tipo(ev.ProgramaTerminado)[0].senal == "SEGV"
            [post_mortem] = t.de_tipo(ev.PostMortem)
            assert post_mortem.datos.get("linea") == 8 and post_mortem.datos.get("explicacion")
            assert (t.carpeta / ".construct" / "artefactos" / "hal_reproducer.sh").is_file()

    correr(prueba)


@pytest.mark.xfail(strict=True, reason="bajo el sandbox de nostromo se pierde el informe de ASan y tetsuo no "
                                       "trae el detalle (problema de nostromo/tetsuo, PLAN Fase 3)")
def test_tetsuo_explica_el_desborde(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, {"main.c": DESBORDE}) as t:
            await t.orquestador.ejecutar(ev.Ejecutar(modo=ev.ModoEjecucion.TETSUO))
            assert isinstance(t.ultimo, ev.TareaTerminada) and not t.ultimo.ok
            assert any(h.get("linea") == 9 for h in t.hallazgos("tetsuo"))

    correr(prueba)


def test_bishop_y_depuracion_con_el_gdb_del_guest(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, {"main.c": LISTA}) as t:
            await t.orquestador.ejecutar(ev.InspeccionarMemoria("main.c", 21))
            [memoria] = t.de_tipo(ev.MemoriaBishop)
            assert len(memoria.datos["heap"]) == 3
            await t.orquestador.ejecutar(ev.AlternarBreakpoint("main.c", 18))
            await t.orquestador.ejecutar(ev.Depurar())
            [parada] = t.de_tipo(ev.DepuracionDetenida)
            assert (parada.archivo, parada.linea, parada.funcion) == ("main.c", 18, "main")
            assert parada.variables["i"] == "0"
            await t.orquestador.ejecutar(ev.TerminarDepuracion())
            assert t.de_tipo(ev.DepuracionTerminada)

    correr(prueba)


def test_suites_de_treadstone(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    archivos = {"suma.h": SUMA_H, "suma.c": SUMA_C, "main.c": SUMA_MAIN, "tests/test_suma.c": SUITE}

    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, archivos) as t:
            await t.orquestador.ejecutar(ev.Probar())
            [resultado] = t.de_tipo(ev.ResultadoPrueba)
            assert resultado.compila and not resultado.ok
            assert "test_suma_con_siete" in resultado.salida
            assert t.ultimo == ev.TareaTerminada(ev.Tarea.PROBAR, False, "0/1 suites aprobadas")

    correr(prueba)


def test_doctor_encadena_mother_del_guest(vm_real: VMReal, tmp_path: Path) -> None:  # noqa: F811
    async def prueba() -> None:
        async with proyecto(vm_real, tmp_path, {}) as t:
            resumen, *fallidos = await consultar_guest(t.orquestador.proyecto.canal)
            assert resumen.ok, (resumen.detalle, fallidos)

    correr(prueba)
