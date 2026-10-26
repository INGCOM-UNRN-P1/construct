from __future__ import annotations

from pathlib import Path

from construct.core import tool_bridge as puente


def test_fuentes_y_pruebas(tmp_path: Path) -> None:
    for ruta in ("main.c", "lib/lista.c", "tests/test_lista.c", "test_raiz.c", ".construct/x.c",
                 ".construct-build/y.c", ".oculto/z.c"):
        (tmp_path / ruta).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / ruta).write_text("")
    assert puente.fuentes_del_proyecto(tmp_path) == ["lib/lista.c", "main.c"]
    assert puente.pruebas_del_proyecto(tmp_path) == ["test_raiz.c", "tests/test_lista.c"]


def test_librerias_por_include() -> None:
    textos = ['#include <bitmaps/libreria.h>\n', '#include "p1_test.h"\n']
    assert puente.librerias_necesarias(textos) == ["/usr/local/lib/libp1_test.a", "/usr/local/lib/libbitmaps.a"]
    assert puente.librerias_necesarias(["#include <stdio.h>"]) == []


def test_argv_build_pone_las_bibliotecas_despues_de_los_fuentes() -> None:
    argv = puente.argv_build(["main.c"], "programa", ["/usr/local/lib/libsonido.a"])
    assert argv[:4] == ["daedalus", "compile", "main.c", "/usr/local/lib/libsonido.a"]
    assert argv[-3:] == ["-o", "programa", "--json"]


def test_argv_nostromo_y_prueba() -> None:
    argv = puente.argv_nostromo("./programa", ["a"], entrada="1\n", timeout_s=5)
    assert argv[:2] == ["nostromo", "run"] and argv[-2:] == ["./programa", "a"] and "--stdin" in argv
    compilar, binario = puente.argv_prueba("tests/test_lista.c", ["main.c", "lista.c"])
    assert binario == ".construct-build/test_lista"
    assert "main.c" not in compilar and "lista.c" in compilar and compilar[-2:] == ["-o", binario]
    assert compilar.index("lista.c") < compilar.index("-lp1_test")


def test_extraer_json_con_texto_alrededor() -> None:
    assert puente.extraer_json('{"a": 1}') == {"a": 1}
    assert puente.extraer_json('aviso: algo\n{"hallazgos": []}\nfin') == {"hallazgos": []}
    assert puente.extraer_json("sin json") is None
    assert puente.extraer_json("[1, 2]") is None


def test_rutas_de_los_hallazgos() -> None:
    vm = "/workspace/tp1-abcd1234"
    salida = puente.leer_salida('{"hallazgos": [{"archivo": "/workspace/tp1-abcd1234/src/main.c", "linea": 3},'
                                ' {"archivo": "./lista.c"}, {"archivo": "/usr/include/stdio.h"}, "basura"]}', vm)
    assert [h["archivo"] for h in salida.hallazgos] == ["src/main.c", "lista.c", "/usr/include/stdio.h"]


def test_senales_fatales() -> None:
    assert puente.es_crash("SEGV") and puente.es_crash("ABRT") and not puente.es_crash("INT")
    assert not puente.es_crash(None)
