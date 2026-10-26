"""Depuración por GDB/MI a través del agente real (el mismo camino que en la VM)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from construct.core.gdb_client import GuestGdbBackend, _detencion
from tests.test_canal_sesion import agente, correr

PROGRAMA = """#include <stdio.h>

static int doble(int x)
{
    int resultado = x * 2;
    return resultado;
}

int main(void)
{
    int n = 21;
    int d = doble(n);
    printf("%d\\n", d);
    return 3;
}
"""

pytestmark = pytest.mark.skipif(shutil.which("gdb") is None or shutil.which("cc") is None,
                                reason="hace falta gdb y cc en el Host de pruebas")


def test_detencion_con_codigo_en_octal() -> None:
    assert _detencion({"reason": "exited", "exit-code": "012"}).codigo_salida == 10
    assert _detencion({"reason": "exited-normally"}).codigo_salida == 0
    assert _detencion({"reason": "signal-received", "signal-name": "SIGSEGV"}).senal == "SEGV"


def test_breakpoint_paso_a_paso_pila_y_variables(agente_compilado: Path, tmp_path: Path) -> None:
    (tmp_path / "main.c").write_text(PROGRAMA, encoding="utf-8")
    subprocess.run(["cc", "-g", "-O0", "-o", str(tmp_path / "programa"), str(tmp_path / "main.c")], check=True)

    async def prueba() -> None:
        async with agente(agente_compilado, tmp_path) as canal:
            depurador = await GuestGdbBackend(canal, "./programa", cwd=str(tmp_path))
            assert await depurador.breakpoint("main.c", 12) == 1
            parada = await depurador.correr()
            assert (parada.motivo, parada.funcion, parada.linea) == ("breakpoint-hit", "main", 12)
            assert (await depurador.variables())["n"] == "21"
            parada = await depurador.entrar()  # F11: entra en doble()
            assert parada.funcion == "doble"
            pila = await depurador.pila()
            assert [m.funcion for m in pila] == ["doble", "main"]
            await depurador.siguiente()  # F10
            assert await depurador.evaluar("resultado") == "42"
            final = await depurador.continuar()
            assert final.termino and final.codigo_salida == 3
            await depurador.salir()

    correr(prueba)
