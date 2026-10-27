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

from construct.comandos import argv_entrar
from construct.core.protocolo import PROTOCOLO, Decodificador, Marco, Tipo, codificar
from construct.platform import accel, binarios, host
from construct.platform.host_info import Arquitectura

IMAGEN = os.environ.get("CONSTRUCT_IMAGEN_PRUEBA")
FACTOR_TCG = 6
LATIDO_S = 30
"""El agente corta la sesión (y apaga la VM) tras 3 × LATIDO_S sin nada del Host, como en una sesión real."""

pytestmark = pytest.mark.skipif(not IMAGEN, reason="definí CONSTRUCT_IMAGEN_PRUEBA con la carpeta de la imagen")

CONSOLA = {Arquitectura.X86_64: "ttyS0", Arquitectura.AARCH64: "ttyAMA0"}


class Conexion:
    def __init__(self, sock: socket.socket, factor: float = 1) -> None:
        self.sock = sock
        self.factor = factor  # con TCG todo tarda varias veces más
        self.decodificador = Decodificador()
        self.pendientes: list[Marco] = []
        self.ultimo_envio = time.monotonic()
        self.latidos = False  # recién después de la BIENVENIDA

    def enviar(self, tipo: Tipo, id: int = 0, datos: bytes | dict[str, Any] = b"") -> None:
        self.sock.sendall(codificar(tipo, id, datos))
        self.ultimo_envio = time.monotonic()

    def recibir(self, timeout: float) -> Marco:
        """Espera un marco mandando latidos mientras tanto: bajo TCG, `mother doctor` dura minutos."""
        limite = time.monotonic() + timeout * self.factor
        while not self.pendientes:
            ahora = time.monotonic()
            if ahora >= limite:
                raise TimeoutError(f"sin respuesta del agente en {timeout * self.factor:.0f} s")
            if self.latidos and ahora - self.ultimo_envio >= LATIDO_S / 3:
                self.enviar(Tipo.LATIDO)
            self.sock.settimeout(max(0.01, min(limite, self.ultimo_envio + LATIDO_S / 3) - ahora))
            try:
                datos = self.sock.recv(65536)
            except TimeoutError:
                continue
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
    factor = FACTOR_TCG if elegida.compatibilidad else 1  # runners de CI sin WHPX/HVF (PLAN §5.1 e2e-tcg)
    servidor.settimeout(120 * factor)
    puerto = servidor.getsockname()[1]
    comando = [
        qemu.ruta, *elegida.argumentos_qemu(), "-m", "1024", "-smp", "2",
        "-nodefaults", "-no-user-config", "-display", "none", "-nic", "none",
        "-serial", f"file:{registro}",
        "-kernel", str(carpeta / "vmlinuz"), "-initrd", str(carpeta / "initramfs"),
        "-append", f"root=/dev/vda rootfstype=ext4 rw console={CONSOLA[elegida.arquitectura_guest]}",
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
        conexion = Conexion(sock, factor)
        hola = conexion.recibir(timeout=120)
        demora = time.monotonic() - inicio
        datos = hola.json()
        assert hola.tipo is Tipo.HOLA and datos["token"] == token, datos
        conexion.enviar(Tipo.BIENVENIDA, 0, {"protocolo": PROTOCOLO, "latido_s": LATIDO_S})
        conexion.latidos = True
        print(f"\nacelerador: {elegida.acelerador.value}; HOLA a los {demora:.2f} s; {datos}")
        yield conexion, datos, demora
        conexion.enviar(Tipo.APAGAR)
        proceso.wait(timeout=60 * factor)
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


COMANDOS_DEL_ENTORNO = ("ayuda", "nuevo-proyecto", "verificar", "entregar", "configure-git.sh",
                        "customize-terminal.sh", "doctor")


def test_entorno_de_la_catedra(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    """La terminal de `construct entrar`: el entorno de la cátedra activo, como en la máquina del alumno."""
    conexion, _, _ = vm
    script = ('printf "raiz=%s home=%s\\n" "$PORTABLE_ROOT" "$(cd ~ && pwd -P)"; '
              'for c in "$@"; do command -v "$c" >/dev/null || echo "falta $c"; done; '
              'ripley --version >/dev/null || echo "falta ripley"; ls -a ~')
    salida, errores, fin = conexion.ejecutar(7, argv_entrar(["sh", "-c", script, "sh", *COMANDOS_DEL_ENTORNO]))
    assert fin.get("codigo") == 0, (salida, errores)
    assert "raiz=/opt/p1/entorno home=/home/estudiante" in salida and "falta" not in salida, salida
    assert {".bashrc", ".bash_profile", ".gdbinit"} <= set(salida.split()), salida


def test_bienvenida_del_entorno(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    conexion, _, _ = vm
    salida, errores, _ = conexion.ejecutar(8, ["bash", "--login", "-c", "true"])
    assert "UNRN Andina - Programación 1" in salida and "GUÍA RÁPIDA DEL ENTORNO" in salida, (salida, errores)
    assert "Entorno Portable activo" not in salida  # el resumen de activate.sh se calla en la VM


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


def test_preload_de_telemetria(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    conexion, _, _ = vm
    script = ("printf '#include <stdlib.h>\\nint main(void) { return malloc(8) == 0; }\\n' > f.c && gcc -g f.c -o f && "
              "CONSTRUCT_INFORME=/tmp/i.json LD_PRELOAD=/usr/local/lib/libconstruct_preload.so ./f && cat /tmp/i.json")
    salida, errores, fin = conexion.ejecutar(6, ["sh", "-c", script])
    assert fin["codigo"] == 0, errores
    assert '"bloques":1' in salida


def test_sanitizers(vm: tuple[Conexion, dict[str, Any], float]) -> None:
    conexion, _, _ = vm
    script = ("printf 'int main(void) { return 0; }' > s.c && "
              "gcc -fsanitize=address,undefined -g s.c -o s && ./s")
    _, errores, fin = conexion.ejecutar(5, ["sh", "-c", script])
    assert fin["codigo"] == 0, errores
