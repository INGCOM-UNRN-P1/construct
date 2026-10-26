"""Canal, reenvío de la sesión y sincronización contra el agente real (compilado en Linux), sin QEMU."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from construct.core.channel import Canal, ErrorDeCanal, ErrorRemoto, aceptar, conectar
from construct.core.config import Config
from construct.core.sesion import Sesion
from construct.core.sync_manager import ErrorDeSync, SyncManager, nombre_en_vm, problemas_de_nombres
from construct.platform.paths import directorios

GIT = shutil.which("git") or "git"
ESPERA_APAGADO_S = 60


@asynccontextmanager
async def agente(binario: Path, carpeta: Path) -> AsyncIterator[Canal]:
    token = "token-de-prueba"
    (carpeta / "token").write_text(token, encoding="utf-8")
    llegada: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = asyncio.get_running_loop().create_future()

    async def al_conectar(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        if not llegada.done():
            llegada.set_result((lector, escritor))

    servidor = await asyncio.start_server(al_conectar, "127.0.0.1", 0)
    puerto = servidor.sockets[0].getsockname()[1]
    proceso = subprocess.Popen([str(binario), "--conectar", f"tcp:127.0.0.1:{puerto}", "--token-archivo",  # noqa: ASYNC220
                                str(carpeta / "token"), "--sin-apagado", "--espera-host", "3"],
                               stderr=subprocess.DEVNULL)
    try:
        lector, escritor = await asyncio.wait_for(llegada, 10)
        canal, hola = await aceptar(lector, escritor, token, timeout=10, bienvenida={"latido_s": 30})
        assert hola["agente"]
        yield canal
        with contextlib.suppress(ErrorDeCanal):
            await canal.apagar()
            # Al apagarse el agente llama a sync(2), que con el disco del Host ocupado tarda.
            await asyncio.get_running_loop().run_in_executor(None, proceso.wait, ESPERA_APAGADO_S)
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()
        servidor.close()


def correr(corrutina: Callable[[], Awaitable[Any]]) -> Any:
    return asyncio.run(asyncio.wait_for(corrutina(), 60 + ESPERA_APAGADO_S))


# --- Canal ------------------------------------------------------------------------------------


def test_canal_correr_y_archivos(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with agente(agente_compilado, tmp_path) as canal:
            resultado = await canal.correr(["sh", "-c", "cat; echo fin >&2; exit 2"], entrada=b"hola")
            assert (resultado.salida, resultado.errores, resultado.codigo) == (b"hola", b"fin\n", 2)
            inexistente = await canal.correr(["no-existe"])
            assert inexistente.codigo == 127 and "no se encontró" in (inexistente.error or "")
            await canal.escribir_archivo(str(tmp_path / "a" / "b.txt"), "ñandú\n".encode() * 100_000)
            assert await canal.leer_archivo(str(tmp_path / "a" / "b.txt")) == "ñandú\n".encode() * 100_000
            with pytest.raises(ErrorRemoto, match="no se pudo leer"):
                await canal.leer_archivo(str(tmp_path / "nada"))

    correr(prueba)


def test_canal_cortado(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with agente(agente_compilado, tmp_path) as canal:
            proceso = await canal.ejecutar(["sleep", "30"])
            canal._escritor.close()
            # Quien espera un proceso se entera del corte en lugar de quedar colgado.
            with pytest.raises(ErrorDeCanal):
                await proceso.esperar()
            with pytest.raises(ErrorDeCanal):
                await canal.correr(["true"])

    correr(prueba)


# --- Sesión -----------------------------------------------------------------------------------


@asynccontextmanager
async def sesion_con_agente(binario: Path, carpeta: Path) -> AsyncIterator[tuple[Sesion, int]]:
    async with agente(binario, carpeta) as canal:
        sesion = Sesion(directorios(), Config(), inactividad_s=3600)
        sesion.agente = canal
        canal.sin_dueno = sesion.del_agente
        servidor = await asyncio.start_server(sesion.atender_cliente, "127.0.0.1", 0)
        try:
            yield sesion, servidor.sockets[0].getsockname()[1]
        finally:
            servidor.close()


def test_dos_clientes_con_los_mismos_ids(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with sesion_con_agente(agente_compilado, tmp_path) as (sesion, puerto):
            uno, _ = await conectar(puerto, sesion.token_clientes)
            dos, _ = await conectar(puerto, sesion.token_clientes)
            # Los dos clientes numeran desde 1: la sesión los distingue.
            a, b = await asyncio.gather(uno.correr(["sh", "-c", "sleep 0.3; echo uno"]),
                                        dos.correr(["sh", "-c", "echo dos"]))
            assert (a.salida, b.salida) == (b"uno\n", b"dos\n")
            assert not sesion.rutas  # lo terminado no queda registrado
            await uno.cerrar()
            await dos.cerrar()

    correr(prueba)


def test_token_incorrecto(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with sesion_con_agente(agente_compilado, tmp_path) as (_, puerto):
            with pytest.raises(ErrorDeCanal):
                await conectar(puerto, "otro-token", timeout=3)

    correr(prueba)


def test_lo_de_un_cliente_que_se_va_se_mata(agente_compilado: Path, tmp_path: Path) -> None:
    marca = tmp_path / "pid"

    async def prueba() -> None:
        async with sesion_con_agente(agente_compilado, tmp_path) as (sesion, puerto):
            cliente, _ = await conectar(puerto, sesion.token_clientes)
            await cliente.ejecutar(["sh", "-c", f"echo $$ > {marca}; exec sleep 60"])
            while not marca.exists() or not marca.read_text().strip():
                await asyncio.sleep(0.05)
            await cliente.cerrar()
            pid = int(marca.read_text())
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
                await asyncio.sleep(0.05)
            raise AssertionError("el proceso del cliente que se fue sigue vivo")

    correr(prueba)


def test_apagar_desde_un_cliente(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with sesion_con_agente(agente_compilado, tmp_path) as (sesion, puerto):
            cliente, _ = await conectar(puerto, sesion.token_clientes)
            await cliente.apagar()
            await asyncio.wait_for(sesion.terminar.wait(), 5)
            await cliente.cerrar()

    correr(prueba)


# --- Sincronización ---------------------------------------------------------------------------


def test_nombres_problematicos() -> None:
    assert problemas_de_nombres(["main.c", "datos/entrada.txt"]) == []
    problemas = " ".join(problemas_de_nombres(["aux.c", "Main.c", "main.c", "a:b.c", "nul", "punto."]))
    assert "reservado en Windows" in problemas and "mayúsculas" in problemas and "no admite" in problemas
    assert problemas_de_nombres(["canción.c"])  # NFD (como lo guarda macOS)


def test_nombre_en_vm_estable_y_sin_choques(tmp_path: Path) -> None:
    (tmp_path / "a" / "tp1").mkdir(parents=True)
    (tmp_path / "b" / "tp1").mkdir(parents=True)
    uno, otro = nombre_en_vm(tmp_path / "a" / "tp1"), nombre_en_vm(tmp_path / "b" / "tp1")
    assert uno.startswith("/workspace/tp1-") and uno != otro
    assert nombre_en_vm(tmp_path / "a" / "tp1") == uno
    assert nombre_en_vm(tmp_path / "Programación Ñ").startswith("/workspace/Programacion-N-")


@asynccontextmanager
async def sincronizador(binario: Path, tmp_path: Path) -> AsyncIterator[tuple[SyncManager, Path, Path]]:
    proyecto = tmp_path / "proyecto"
    proyecto.mkdir()
    raiz_vm = tmp_path / "vm"
    raiz_vm.mkdir()
    async with agente(binario, tmp_path) as canal:
        gestor = SyncManager(proyecto, GIT, canal, raiz_vm=str(raiz_vm))
        yield gestor, proyecto, Path(gestor.vm)


def test_subir_bajar_y_artefactos(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with sincronizador(agente_compilado, tmp_path) as (gestor, proyecto, vm):
            (proyecto / "main.c").write_bytes(b"int main(void) {\r\n    return 0;\r\n}\r\n")
            (proyecto / "entrada.bmp").write_bytes(b"BM-entrada")
            (proyecto / "prueba.o").write_bytes(b"objeto")
            (proyecto / ".gitignore").write_text("salida/\n")
            (proyecto / "salida").mkdir()
            (proyecto / "salida" / "x.txt").write_text("ignorado")
            assert await gestor.subir() is True
            assert (vm / "main.c").read_bytes() == b"int main(void) {\n    return 0;\n}\n"  # LF en la VM
            assert (vm / "entrada.bmp").exists()
            assert not (vm / "prueba.o").exists() and not (vm / "salida").exists()
            assert await gestor.subir() is False  # nada nuevo

            (proyecto / "main.c").write_text("int main(void) {\n    return 1;\n}\n")
            assert await gestor.subir() is True
            assert "return 1" in (vm / "main.c").read_text()

            # gaff fix en la VM + un programa que genera artefactos
            (vm / "main.c").write_text("int main(void)\n{\n    return 1;\n}\n")
            (vm / "salida.bmp").write_bytes(b"BM-salida")
            (vm / "sub").mkdir()
            (vm / "sub" / "tono.wav").write_bytes(b"RIFF")
            (vm / "binario").write_bytes(b"\x7fELF")
            with pytest.raises(ErrorDeSync, match="cambios que el Host todavía no tiene"):
                await gestor.subir()
            resultado = await gestor.sincronizar()
            assert resultado.bajados and not resultado.conflictos
            assert (proyecto / "main.c").read_text() == "int main(void)\n{\n    return 1;\n}\n"
            artefactos = sorted(p.relative_to(proyecto).as_posix() for p in resultado.artefactos)
            assert artefactos == [".construct/artefactos/salida.bmp", ".construct/artefactos/sub/tono.wav"]
            assert not (proyecto / "binario").exists()  # lo nuevo de la VM no vuelve como código
            assert await gestor.bajar_artefactos() == []  # ya copiados

            estado = await gestor.estado()
            assert not estado.cambios_vm and estado.cambios_host == []

    correr(prueba)


def test_conflicto_de_tres_vias(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with sincronizador(agente_compilado, tmp_path) as (gestor, proyecto, vm):
            (proyecto / "main.c").write_text("linea 1\nlinea 2\nlinea 3\n")
            await gestor.subir()
            (proyecto / "main.c").write_text("linea 1\nlinea 2 del Host\nlinea 3\n")
            (vm / "main.c").write_text("linea 1\nlinea 2 de la VM\nlinea 3\n")
            resultado = await gestor.sincronizar()
            assert resultado.conflictos == ["main.c"]
            texto = (proyecto / "main.c").read_text()
            assert "<<<<<<<" in texto and "del Host" in texto and "de la VM" in texto
            with pytest.raises(ErrorDeSync, match="conflictos sin resolver"):
                await gestor.sincronizar()
            assert (await gestor.estado()).fusion_pendiente
            (proyecto / "main.c").write_text("linea 1\nlinea 2 resuelta\nlinea 3\n")
            final = await gestor.sincronizar()
            assert final.subidos and not final.conflictos
            assert (vm / "main.c").read_text() == "linea 1\nlinea 2 resuelta\nlinea 3\n"

    correr(prueba)


def test_elegir_version_en_un_conflicto(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with sincronizador(agente_compilado, tmp_path) as (gestor, proyecto, vm):
            (proyecto / "a.c").write_text("a\n")
            (proyecto / "b.c").write_text("b\n")
            await gestor.subir()
            (proyecto / "a.c").write_text("a del Host\n")
            (proyecto / "b.c").write_text("b del Host\n")
            (vm / "a.c").write_text("a de la VM\n")
            (vm / "b.c").write_text("b de la VM\n")
            assert (await gestor.sincronizar()).conflictos == ["a.c", "b.c"]
            assert gestor.conflictos() == ["a.c", "b.c"]
            versiones = gestor.versiones("a.c")
            assert (versiones.base, versiones.host, versiones.vm) == ("a\n", "a del Host\n", "a de la VM\n")
            gestor.elegir("a.c", "vm")
            gestor.elegir("b.c", "host")
            assert gestor.conflictos() == []
            final = await gestor.sincronizar()
            assert final.subidos and not final.conflictos
            assert (vm / "a.c").read_text() == "a de la VM\n" and (vm / "b.c").read_text() == "b del Host\n"
            assert gestor.conflictos() == []

    correr(prueba)


def test_cambios_no_conflictivos_se_fusionan(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with sincronizador(agente_compilado, tmp_path) as (gestor, proyecto, vm):
            (proyecto / "a.c").write_text("a\n")
            (proyecto / "b.c").write_text("b\n")
            await gestor.subir()
            (proyecto / "a.c").write_text("a del Host\n")
            (vm / "b.c").write_text("b de la VM\n")
            resultado = await gestor.sincronizar()
            assert not resultado.conflictos and resultado.subidos
            assert (proyecto / "b.c").read_text() == "b de la VM\n"
            assert (vm / "a.c").read_text() == "a del Host\n"

    correr(prueba)


def test_nombre_problematico_frena_la_subida(agente_compilado: Path, tmp_path: Path) -> None:
    async def prueba() -> None:
        async with sincronizador(agente_compilado, tmp_path) as (gestor, proyecto, _):
            (proyecto / "aux.c").write_text("x")
            with pytest.raises(ErrorDeSync, match="reservado en Windows"):
                await gestor.subir()

    correr(prueba)
