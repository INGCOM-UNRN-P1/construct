"""Arranque real de la VM con la imagen construida (checkpoint de la Fase 2; base de la Fase 0).

Se corre sólo si `CONSTRUCT_IMAGEN_PRUEBA` apunta a una carpeta con vmlinuz, initramfs y
rootfs.qcow2 (lo que deja `vm_image/build_vm.sh` en `<salida>/<arch>/`):

    CONSTRUCT_IMAGEN_PRUEBA=dist/imagenes/x86_64 uv run pytest tests/e2e -s

El disco se abre con `snapshot=on`: la prueba no modifica la imagen. Usa el esquema de conexión
del plan (§3.1): el Host escucha en 127.0.0.1 y QEMU se conecta como cliente.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from construct.core.protocolo import PROTOCOLO, Decodificador, Marco, Tipo, codificar
from construct.platform import accel, binarios, host
from construct.platform.host_info import Arquitectura

IMAGEN = os.environ.get("CONSTRUCT_IMAGEN_PRUEBA")
pytestmark = pytest.mark.skipif(not IMAGEN, reason="definí CONSTRUCT_IMAGEN_PRUEBA con la carpeta de la imagen")

CONSOLA = {Arquitectura.X86_64: "ttyS0", Arquitectura.AARCH64: "ttyAMA0"}


class Conexion:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.decodificador = Decodificador()
        self.pendientes: list[Marco] = []

    def enviar(self, tipo: Tipo, id: int = 0, datos: bytes | dict[str, Any] = b"") -> None:
        self.sock.sendall(codificar(tipo, id, datos))

    def recibir(self, timeout: float) -> Marco:
        limite = time.monotonic() + timeout
        while not self.pendientes:
            self.sock.settimeout(max(0.01, limite - time.monotonic()))
            datos = self.sock.recv(65536)
            if not datos:
                raise ConnectionError("QEMU cerró el canal")
            self.pendientes += self.decodificador.alimentar(datos)
        return self.pendientes.pop(0)

    def ejecutar(self, id: int, argv: list[str], timeout: float = 120) -> tuple[str, str, dict[str, Any]]:
        self.enviar(Tipo.EJECUTAR, id, {"argv": argv})
        salida, errores = b"", b""
        while True:
            marco = self.recibir(timeout)
            if marco.tipo is Tipo.LATIDO_OK or marco.id != id:
                continue
            if marco.tipo is Tipo.SALIDA:
                salida += marco.datos
            elif marco.tipo is Tipo.ERRORES:
                errores += marco.datos
            elif marco.tipo is Tipo.TERMINADO:
                return salida.decode(errors="replace"), errores.decode(errors="replace"), marco.json()


@pytest.fixture(scope="module")
def vm(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Conexion, dict[str, Any], float]]:
    carpeta = Path(IMAGEN or "")
    elegida = accel.detectar(host())
    qemu = binarios.buscar_qemu(host(), elegida.arquitectura_guest)
    assert qemu is not None, "no se encontró QEMU"
    token = secrets.token_hex(16)
    registro = tmp_path_factory.mktemp("vm") / "consola.log"

    servidor = socket.create_server(("127.0.0.1", 0))
    servidor.settimeout(120)
    puerto = servidor.getsockname()[1]
    comando = [
        qemu.ruta, *elegida.argumentos_qemu(), "-m", "1024", "-smp", "2",
        "-nodefaults", "-no-user-config", "-display", "none", "-nic", "none",
        "-serial", f"file:{registro}",
        "-kernel", str(carpeta / "vmlinuz"), "-initrd", str(carpeta / "initramfs"),
        "-append", f"root=/dev/vda rootfstype=ext4 modules=virtio_blk,ext4 console={CONSOLA[elegida.arquitectura_guest]}",
        "-drive", f"file={carpeta / 'rootfs.qcow2'},if=virtio,format=qcow2,snapshot=on",
        "-device", "virtio-serial-pci",
        "-chardev", f"socket,id=ctl,host=127.0.0.1,port={puerto}",
        "-device", "virtserialport,chardev=ctl,name=construct-ctl",
        "-fw_cfg", f"name=opt/construct/token,string={token}",
    ]
    inicio = time.monotonic()
    proceso = subprocess.Popen(comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        sock, _ = servidor.accept()
        conexion = Conexion(sock)
        hola = conexion.recibir(timeout=120)
        demora = time.monotonic() - inicio
        datos = hola.json()
        assert hola.tipo is Tipo.HOLA and datos["token"] == token, datos
        conexion.enviar(Tipo.BIENVENIDA, 0, {"protocolo": PROTOCOLO, "latido_s": 30})
        print(f"\nacelerador: {elegida.acelerador.value}; HOLA a los {demora:.2f} s; {datos}")
        yield conexion, datos, demora
        conexion.enviar(Tipo.APAGAR)
        proceso.wait(timeout=60)
    except BaseException:
        print(registro.read_text(errors="replace")[-4000:] if registro.exists() else "(sin consola)")
        raise
    finally:
        if proceso.poll() is None:
            proceso.kill()
            proceso.wait()
        servidor.close()


def test_handshake(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    _, hola, demora = vm
    assert hola["protocolo"] == PROTOCOLO
    assert hola["arquitectura"] == accel.detectar(host()).arquitectura_guest.value
    print(f"tiempo hasta el handshake: {demora:.2f} s")


def test_usuario_y_workspace(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    conexion, _, _ = vm
    salida, _, fin = conexion.ejecutar(1, ["sh", "-c", "id -un; pwd; cat /etc/construct-imagen"])
    assert fin["codigo"] == 0
    assert salida.splitlines()[:2] == ["estudiante", "/workspace"]
    assert "protocolo=1" in salida


def test_cgroups_v2(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    conexion, _, _ = vm
    # (el stat de busybox no conoce el nombre de cgroup2: se mira /proc/mounts)
    script = "grep ' /sys/fs/cgroup ' /proc/mounts; cat /sys/fs/cgroup/cgroup.controllers"
    salida, _, _ = conexion.ejecutar(2, ["sh", "-c", script])
    montaje, controladores = salida.splitlines()[:2]
    assert montaje.split()[2] == "cgroup2"
    assert {"cpu", "memory", "pids"} <= set(controladores.split())


PROGRAMAS = ["gcc", "gdb", "valgrind", "bwrap", "make", "git", "jonesy",
             "mother", "daedalus", "ripley", "gaff", "spunkmeyer", "kaneda", "nostromo", "hal", "bishop", "tetsuo"]


@pytest.mark.parametrize("programa", PROGRAMAS)
def test_herramientas_del_perfil(vm: tuple[Conexion, dict[str, Any], float], programa: str) -> None:
    conexion, _, _ = vm
    salida, errores, fin = conexion.ejecutar(100 + PROGRAMAS.index(programa), [programa, "--version"])
    assert fin["codigo"] == 0, (salida, errores)


def test_compilar_con_librerias_de_la_catedra(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    conexion, _, _ = vm
    programa = ('#include <p1_test.h>\\n#include <bitmaps/libreria.h>\\n#include <sonido/libreria.h>\\n'
                'int main(void) { return 0; }\\n')
    script = (f"printf '{programa}' > prueba.c && gcc -std=c11 -Wall -Wextra -Werror -pedantic prueba.c "
              "-lp1_test -lbitmaps -lsonido -lm -o prueba && ./prueba")
    _, errores, fin = conexion.ejecutar(3, ["sh", "-c", script])
    assert fin["codigo"] == 0, errores


def test_mother_doctor_del_guest(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    conexion, _, _ = vm
    salida, errores, _ = conexion.ejecutar(4, ["mother", "doctor", "--perfil", "estudiante", "--json"], timeout=300)
    print(salida[-3000:], errores[-2000:])
    assert '"schema_version"' in salida


@pytest.mark.xfail(strict=True, reason="el gcc de Alpine (musl) no trae libasan ni libubsan: tetsuo y el paso "
                                       "AddressSanitizer de ripley no funcionan (decisión pendiente sobre la base)")
def test_sanitizers(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    conexion, _, _ = vm
    script = ("printf 'int main(void) { return 0; }' > s.c && "
              "gcc -fsanitize=address,undefined -g s.c -o s && ./s")
    _, errores, fin = conexion.ejecutar(5, ["sh", "-c", script])
    assert fin["codigo"] == 0, errores
