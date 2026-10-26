"""Consola del Host (PLAN §2): UTF-8 en todas las plataformas.

La consola de Windows arranca en cp1252/cp850 y rompe las tildes y la «ñ» de los mensajes; acá
se fuerza UTF-8 antes de imprimir cualquier cosa. También vive acá el modo crudo de
`construct jonesy` (Fase 5): `termios` en POSIX y la API de consola en Windows.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import importlib
import os
import shutil
import signal
import sys
from collections.abc import Callable, Iterator
from typing import Any, TextIO

from construct.platform.host_info import Sistema


def preparar_consola(flujos: tuple[TextIO, ...] | None = None) -> None:
    """Reconfigura stdout y stderr a UTF-8 (sin romper si no son flujos de texto reconfigurables)."""
    for flujo in flujos if flujos is not None else (sys.stdout, sys.stderr):
        reconfigurar = getattr(flujo, "reconfigure", None)
        if reconfigurar is None or (getattr(flujo, "encoding", "") or "").lower().replace("-", "") == "utf8":
            continue
        try:
            reconfigurar(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue  # Flujo ya usado en modo binario o cerrado: se deja como está.


# --- Modo crudo para `construct jonesy` (PLAN Fase 5, RF-05) -----------------------------------
#
# La terminal del Host pasa a modo crudo y cada tecla (Ctrl+C incluido, como el byte 0x03) viaja tal
# cual a la terminal (pty) del proceso en la VM; el tamaño se le avisa al principio y en cada cambio.
# Restaurar la terminal es responsabilidad de quien entra: siempre con `modo_crudo` (try/finally).

TamanoTerminal = tuple[int, int]
"""(filas, columnas)."""


class TerminalNoInteractiva(ValueError):
    """La entrada no es una terminal (por ejemplo, una tubería): no hay modo crudo posible."""


def tamano_actual(predeterminado: TamanoTerminal = (24, 80)) -> TamanoTerminal:
    columnas, filas = shutil.get_terminal_size((predeterminado[1], predeterminado[0]))
    return filas, columnas


def _sondear(loop: asyncio.AbstractEventLoop, tamano: Callable[[], TamanoTerminal],
             al_cambiar: Callable[[int, int], None], cada_s: float) -> Callable[[], None]:
    """Avisa los cambios de tamaño mirando cada `cada_s` (Windows no tiene SIGWINCH)."""
    async def vigilar() -> None:
        ultimo = tamano()
        while True:
            await asyncio.sleep(cada_s)
            actual = tamano()
            if actual != ultimo:
                ultimo = actual
                al_cambiar(*actual)

    tarea = loop.create_task(vigilar())

    def detener() -> None:
        tarea.cancel()

    return detener


class TerminalPosix:
    """Linux y macOS: `termios` + `tty.setraw`, y SIGWINCH para el tamaño."""

    def __init__(self, descriptor: int, *, termios_mod: Any = None, tty_mod: Any = None,
                 tamano: Callable[[], TamanoTerminal] = tamano_actual, sondeo_s: float = 0.25) -> None:
        self.descriptor = descriptor
        self._termios = termios_mod if termios_mod is not None else importlib.import_module("termios")
        self._tty = tty_mod if tty_mod is not None else importlib.import_module("tty")
        self.tamano = tamano
        self.sondeo_s = sondeo_s
        self._previo: Any = None

    def entrar(self) -> None:
        self._previo = self._termios.tcgetattr(self.descriptor)
        self._tty.setraw(self.descriptor, self._termios.TCSANOW)

    def salir(self) -> None:
        if self._previo is not None:
            self._termios.tcsetattr(self.descriptor, self._termios.TCSADRAIN, self._previo)
            self._previo = None

    def observar_tamano(self, loop: asyncio.AbstractEventLoop,
                        al_cambiar: Callable[[int, int], None]) -> Callable[[], None]:
        senal = getattr(signal, "SIGWINCH", None)
        if senal is not None:
            try:
                loop.add_signal_handler(senal, lambda: al_cambiar(*self.tamano()))
            except (RuntimeError, ValueError, NotImplementedError):
                pass  # fuera del hilo principal: queda el sondeo
            else:
                def detener() -> None:
                    loop.remove_signal_handler(senal)

                return detener
        return _sondear(loop, self.tamano, al_cambiar, self.sondeo_s)


# Windows: modos de la consola (SetConsoleMode) y su página de códigos.
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
ENABLE_PROCESSED_OUTPUT = 0x0001
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
UTF8 = 65001


class ConsolaWin32:
    """Las llamadas de kernel32 que usa `TerminalWindows` (los tests le pasan una falsa)."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self._wintypes = wintypes
        self._k = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009 - sólo existe en Windows
        self._k.GetStdHandle.restype = wintypes.HANDLE

    def handle(self, cual: int) -> Any:
        return self._k.GetStdHandle(cual)

    def modo(self, handle: Any) -> int:
        valor = self._wintypes.DWORD()
        if not self._k.GetConsoleMode(handle, ctypes.byref(valor)):
            raise TerminalNoInteractiva("la entrada o la salida no es una consola de Windows.")
        return int(valor.value)

    def fijar_modo(self, handle: Any, modo: int) -> None:
        if not self._k.SetConsoleMode(handle, modo):
            raise OSError(getattr(ctypes, "get_last_error")(), "SetConsoleMode falló")  # noqa: B009 - sólo en Windows

    def paginas(self) -> tuple[int, int]:
        return int(self._k.GetConsoleCP()), int(self._k.GetConsoleOutputCP())

    def fijar_paginas(self, entrada: int, salida: int) -> None:
        self._k.SetConsoleCP(entrada)
        self._k.SetConsoleOutputCP(salida)


