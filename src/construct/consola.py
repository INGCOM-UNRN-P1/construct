"""Procesos de la VM conectados a la terminal del Host (capa de interfaz de la CLI).

La entrada estándar se lee en un hilo (funciona igual con la consola de Windows, una terminal
POSIX o una tubería) y Ctrl+C no corta construct: se reenvía como SIGINT al programa en la VM,
como si corriera en la terminal. Un segundo Ctrl+C sí lo abandona (la sesión lo mata).

`conectar_pty` es el modo de `construct jonesy`: la terminal en crudo unida a una pty de la VM.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import threading
from typing import Any, BinaryIO

from construct.core.channel import ProcesoRemoto
from construct.core.protocolo import Marco, Tipo
from construct.platform.terminal import TerminalCruda, modo_crudo


def _leer_entrada(loop: asyncio.AbstractEventLoop, cola: asyncio.Queue[bytes], entrada: BinaryIO) -> None:
    # Con el descriptor crudo (os.read) el hilo no toma el lock del buffer de stdin: si sigue
    # bloqueado leyendo cuando construct termina, Python puede cerrar igual.
    try:
        descriptor: int | None = entrada.fileno()
    except (OSError, ValueError, AttributeError):
        descriptor = None
    while True:
        try:
            datos = os.read(descriptor, 65536) if descriptor is not None else entrada.read(65536)
        except (OSError, ValueError):
            datos = b""
        try:
            loop.call_soon_threadsafe(cola.put_nowait, datos)
        except RuntimeError:
            return  # el loop ya cerró (construct terminó): lo leído no tiene a quién ir
        if not datos:
            return


async def conectar_terminal(proceso: ProcesoRemoto, *, entrada: BinaryIO | None = None,
                            salida: BinaryIO | None = None, errores: BinaryIO | None = None,
                            reenviar_ctrl_c: bool = True, registro_entrada: bytearray | None = None,
                            otros: list[Marco] | None = None) -> int:
    """Bombea la terminal del Host hacia el proceso remoto hasta que termina; devuelve su código.

    `registro_entrada` junta lo que se le mandó (para repetirlo, por ejemplo, en el post-mortem de hal) y
    `otros`, los marcos que no son de entrada o salida (la telemetría).
    """
    entrada = entrada if entrada is not None else sys.stdin.buffer
    salida = salida if salida is not None else sys.stdout.buffer
    errores = errores if errores is not None else sys.stderr.buffer
    loop = asyncio.get_running_loop()
    cola: asyncio.Queue[bytes] = asyncio.Queue()
    threading.Thread(target=_leer_entrada, args=(loop, cola, entrada), daemon=True).start()

    interrupciones = 0
    anterior: Any = None

    def al_ctrl_c(*_: Any) -> None:
        nonlocal interrupciones
        interrupciones += 1
        if interrupciones > 1:
            loop.call_soon_threadsafe(tarea_salida.cancel)
        else:
            loop.call_soon_threadsafe(lambda: loop.create_task(proceso.enviar_senal("INT")))

    async def bombear_entrada() -> None:
        while True:
            datos = await cola.get()
            if not datos:
                await proceso.cerrar_entrada()
                return
            if registro_entrada is not None:
                registro_entrada.extend(datos)
            await proceso.escribir(datos)

    async def bombear_salida() -> None:
        while (marco := await proceso.siguiente()) is not None:
            destino = salida if marco.tipo is Tipo.SALIDA else errores if marco.tipo is Tipo.ERRORES else None
            if destino is not None:
                destino.write(marco.datos)
                destino.flush()
            elif otros is not None:
                otros.append(marco)

    if reenviar_ctrl_c and threading.current_thread() is threading.main_thread():
        anterior = signal.signal(signal.SIGINT, al_ctrl_c)
    tarea_entrada = loop.create_task(bombear_entrada())
    tarea_salida = loop.create_task(bombear_salida())
    try:
        await tarea_salida
    except asyncio.CancelledError:
        return 130
    finally:
        tarea_entrada.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea_entrada
        if anterior is not None:
            signal.signal(signal.SIGINT, anterior)
    return proceso.codigo


async def conectar_pty(proceso: ProcesoRemoto, terminal: TerminalCruda, *, entrada: BinaryIO | None = None,
                       salida: BinaryIO | None = None) -> int:
    """Une la terminal del Host, en modo crudo, con un proceso de la VM que tiene pty (RF-05).

    Cada byte va tal cual (Ctrl+C es 0x03 y lo interpreta la pty de la VM), el tamaño se avisa al
    empezar y en cada cambio, y la terminal se restaura siempre: también si se cae la VM.
    """
    entrada = entrada if entrada is not None else sys.stdin.buffer
    salida = salida if salida is not None else sys.stdout.buffer
    loop = asyncio.get_running_loop()
    cola: asyncio.Queue[bytes] = asyncio.Queue()

    def al_cambiar(filas: int, columnas: int) -> None:
        loop.create_task(proceso.cambiar_tamano(filas, columnas))

    async def bombear_entrada() -> None:
        while datos := await cola.get():  # vacío: se cerró la entrada del Host; la pty sigue
            await proceso.escribir(datos)

    async def bombear_salida() -> None:
        while (marco := await proceso.siguiente()) is not None:
            if marco.tipo is Tipo.SALIDA:
                salida.write(marco.datos)
                salida.flush()

    with modo_crudo(terminal):
        await proceso.cambiar_tamano(*terminal.tamano())
        detener = terminal.observar_tamano(loop, al_cambiar)
        threading.Thread(target=_leer_entrada, args=(loop, cola, entrada), daemon=True).start()
        tarea_entrada = loop.create_task(bombear_entrada())
        try:
            await bombear_salida()
        finally:
            detener()
            tarea_entrada.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                await tarea_entrada
    return proceso.codigo
