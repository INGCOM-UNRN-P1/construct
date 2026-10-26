"""Depuración con GDB/MI (SPEC §4 `GDBClient`, PLAN §3.4, decisión D2).

Dos *backends* con la misma interfaz y los mismos resultados hacia la UI y `bishop_view`:

- `GuestGdbBackend` (por defecto, el de los alumnos): `gdb --interpreter=mi3` corre **dentro de la
  VM** como un proceso más del canal; el Host no necesita GDB.
- `HostGdbBackend` (modo desarrollo): un GDB local (`gdb-multiarch`) se conecta con
  `target remote | construct dev gdbserver …`, es decir, a un `gdbserver -` del Guest por el mismo
  canal, sin red ni puertos.

En los dos casos el Host sólo interpreta MI con el parser de `pygdbmi`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pygdbmi import gdbmiparser

from construct.core.channel import Canal, ProcesoRemoto
from construct.core.protocolo import Tipo

TIMEOUT_S = 30


class ErrorDeDepuracion(RuntimeError):
    """GDB rechazó un comando o terminó."""


class Transporte(Protocol):
    async def escribir(self, datos: bytes) -> None: ...
    async def leer(self) -> bytes | None: ...
    async def cerrar(self) -> None: ...


@dataclass
class Detencion:
    """Dónde y por qué se detuvo el programa (`*stopped`)."""

    motivo: str
    archivo: str | None = None
    linea: int | None = None
    funcion: str | None = None
    codigo_salida: int | None = None
    senal: str | None = None

    @property
    def termino(self) -> bool:
        return self.motivo.startswith("exited")


@dataclass
class Marco:
    nivel: int
    funcion: str
    archivo: str | None
    linea: int | None
    direccion: str | None


@dataclass
class ClienteMI:
    transporte: Transporte
    _token: int = 0
    _pendiente: str = ""
    _resultados: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    _detenciones: asyncio.Queue[Detencion] = field(default_factory=asyncio.Queue)
    salida_programa: list[str] = field(default_factory=list)
    _lector: asyncio.Task[None] | None = None

    def iniciar(self) -> None:
        self._lector = asyncio.get_running_loop().create_task(self._leer())

    async def _leer(self) -> None:
        while (datos := await self.transporte.leer()) is not None:
            self._pendiente += datos.decode("utf-8", errors="replace")
            *lineas, self._pendiente = self._pendiente.split("\n")
            for linea in lineas:
                self._procesar(linea.rstrip("\r"))
        for futuro in self._resultados.values():
            if not futuro.done():
                futuro.set_exception(ErrorDeDepuracion("GDB terminó."))
        self._detenciones.put_nowait(Detencion("gdb-terminado"))

    def _procesar(self, linea: str) -> None:
        if not linea or linea.strip() == "(gdb)":
            return
        registro = gdbmiparser.parse_response(linea)
        tipo = registro.get("type")
        if tipo == "result":
            futuro = self._resultados.pop(registro.get("token") or -1, None)
            if futuro is not None and not futuro.done():
                futuro.set_result(registro)
        elif tipo == "notify" and registro.get("message") == "stopped":
            self._detenciones.put_nowait(_detencion(registro.get("payload") or {}))
        elif tipo in ("output", "target"):
            self.salida_programa.append(str(registro.get("payload") or ""))

    async def comando(self, mi: str, timeout: float = TIMEOUT_S) -> dict[str, Any]:
        self._token += 1
        token = self._token
        futuro: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._resultados[token] = futuro
        await self.transporte.escribir(f"{token}{mi}\n".encode())
        try:
            registro = await asyncio.wait_for(futuro, timeout)
        except TimeoutError:
            self._resultados.pop(token, None)
            raise ErrorDeDepuracion(f"GDB no respondió a {mi}.") from None
        if registro.get("message") == "error":
            raise ErrorDeDepuracion(str((registro.get("payload") or {}).get("msg", "error de GDB")))
        return dict(registro.get("payload") or {})

    async def esperar_detencion(self, timeout: float = TIMEOUT_S) -> Detencion:
        try:
            return await asyncio.wait_for(self._detenciones.get(), timeout)
        except TimeoutError:
            raise ErrorDeDepuracion("el programa no se detuvo a tiempo (¿está esperando entrada?).") from None


def _entero(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _detencion(datos: dict[str, Any]) -> Detencion:
    marco = datos.get("frame") or {}
    senal = datos.get("signal-name")
    motivo = str(datos.get("reason", "desconocido"))
    codigo = datos.get("exit-code")
    salida = int(codigo, 8) if isinstance(codigo, str) and codigo.isdigit() else (0 if motivo == "exited-normally" else None)
    return Detencion(motivo=motivo, archivo=marco.get("file"), linea=_entero(marco.get("line")),
                     funcion=marco.get("func"), codigo_salida=salida,  # GDB informa el código en octal
                     senal=str(senal).removeprefix("SIG") if senal else None)


class Depurador:
    """Operaciones de depuración del IDE (F9 breakpoint, F10 step over, F11 step into)."""

    def __init__(self, mi: ClienteMI) -> None:
        self.mi = mi

    async def preparar(self) -> None:
        await self.mi.comando("-gdb-set confirm off")
        await self.mi.comando("-gdb-set pagination off")

    async def breakpoint(self, archivo: str, linea: int) -> int:
        datos = await self.mi.comando(f"-break-insert {archivo}:{linea}")
        return int(datos["bkpt"]["number"])

    async def borrar_breakpoint(self, numero: int) -> None:
        await self.mi.comando(f"-break-delete {numero}")

    async def fijar_argumentos(self, argumentos: Sequence[str], entrada: str | None = None) -> None:
        """Argumentos del programa y, con `entrada`, el archivo del que lee su entrada estándar (la de
        GDB lleva los comandos MI). GDB los pasa por el shell del Guest: se citan como en sh."""
        partes = [_citar(a) for a in argumentos] + ([f"< {_citar(entrada)}"] if entrada else [])
        await self.mi.comando("-exec-arguments " + " ".join(partes) if partes else "-exec-arguments")

    async def correr(self) -> Detencion:
        await self.mi.comando("-exec-run")
        return await self.mi.esperar_detencion()

    async def siguiente(self) -> Detencion:
        await self.mi.comando("-exec-next")
        return await self.mi.esperar_detencion()

    async def entrar(self) -> Detencion:
        await self.mi.comando("-exec-step")
        return await self.mi.esperar_detencion()

    async def continuar(self) -> Detencion:
        await self.mi.comando("-exec-continue")
        return await self.mi.esperar_detencion()

    async def pila(self) -> list[Marco]:
        datos = await self.mi.comando("-stack-list-frames")
        return [Marco(int(m.get("level", 0)), m.get("func", "?"), m.get("file"), _entero(m.get("line")), m.get("addr"))
                for m in (f.get("frame", f) if isinstance(f, dict) else {} for f in datos.get("stack", []))]

    async def variables(self) -> dict[str, str]:
        datos = await self.mi.comando("-stack-list-variables --simple-values")
        return {v["name"]: v.get("value", "") for v in datos.get("variables", [])}

    async def evaluar(self, expresion: str) -> str:
        escapada = expresion.replace("\\", "\\\\").replace('"', '\\"')
        return str((await self.mi.comando(f'-data-evaluate-expression "{escapada}"')).get("value", ""))

    async def salir(self) -> None:
        with contextlib.suppress(ErrorDeDepuracion, ConnectionError):
            await self.mi.comando("-gdb-exit", timeout=5)
        await self.mi.transporte.cerrar()


# --- Backends ---------------------------------------------------------------------------------


class _TransporteRemoto:
    def __init__(self, proceso: ProcesoRemoto) -> None:
        self.proceso = proceso

    async def escribir(self, datos: bytes) -> None:
        await self.proceso.escribir(datos)

    async def leer(self) -> bytes | None:
        while (marco := await self.proceso.siguiente()) is not None:
            if marco.tipo in (Tipo.SALIDA, Tipo.ERRORES):
                return marco.datos
        return None

    async def cerrar(self) -> None:
        await self.proceso.enviar_senal("KILL")


async def GuestGdbBackend(canal: Canal, binario: str, argumentos: Sequence[str] = (), *,
                          cwd: str | None = None) -> Depurador:
    """GDB dentro de la VM (el de los alumnos)."""
    proceso = await canal.ejecutar(["gdb", "--interpreter=mi3", "--quiet", "--nx", "--args", binario, *argumentos],
                                   cwd=cwd)
    if proceso.error:
        raise ErrorDeDepuracion(proceso.error)
    return await _armar(_TransporteRemoto(proceso))


class _TransporteLocal:
    def __init__(self, proceso: asyncio.subprocess.Process) -> None:
        self.proceso = proceso

    async def escribir(self, datos: bytes) -> None:
        assert self.proceso.stdin is not None
        self.proceso.stdin.write(datos)
        await self.proceso.stdin.drain()

    async def leer(self) -> bytes | None:
        assert self.proceso.stdout is not None
        datos = await self.proceso.stdout.read(65536)
        return datos or None

    async def cerrar(self) -> None:
        if self.proceso.returncode is None:
            self.proceso.kill()
            await self.proceso.wait()


async def HostGdbBackend(gdb: str, binario_local: str, comando_gdbserver: Sequence[str], *,
                         sysroot: str | None = None, sustituir: tuple[str, str] | None = None) -> Depurador:
    """GDB del Host contra el `gdbserver -` del Guest (modo desarrollo)."""
    previos = [f"-gdb-set sysroot {sysroot}"] if sysroot else []
    if sustituir:
        previos.append(f"-gdb-set substitute-path {sustituir[0]} {sustituir[1]}")
    proceso = await asyncio.create_subprocess_exec(
        gdb, "--interpreter=mi3", "--quiet", "--nx", binario_local,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    depurador = await _armar(_TransporteLocal(proceso))
    for comando in previos:
        await depurador.mi.comando(comando)
    await depurador.mi.comando("-target-select remote | " + " ".join(_citar(p) for p in comando_gdbserver), timeout=60)
    return depurador


def _citar(parte: str) -> str:
    seguros = "-_./:=@+"
    return parte if parte and all(c.isalnum() or c in seguros for c in parte) else "'" + parte.replace("'", "'\\''") + "'"


async def _armar(transporte: Transporte) -> Depurador:
    mi = ClienteMI(transporte)
    mi.iniciar()
    depurador = Depurador(mi)
    await depurador.preparar()
    return depurador