class TerminalWindows:
    """Windows 10/11: secuencias VT en la entrada y en la salida, sin eco ni línea ni Ctrl+C local."""

    def __init__(self, *, consola: Any = None, tamano: Callable[[], TamanoTerminal] = tamano_actual,
                 sondeo_s: float = 0.25) -> None:
        self._consola = consola
        self.tamano = tamano
        self.sondeo_s = sondeo_s
        self._previo: tuple[Any, int, Any, int, tuple[int, int]] | None = None

    @property
    def consola(self) -> Any:
        if self._consola is None:
            self._consola = ConsolaWin32()
        return self._consola

    def entrar(self) -> None:
        c = self.consola
        entrada, salida = c.handle(STD_INPUT_HANDLE), c.handle(STD_OUTPUT_HANDLE)
        modo_entrada, modo_salida = c.modo(entrada), c.modo(salida)
        paginas = c.paginas()
        self._previo = (entrada, modo_entrada, salida, modo_salida, paginas)
        crudo = (modo_entrada & ~(ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_PROCESSED_INPUT)
                 | ENABLE_VIRTUAL_TERMINAL_INPUT)
        try:
            c.fijar_paginas(UTF8, UTF8)
            c.fijar_modo(entrada, crudo)
            c.fijar_modo(salida, modo_salida | ENABLE_VIRTUAL_TERMINAL_PROCESSING | ENABLE_PROCESSED_OUTPUT)
        except OSError:
            self.salir()
            raise TerminalNoInteractiva("esta consola no acepta secuencias VT: usá Windows Terminal "
                                        "(o Windows 10 1809 o posterior).") from None

    def salir(self) -> None:
        if self._previo is None:
            return
        entrada, modo_entrada, salida, modo_salida, paginas = self._previo
        self._previo = None
        c = self.consola
        for handle, modo in ((entrada, modo_entrada), (salida, modo_salida)):
            with contextlib.suppress(OSError):
                c.fijar_modo(handle, modo)
        c.fijar_paginas(*paginas)

    def observar_tamano(self, loop: asyncio.AbstractEventLoop,
                        al_cambiar: Callable[[int, int], None]) -> Callable[[], None]:
        return _sondear(loop, self.tamano, al_cambiar, self.sondeo_s)


TerminalCruda = TerminalPosix | TerminalWindows


def terminal_cruda(sistema: Sistema, descriptor: int = 0) -> TerminalCruda:
    """La terminal del Host para el modo crudo; TerminalNoInteractiva si la entrada no es una terminal."""
    if not os.isatty(descriptor):
        raise TerminalNoInteractiva("construct jonesy necesita una terminal interactiva (no una tubería).")
    return TerminalWindows() if sistema is Sistema.WINDOWS else TerminalPosix(descriptor)


@contextlib.contextmanager
def modo_crudo(terminal: TerminalCruda) -> Iterator[TerminalCruda]:
    """Modo crudo mientras dure el bloque; la terminal se restaura pase lo que pase."""
    terminal.entrar()
    try:
        yield terminal
    finally:
        terminal.salir()
