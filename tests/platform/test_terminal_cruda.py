"""Modo crudo de `construct jonesy` en POSIX y en Windows, con fakes (corre en cualquier sistema)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

import pytest

from construct.platform import terminal as t
from construct.platform.host_info import Sistema


class TermiosFalso:
    TCSANOW, TCSADRAIN = 0, 1

    def __init__(self) -> None:
        self.llamadas: list[tuple[Any, ...]] = []

    def tcgetattr(self, fd: int) -> list[str]:
        self.llamadas.append(("get", fd))
        return ["modo-cocido"]

    def tcsetattr(self, fd: int, cuando: int, modo: list[str]) -> None:
        self.llamadas.append(("set", fd, cuando, modo))


class TtyFalso:
    def __init__(self, termios: TermiosFalso) -> None:
        self.termios = termios

    def setraw(self, fd: int, cuando: int) -> None:
        self.termios.llamadas.append(("raw", fd, cuando))


def test_posix_entra_y_restaura_una_vez() -> None:
    termios = TermiosFalso()
    terminal = t.TerminalPosix(5, termios_mod=termios, tty_mod=TtyFalso(termios))
    with pytest.raises(RuntimeError), t.modo_crudo(terminal):
        raise RuntimeError("se cayó la VM")
    terminal.salir()  # otra vez: no hace nada
    assert termios.llamadas == [("get", 5), ("raw", 5, 0), ("set", 5, 1, ["modo-cocido"])]


class LoopFalso:
    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.manejadores: dict[int, Callable[[], None]] = {}

    def add_signal_handler(self, senal: int, funcion: Callable[[], None]) -> None:
        if self.falla:
            raise NotImplementedError
        self.manejadores[senal] = funcion

    def remove_signal_handler(self, senal: int) -> None:
        del self.manejadores[senal]


def test_posix_avisa_el_tamano_con_sigwinch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t.signal, "SIGWINCH", 28, raising=False)
    termios = TermiosFalso()
    terminal = t.TerminalPosix(0, termios_mod=termios, tty_mod=TtyFalso(termios), tamano=lambda: (40, 120))
    loop, avisos = LoopFalso(), []
    detener = terminal.observar_tamano(loop, lambda f, c: avisos.append((f, c)))  # type: ignore[arg-type]
    loop.manejadores[28]()
    assert avisos == [(40, 120)]
    detener()
    assert loop.manejadores == {}


def test_sin_sigwinch_se_sondea() -> None:
    tamanos = iter([(24, 80), (24, 80), (30, 100), (30, 100), (30, 100)])
    ultimo = [(30, 100)]

    def tamano() -> tuple[int, int]:
        ultimo[0] = next(tamanos, ultimo[0])
        return ultimo[0]

    async def prueba() -> list[tuple[int, int]]:
        avisos: list[tuple[int, int]] = []
        detener = t.TerminalWindows(tamano=tamano, sondeo_s=0.01).observar_tamano(
            asyncio.get_running_loop(), lambda f, c: avisos.append((f, c)))
        await asyncio.sleep(0.1)
        detener()
        return avisos

    assert asyncio.run(prueba()) == [(30, 100)]


def test_posix_sin_poder_registrar_la_senal_sondea(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t.signal, "SIGWINCH", 28, raising=False)

    async def prueba() -> None:
        termios = TermiosFalso()
        terminal = t.TerminalPosix(0, termios_mod=termios, tty_mod=TtyFalso(termios), tamano=lambda: (1, 1))
        loop = LoopFalso(falla=True)
        loop.create_task = asyncio.get_running_loop().create_task  # type: ignore[attr-defined]
        detener = terminal.observar_tamano(loop, lambda f, c: None)  # type: ignore[arg-type]
        detener()

    asyncio.run(prueba())


class ConsolaFalsa:
    def __init__(self, falla_en: Any = None) -> None:
        self.modos = {"in": 0x01F7, "out": 0x0003}  # los de una consola recién abierta
        self.cp = (850, 850)
        self.falla_en = falla_en

    def handle(self, cual: int) -> str:
        return "in" if cual == t.STD_INPUT_HANDLE else "out"

    def modo(self, handle: str) -> int:
        return self.modos[handle]

    def fijar_modo(self, handle: str, modo: int) -> None:
        if handle == self.falla_en:
            raise OSError(87, "parámetro incorrecto")
        self.modos[handle] = modo

    def paginas(self) -> tuple[int, int]:
        return self.cp

    def fijar_paginas(self, entrada: int, salida: int) -> None:
        self.cp = (entrada, salida)


def test_windows_modo_vt_y_restauracion() -> None:
    consola = ConsolaFalsa()
    terminal = t.TerminalWindows(consola=consola)
    with t.modo_crudo(terminal):
        entrada, salida = consola.modos["in"], consola.modos["out"]
        assert entrada & t.ENABLE_VIRTUAL_TERMINAL_INPUT
        assert not entrada & (t.ENABLE_LINE_INPUT | t.ENABLE_ECHO_INPUT | t.ENABLE_PROCESSED_INPUT)
        assert salida & t.ENABLE_VIRTUAL_TERMINAL_PROCESSING
        assert consola.cp == (t.UTF8, t.UTF8)
    assert consola.modos == {"in": 0x01F7, "out": 0x0003} and consola.cp == (850, 850)


def test_windows_sin_vt_restaura_y_explica() -> None:
    consola = ConsolaFalsa(falla_en="out")
    with pytest.raises(t.TerminalNoInteractiva, match="Windows Terminal"):
        t.TerminalWindows(consola=consola).entrar()
    assert consola.modos["in"] == 0x01F7 and consola.cp == (850, 850)


@pytest.mark.parametrize("sistema", [Sistema.LINUX, Sistema.WINDOWS, Sistema.MACOS])
def test_sin_terminal_no_hay_modo_crudo(sistema: Sistema) -> None:
    leer, escribir = os.pipe()
    try:
        with pytest.raises(t.TerminalNoInteractiva, match="terminal interactiva"):
            t.terminal_cruda(sistema, leer)
    finally:
        os.close(leer)
        os.close(escribir)
