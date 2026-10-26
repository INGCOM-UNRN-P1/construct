"""Sesión de VM persistente (PLAN Fase 3): el proceso de fondo dueño de QEMU.

Cada `construct build`/`check`/`run` es un proceso corto, pero la VM tiene que seguir viva entre
uno y otro. La sesión:

1. prepara los discos y lanza QEMU atado a su propia vida (`platform.process.lanzar_vinculado`);
2. escucha en loopback al agente (QEMU se conecta como cliente) y lo autentica con el token que le
   pasó por fw_cfg;
3. escucha en otro puerto de loopback a los comandos de la CLI, que se autentican con el token de
   `sesion.json` y hablan el mismo protocolo: la sesión reenvía sus pedidos al agente cambiando
   los `id` (cada cliente numera desde 1) y les devuelve las respuestas;
4. manda los latidos al agente y apaga la VM cuando pasa `inactividad_s` sin clientes, cuando un
   cliente pide APAGAR o cuando recibe SIGTERM/Ctrl+C.

`sesion.json` (en la carpeta de estado, sólo legible por el usuario) dice cómo conectarse.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import secrets
import signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from construct import __version__
from construct.core import discos, imagenes, qemu
from construct.core.channel import Canal, ErrorDeCanal, aceptar
from construct.core.config import Config, cargar
from construct.core.doctor import Entorno, resolver_aceleracion
from construct.core.protocolo import Marco, Tipo
from construct.platform import binarios, host
from construct.platform.paths import Directorios, directorios
from construct.platform.process import lanzar_vinculado, proceso_vivo, terminar

ARCHIVO = "sesion.json"
LATIDO_S = 5
ESPERA_AGENTE_S = 180
INACTIVIDAD_S = 15 * 60
TERMINALES = {Tipo.TERMINADO, Tipo.HECHO, Tipo.FIN}


class ErrorDeSesion(ValueError):
    """No se pudo arrancar o usar la sesión de la VM."""


@dataclass(frozen=True)
class InfoSesion:
    pid: int
    puerto: int
    token: str
    qemu_pid: int
    arquitectura: str
    acelerador: str
    imagen: str
    agente: str
    inicio: float
    version: str


def ruta_info(dirs: Directorios) -> Path:
    return dirs.estado / ARCHIVO


def leer_info(dirs: Directorios) -> InfoSesion | None:
    """La sesión registrada, si su proceso sigue vivo (un archivo viejo se borra)."""
    archivo = ruta_info(dirs)
    try:
        info = InfoSesion(**json.loads(archivo.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        archivo.unlink(missing_ok=True)
        return None
    if not proceso_vivo(info.pid):
        archivo.unlink(missing_ok=True)
        return None
    return info


def _escribir_info(dirs: Directorios, info: InfoSesion) -> None:
    archivo = ruta_info(dirs)
    temporal = archivo.with_suffix(".parcial")
    descriptor = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as salida:
        json.dump(asdict(info), salida)
    os.replace(temporal, archivo)


class Sesion:
    def __init__(self, dirs: Directorios, config: Config, inactividad_s: float) -> None:
        self.dirs = dirs
        self.config = config
        self.inactividad_s = inactividad_s
        self.agente: Canal | None = None
        self.token_clientes = secrets.token_urlsafe(24)
        self.rutas: dict[int, tuple[Canal, int, bool]] = {}
        """id global → (cliente, id del cliente, es proceso)."""
        self.ids: dict[tuple[int, int], int] = {}
        """(id(cliente), id del cliente) → id global."""
        self.siguiente = 1
        self.clientes = 0
        self.ultimo_cliente = time.monotonic()
        self.terminar = asyncio.Event()
        self.descripcion: dict[str, Any] = {}

    # --- Reenvío -------------------------------------------------------------------------------

    async def del_agente(self, marco: Marco) -> None:
        ruta = self.rutas.get(marco.id)
        if ruta is None:
            return
        cliente, id_cliente, es_proceso = ruta
        if marco.tipo in TERMINALES or (marco.tipo is Tipo.ERROR and not es_proceso):
            self._olvidar(marco.id)
        with contextlib.suppress(ErrorDeCanal):
            await cliente.enviar(marco.tipo, id_cliente, marco.datos)

    def _olvidar(self, global_id: int) -> None:
        ruta = self.rutas.pop(global_id, None)
        if ruta is not None:
            self.ids.pop((id(ruta[0]), ruta[1]), None)

    async def del_cliente(self, cliente: Canal, marco: Marco) -> None:
        assert self.agente is not None
        if marco.tipo is Tipo.LATIDO:
            await cliente.enviar(Tipo.LATIDO_OK)
            return
        if marco.tipo is Tipo.APAGAR:
            self.terminar.set()
            return
        clave = (id(cliente), marco.id)
        if marco.tipo in (Tipo.EJECUTAR, Tipo.ESCRIBIR, Tipo.LEER):
            if marco.id == 0 or clave in self.ids:
                await cliente.enviar(Tipo.ERROR, marco.id, {"mensaje": f"el id {marco.id} ya está en uso"})
                return
            global_id = self.siguiente
            self.siguiente += 1
            self.ids[clave] = global_id
            self.rutas[global_id] = (cliente, marco.id, marco.tipo is Tipo.EJECUTAR)
        else:
            global_id = self.ids.get(clave, 0)
            if global_id == 0:
                return  # el proceso o la transferencia ya terminó
        await self.agente.enviar(marco.tipo, global_id, marco.datos)

    async def atender_cliente(self, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            cliente, _ = await aceptar(lector, escritor, self.token_clientes, timeout=10,
                                       bienvenida={"latido_s": 0, "vm": self.descripcion})
        except (ErrorDeCanal, ValueError):
            return
        self.clientes += 1

        async def reenviar(marco: Marco) -> None:
            await self.del_cliente(cliente, marco)

        cliente.sin_dueno = reenviar
        try:
            await cliente.cerrado.wait()
        finally:
            self.clientes -= 1
            self.ultimo_cliente = time.monotonic()
            # Lo que dejó corriendo un cliente que se fue (Ctrl+C, terminal cerrada) se mata.
            for (dueno, _), global_id in list(self.ids.items()):
                if dueno == id(cliente):
                    es_proceso = self.rutas[global_id][2]
                    self._olvidar(global_id)
                    if es_proceso and self.agente is not None:
                        with contextlib.suppress(ErrorDeCanal):
                            await self.agente.enviar(Tipo.SENAL, global_id, {"senal": "KILL"})
            escritor.close()

    # --- Ciclo de vida -------------------------------------------------------------------------

    async def latir(self) -> None:
        assert self.agente is not None
        while not self.agente.cerrado.is_set():
            with contextlib.suppress(ErrorDeCanal):
                await self.agente.enviar(Tipo.LATIDO)
            if self.clientes == 0 and time.monotonic() - self.ultimo_cliente > self.inactividad_s:
                print(f"sin uso durante {self.inactividad_s:.0f} s: se apaga la VM", flush=True)
                self.terminar.set()
            await asyncio.sleep(LATIDO_S)

    async def correr(self) -> int:
        entorno = Entorno(host=host(), config=self.config, directorios=self.dirs)
        elegida = resolver_aceleracion(entorno)
        arq = elegida.arquitectura_guest
        imagen = imagenes.instalada(self.dirs.imagenes, arq)
        if imagen is None or not imagen.completa():
            raise ErrorDeSesion(f"no está instalada la imagen del Guest {arq.value}: ejecutá `construct setup`.")
        ejecutable = binarios.buscar_qemu(entorno.host, arq, ruta_configurada=self.config.vm.qemu or None)
        if ejecutable is None:
            raise ErrorDeSesion(f"no se encontró qemu-system-{arq.value}: ejecutá `construct doctor`.")
        qemu_img = binarios.buscar_qemu_img(entorno.host, ejecutable)
        if qemu_img is None:
            raise ErrorDeSesion("no se encontró qemu-img: ejecutá `construct doctor`.")
        unidad = discos.preparar(self.dirs.vms / arq.value, imagen, qemu_img.ruta)

        token_agente = secrets.token_hex(16)
        conexion_agente: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
            asyncio.get_running_loop().create_future())

        async def al_conectar_agente(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
            if conexion_agente.done():
                escritor.close()  # una sola conexión: la de QEMU
            else:
                conexion_agente.set_result((lector, escritor))

        servidor_agente = await asyncio.start_server(al_conectar_agente, "127.0.0.1", 0)
        servidor_clientes = await asyncio.start_server(self.atender_cliente, "127.0.0.1", 0)
        parametros = qemu.ParametrosVM(
            qemu=ejecutable.ruta, aceleracion=elegida, kernel=imagen.ruta("vmlinuz"),
            initramfs=imagen.ruta("initramfs"), sistema=unidad.sistema, workspace=unidad.workspace,
            consola=self.dirs.logs / "consola.log", puerto_agente=servidor_agente.sockets[0].getsockname()[1],
            token=token_agente, memoria_mb=self.config.vm.memoria_mb, cpus=self.config.vm.cpus)
        self.dirs.logs.mkdir(parents=True, exist_ok=True)
        registro_qemu = (self.dirs.logs / "qemu.log").open("wb")
        proceso = lanzar_vinculado(qemu.argumentos(parametros), stdout=registro_qemu, stderr=registro_qemu)
        print(f"QEMU {proceso.pid}: {elegida.acelerador.value}, Guest {arq.value}, imagen {imagen.version}", flush=True)

        try:
            lector, escritor = await self._esperar_agente(conexion_agente, proceso)
            self.agente, hola = await aceptar(lector, escritor, token_agente, timeout=30, bienvenida={"latido_s": LATIDO_S})
            servidor_agente.close()
            self.agente.sin_dueno = self.del_agente
            self.descripcion = {"arquitectura": arq.value, "acelerador": elegida.acelerador.value,
                                "imagen": imagen.version, "agente": hola.get("agente", "")}
            _escribir_info(self.dirs, InfoSesion(
                pid=os.getpid(), puerto=servidor_clientes.sockets[0].getsockname()[1], token=self.token_clientes,
                qemu_pid=proceso.pid, arquitectura=arq.value, acelerador=elegida.acelerador.value,
                imagen=imagen.version, agente=str(hola.get("agente", "")), inicio=time.time(), version=__version__))
            print("sesión lista", flush=True)
            latidos = asyncio.get_running_loop().create_task(self.latir())
            espera = asyncio.get_running_loop().create_task(self.terminar.wait())
            caida = asyncio.get_running_loop().create_task(self.agente.cerrado.wait())
            await asyncio.wait({espera, caida}, return_when=asyncio.FIRST_COMPLETED)
            latidos.cancel()
            if not self.agente.cerrado.is_set():
                with contextlib.suppress(ErrorDeCanal):
                    await self.agente.apagar()
        finally:
            ruta_info(self.dirs).unlink(missing_ok=True)
            servidor_clientes.close()
            servidor_agente.close()
            await self._esperar_qemu(proceso)
            registro_qemu.close()
        return 0

    async def _esperar_agente(self, conexion: asyncio.Future[Any], proceso: Any) -> Any:
        limite = time.monotonic() + ESPERA_AGENTE_S
        while time.monotonic() < limite:
            if proceso.poll() is not None:
                raise ErrorDeSesion(f"QEMU terminó al arrancar (código {proceso.returncode}): "
                                    f"revisá {self.dirs.logs / 'qemu.log'}.")
            if self.terminar.is_set():
                raise ErrorDeSesion("arranque cancelado.")
            try:
                return await asyncio.wait_for(asyncio.shield(conexion), 0.5)
            except TimeoutError:
                continue
        raise ErrorDeSesion(f"el Guest no respondió en {ESPERA_AGENTE_S} s: revisá {self.dirs.logs / 'consola.log'}.")

    async def _esperar_qemu(self, proceso: Any) -> None:
        for _ in range(60):  # hasta 30 s para el apagado ordenado
            if proceso.poll() is not None:
                return
            await asyncio.sleep(0.5)
        terminar(proceso.pid)
        proceso.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m construct.core.sesion", description=__doc__)
    parser.add_argument("--config", type=Path, help="project.toml con la sección [vm]")
    parser.add_argument("--inactividad", type=float, default=INACTIVIDAD_S, help="segundos sin clientes hasta apagar")
    args = parser.parse_args(argv)
    config = cargar(args.config) if args.config else Config()
    dirs = directorios()
    dirs.crear()
    if leer_info(dirs) is not None:
        print("ya hay una sesión corriendo", file=sys.stderr)
        return 1
    sesion = Sesion(dirs, config, args.inactividad)

    async def correr() -> int:
        loop = asyncio.get_running_loop()
        for nombre in ("SIGTERM", "SIGINT", "SIGHUP"):
            numero = getattr(signal, nombre, None)
            if numero is not None:
                with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows no los soporta en el loop
                    loop.add_signal_handler(numero, sesion.terminar.set)
        return await sesion.correr()

    try:
        return asyncio.run(correr())
    except (ErrorDeSesion, discos.ErrorDeDiscos, imagenes.ErrorDeImagen, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
