"""Canal `construct-ctl` sobre asyncio (PLAN §3.1 y §3.2, docs/protocolo.md).

Un `Canal` es una conexión ya autenticada: la de la sesión con el agente del Guest o la de un
comando de la CLI con la sesión, que hablan el mismo protocolo. Encima de los marcos ofrece
`ejecutar`/`correr` (procesos), `escribir_archivo`/`leer_archivo` (transferencias) y `apagar`.

Sólo usa TCP sobre loopback, así funciona igual con el *event loop* Proactor de Windows.
"""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from construct.core.protocolo import (
    PROTOCOLO,
    Decodificador,
    ErrorDeProtocolo,
    Marco,
    Tipo,
    codificar,
    trocear,
)

BLOQUE_LECTURA = 256 * 1024


class ErrorDeCanal(ConnectionError):
    """La conexión con la VM se cortó o el otro extremo no respondió como corresponde."""


class ErrorRemoto(RuntimeError):
    """El agente informó un error en un pedido (programa inexistente, archivo que no se pudo leer…)."""


@dataclass
class Resultado:
    codigo: int
    senal: str | None
    salida: bytes = b""
    errores: bytes = b""
    error: str | None = None
    """Mensaje del agente si el proceso no se pudo lanzar."""

    @property
    def ok(self) -> bool:
        return self.codigo == 0 and self.error is None

    def texto(self) -> str:
        return self.salida.decode("utf-8", errors="replace")


