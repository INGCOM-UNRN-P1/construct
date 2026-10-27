"""`construct jonesy`: la terminal cruda unida a una pty del agente real (RF-05)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from construct import comandos
from construct.consola import conectar_pty
from tests.test_canal_sesion import agente, correr

# Muestra el tamaño de su pty antes y después de una línea de entrada.
GUION = 'sleep 0.5; stty size; IFS= read -r linea; echo "recibido:$linea"; sleep 0.3; stty size'


class TerminalFalsa:
    """Registra entrar/salir y deja disparar un cambio de tamaño a mano."""

    def __init__(self) -> None:
        self.eventos: list[str] = []
        self.al_cambiar: Callable[[int, int], None] | None = None

    def entrar(self) -> None:
        self.eventos.append("entrar")

    def salir(self) -> None:
        self.eventos.append("salir")

    def tamano(self) -> tuple[int, int]:
        return 30, 100

    def observar_tamano(self, _loop: asyncio.AbstractEventLoop,
                        al_cambiar: Callable[[int, int], None]) -> Callable[[], None]:
        self.al_cambiar = al_cambiar
        return lambda: self.eventos.append("dejar de observar")


def test_pty_tamano_entrada_y_restauracion(agente_compilado: Path, tmp_path: Path) -> None:
    terminal = TerminalFalsa()
    leer, escribir = os.pipe()
    salida = tmp_path / "salida"

    async def prueba() -> int:
        async with agente(agente_compilado, tmp_path) as canal:
            proceso = await canal.ejecutar(["sh", "-c", GUION], pty=True, filas=24, columnas=80)

            async def teclear() -> None:
                await asyncio.sleep(1.0)
                assert terminal.al_cambiar is not None
                terminal.al_cambiar(40, 120)  # el usuario agrandó la ventana
                await asyncio.sleep(0.2)
                os.write(escribir, b"hola\r")  # en crudo, Enter llega como \r

            tecleo = asyncio.create_task(teclear())
            with os.fdopen(leer, "rb", buffering=0) as entrada, salida.open("wb") as destino:
                codigo = await conectar_pty(proceso, terminal, entrada=entrada, salida=destino)  # type: ignore[arg-type]
            await tecleo
            return codigo

    assert correr(prueba) == 0
    os.close(escribir)
    texto = salida.read_bytes().decode()
    assert "30 100" in texto  # el tamaño de la terminal del Host, no el inicial 24×80
    assert "recibido:hola" in texto
    assert texto.rstrip().endswith("40 120")
    assert terminal.eventos == ["entrar", "dejar de observar", "salir"]


def test_entorno_de_terminal() -> None:
    assert comandos.entorno_de_terminal({}) == {"TERM": "xterm-256color", "LANG": "C.UTF-8"}
    assert comandos.entorno_de_terminal({"WT_SESSION": "x"})["COLORTERM"] == "truecolor"
    assert comandos.entorno_de_terminal({"TERM": "xterm-kitty", "COLORTERM": "24bit"}) == {
        "TERM": "xterm-kitty", "LANG": "C.UTF-8", "COLORTERM": "24bit"}


def test_si_se_corta_el_canal_la_terminal_se_restaura(agente_compilado: Path, tmp_path: Path) -> None:
    terminal = TerminalFalsa()
    leer, escribir = os.pipe()

    async def prueba() -> None:
        async with agente(agente_compilado, tmp_path) as canal:
            proceso = await canal.ejecutar(["sleep", "30"], pty=True)

            async def cortar() -> None:
                await asyncio.sleep(0.5)
                await canal.cerrar()  # como si la VM se cayera

            corte = asyncio.create_task(cortar())
            with os.fdopen(leer, "rb", buffering=0) as entrada, (tmp_path / "salida").open("wb") as destino:
                try:
                    await asyncio.wait_for(conectar_pty(proceso, terminal, entrada=entrada,  # type: ignore[arg-type]
                                                        salida=destino), 10)
                except ConnectionError:
                    pass
            await corte

    correr(prueba)
    os.close(escribir)
    assert terminal.eventos[0] == "entrar" and terminal.eventos[-1] == "salir"


def test_entrar_sin_comando_es_un_bash_de_login() -> None:
    assert comandos.argv_entrar([]) == ["bash", "--login", "-i"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="hace falta bash")
def test_entrar_con_comando_carga_el_entorno_y_respeta_los_argumentos(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "perfil.sh").write_text('export P1_PRUEBA="activo"\n', encoding="utf-8")
    monkeypatch.setattr(comandos, "PERFIL_ENTORNO", "./perfil.sh")
    argv = comandos.argv_entrar(["sh", "-c", 'printf "%s|%s" "$P1_PRUEBA" "$0"', "con espacios y 'comillas'"])
    salida = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, check=True).stdout
    assert salida == "activo|con espacios y 'comillas'"


def test_entrar_usa_la_pty_de_la_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas: list[tuple[list[str], str]] = []

    async def pty(argv: list[str], editado: str) -> int:
        llamadas.append((argv, editado))
        return 0

    monkeypatch.setattr(comandos, "_pty_en_la_vm", pty)
    assert asyncio.run(comandos.entrar(["ayuda"])) == 0
    assert asyncio.run(comandos.jonesy(["main.c"])) == 0
    assert llamadas == [(comandos.argv_entrar(["ayuda"]), "la terminal de la VM"), (["jonesy", "main.c"], "jonesy")]