class Canal:
    def __init__(self, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        self._lector = lector
        self._escritor = escritor
        self._decodificador = Decodificador()
        self._colas: dict[int, asyncio.Queue[Marco | None]] = {}
        self._siguiente_id = 1
        self._escritura = asyncio.Lock()
        self._tarea: asyncio.Task[None] | None = None
        self.cerrado = asyncio.Event()
        self.sin_dueno: Callable[[Marco], Awaitable[None]] | None = None
        """Recibe los marcos cuyo `id` no reservó este extremo (la sesión los reenvía)."""

    def iniciar(self) -> None:
        self._tarea = asyncio.get_running_loop().create_task(self._leer())

    async def _leer(self) -> None:
        try:
            while True:
                datos = await self._lector.read(BLOQUE_LECTURA)
                if not datos:
                    break
                for marco in self._decodificador.alimentar(datos):
                    cola = self._colas.get(marco.id) if marco.id else None
                    if cola is not None:
                        cola.put_nowait(marco)
                    elif marco.tipo is Tipo.LATIDO_OK:
                        continue
                    elif self.sin_dueno is not None:
                        await self.sin_dueno(marco)
        except (ConnectionError, OSError, ErrorDeProtocolo):
            pass
        finally:
            self.cerrado.set()
            for cola in self._colas.values():
                cola.put_nowait(None)

    async def enviar(self, tipo: Tipo, id: int = 0, datos: bytes | dict[str, Any] = b"") -> None:
        await self.enviar_crudo(codificar(tipo, id, datos))

    async def enviar_crudo(self, *marcos: bytes) -> None:
        if self.cerrado.is_set():
            raise ErrorDeCanal("la conexión con la máquina virtual está cerrada.")
        async with self._escritura:
            try:
                for marco in marcos:
                    self._escritor.write(marco)
                await self._escritor.drain()
            except (ConnectionError, OSError) as error:
                raise ErrorDeCanal(f"se cortó la conexión con la máquina virtual ({error}).") from None

    def reservar(self) -> tuple[int, asyncio.Queue[Marco | None]]:
        id = self._siguiente_id
        self._siguiente_id += 1
        cola: asyncio.Queue[Marco | None] = asyncio.Queue()
        self._colas[id] = cola
        return id, cola

    def liberar(self, id: int) -> None:
        self._colas.pop(id, None)

    async def cerrar(self) -> None:
        self._escritor.close()
        try:
            await self._escritor.wait_closed()
        except (ConnectionError, OSError):
            pass
        if self._tarea is not None:
            await self._tarea

    # --- Procesos --------------------------------------------------------------------------------

    async def ejecutar(self, argv: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None,
                       pty: bool = False, filas: int = 24, columnas: int = 80, root: bool = False,
                       telemetria_ms: int = 0) -> ProcesoRemoto:
        id, cola = self.reservar()
        pedido: dict[str, Any] = {"argv": argv, "pty": pty, "root": root}
        if cwd is not None:
            pedido["cwd"] = cwd
        if env:
            pedido["env"] = env
        if pty:
            pedido.update(filas=filas, columnas=columnas)
        if telemetria_ms:
            pedido["telemetria_ms"] = telemetria_ms
        await self.enviar(Tipo.EJECUTAR, id, pedido)
        proceso = ProcesoRemoto(self, id, cola)
        await proceso._esperar_inicio()
        return proceso

    async def correr(self, argv: list[str], entrada: bytes | None = None, **opciones: Any) -> Resultado:
        """Ejecuta y espera: la salida y los errores completos, el código y la señal."""
        proceso = await self.ejecutar(argv, **opciones)
        if entrada is not None and proceso.error is None:
            await proceso.escribir(entrada)
            await proceso.cerrar_entrada()
        salida, errores = bytearray(), bytearray()
        async for tipo, datos in proceso.flujos():
            (salida if tipo is Tipo.SALIDA else errores).extend(datos)
        return Resultado(proceso.codigo, proceso.senal, bytes(salida), bytes(errores), proceso.error)

    # --- Transferencias --------------------------------------------------------------------------

    async def escribir_archivo(self, ruta: str, datos: bytes, *, modo: int = 0o644, root: bool = False) -> None:
        id, cola = self.reservar()
        try:
            await self.enviar_crudo(codificar(Tipo.ESCRIBIR, id, {"ruta": ruta, "modo": modo, "root": root}),
                                    *trocear(Tipo.DATOS, id, datos), codificar(Tipo.FIN, id))
            marco = await _siguiente(cola)
            if marco.tipo is Tipo.ERROR:
                raise ErrorRemoto(marco.json().get("mensaje", "error al escribir"))
            if marco.tipo is not Tipo.HECHO:
                raise ErrorDeCanal(f"respuesta inesperada {marco.tipo.name} al escribir {ruta}.")
        finally:
            self.liberar(id)

    async def leer_archivo(self, ruta: str, *, root: bool = False) -> bytes:
        id, cola = self.reservar()
        datos = bytearray()
        try:
            await self.enviar(Tipo.LEER, id, {"ruta": ruta, "root": root})
            while True:
                marco = await _siguiente(cola)
                if marco.tipo is Tipo.DATOS:
                    datos += marco.datos
                elif marco.tipo is Tipo.FIN:
                    return bytes(datos)
                elif marco.tipo is Tipo.ERROR:
                    raise ErrorRemoto(marco.json().get("mensaje", "error al leer"))
        finally:
            self.liberar(id)

    async def apagar(self) -> None:
        await self.enviar(Tipo.APAGAR)


async def _siguiente(cola: asyncio.Queue[Marco | None]) -> Marco:
    marco = await cola.get()
    if marco is None:
        raise ErrorDeCanal("se cortó la conexión con la máquina virtual.")
    return marco


@dataclass
class ProcesoRemoto:
    canal: Canal
    id: int
    _cola: asyncio.Queue[Marco | None]
    pid: int | None = None
    error: str | None = None
    codigo: int = -1
    senal: str | None = None
    terminado: bool = False
    _pendientes: list[Marco] = field(default_factory=list)

    async def _esperar_inicio(self) -> None:
        while True:
            marco = await _siguiente(self._cola)
            if marco.tipo is Tipo.INICIADO:
                self.pid = marco.json().get("pid")
                return
            if marco.tipo is Tipo.ERROR:
                self.error = marco.json().get("mensaje", "error al ejecutar")
                return
            self._pendientes.append(marco)  # salida temprana: se entrega en flujos()

    async def siguiente(self) -> Marco | None:
        """El próximo marco del proceso (SALIDA, ERRORES, ERROR, TELEMETRIA o TERMINADO); None al final."""
        if self.terminado:
            return None
        marco = self._pendientes.pop(0) if self._pendientes else await _siguiente(self._cola)
        if marco.tipo is Tipo.TERMINADO:
            datos = marco.json()
            self.codigo = int(datos.get("codigo", -1))
            self.senal = datos.get("senal")
            self.terminado = True
            self.canal.liberar(self.id)
        elif marco.tipo is Tipo.ERROR and self.error is None:
            self.error = marco.json().get("mensaje")
        return marco

    async def flujos(self) -> AsyncIterator[tuple[Tipo, bytes]]:
        """(SALIDA|ERRORES, bytes) hasta que el proceso termina."""
        while (marco := await self.siguiente()) is not None:
            if marco.tipo in (Tipo.SALIDA, Tipo.ERRORES):
                yield marco.tipo, marco.datos

    async def escribir(self, datos: bytes) -> None:
        if datos:
            await self.canal.enviar_crudo(*trocear(Tipo.ENTRADA, self.id, datos))

    async def cerrar_entrada(self) -> None:
        await self.canal.enviar(Tipo.ENTRADA, self.id)

    async def enviar_senal(self, nombre: str) -> None:
        if not self.terminado:
            await self.canal.enviar(Tipo.SENAL, self.id, {"senal": nombre})

    async def cambiar_tamano(self, filas: int, columnas: int) -> None:
        await self.canal.enviar(Tipo.TAMANO, self.id, {"filas": filas, "columnas": columnas})

    async def esperar(self) -> int:
        while await self.siguiente() is not None:
            pass
        return self.codigo


# --- Autenticación ----------------------------------------------------------------------------


async def _primer_marco(canal: Canal, lector: asyncio.StreamReader, timeout: float) -> Marco:
    async def leer() -> Marco:
        while True:
            datos = await lector.read(BLOQUE_LECTURA)
            if not datos:
                raise ErrorDeCanal("el otro extremo cerró la conexión antes de presentarse.")
            marcos = canal._decodificador.alimentar(datos)
            if marcos:
                if len(marcos) > 1:  # no debería pasar antes de la BIENVENIDA
                    raise ErrorDeProtocolo("llegaron mensajes antes de completar la presentación.")
                return marcos[0]

    try:
        return await asyncio.wait_for(leer(), timeout)
    except TimeoutError:
        raise ErrorDeCanal("el otro extremo no se presentó a tiempo.") from None


def _token_valido(datos: dict[str, Any], token: str) -> bool:
    recibido = datos.get("token")
    return isinstance(recibido, str) and hmac.compare_digest(recibido.encode(), token.encode())


async def aceptar(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter, token: str, *,
                  timeout: float, bienvenida: dict[str, Any]) -> tuple[Canal, dict[str, Any]]:
    """Del lado que escucha: espera el HOLA, verifica token y protocolo, responde BIENVENIDA."""
    canal = Canal(lector, escritor)
    try:
        hola = await _primer_marco(canal, lector, timeout)
        datos = hola.json()
        if hola.tipo is not Tipo.HOLA or not _token_valido(datos, token) or datos.get("protocolo") != PROTOCOLO:
            raise ErrorDeCanal("presentación rechazada (token o protocolo incorrectos).")
    except (ErrorDeCanal, ErrorDeProtocolo):
        escritor.close()
        raise
    await canal.enviar(Tipo.BIENVENIDA, 0, {"protocolo": PROTOCOLO, **bienvenida})
    canal.iniciar()
    return canal, datos


async def conectar(puerto: int, token: str, *, timeout: float = 10) -> tuple[Canal, dict[str, Any]]:
    """Del lado que conecta (la CLI hacia la sesión): manda HOLA y espera la BIENVENIDA."""
    try:
        lector, escritor = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", puerto), timeout)
    except (OSError, TimeoutError) as error:
        raise ErrorDeCanal(f"no se pudo conectar con la sesión de la VM ({error}).") from None
    canal = Canal(lector, escritor)
    await canal.enviar(Tipo.HOLA, 0, {"protocolo": PROTOCOLO, "token": token, "cliente": "construct"})
    try:
        respuesta = await _primer_marco(canal, lector, timeout)
    except (ErrorDeCanal, ErrorDeProtocolo):
        escritor.close()
        raise
    if respuesta.tipo is not Tipo.BIENVENIDA:
        escritor.close()
        raise ErrorDeCanal("la sesión rechazó la conexión.")
    canal.iniciar()
    return canal, respuesta.json()
