from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from construct.core import discos, qemu
from construct.core.imagenes import ImagenInstalada
from construct.core.sesion import InfoSesion, _escribir_info, leer_info, ruta_info
from construct.platform.accel import detectar
from construct.platform.host_info import Arquitectura, Sistema
from construct.platform.paths import directorios
from tests.conftest import crear_host, sondas_aceleracion

QEMU_IMG = shutil.which("qemu-img")


def parametros(arquitectura: Arquitectura = Arquitectura.X86_64, sistema: Sistema = Sistema.LINUX) -> qemu.ParametrosVM:
    elegida = detectar(crear_host(sistema, arquitectura), sondas=sondas_aceleracion())
    return qemu.ParametrosVM(qemu="qemu", aceleracion=elegida, kernel=Path("k"), initramfs=Path("i"),
                             sistema=Path("/datos/vms/sistema.qcow2"), workspace=Path("/datos/a,b/workspace.qcow2"),
                             consola=Path("/logs/consola.log"), puerto_agente=4321, token="t0k")


def test_linea_de_qemu() -> None:
    argv = qemu.argumentos(parametros())
    texto = " ".join(argv)
    assert argv[0] == "qemu" and "-accel kvm" in texto
    assert "socket,id=ctl,host=127.0.0.1,port=4321" in argv  # QEMU es el cliente
    assert "name=opt/construct/token,string=t0k" in argv
    assert "virtserialport,chardev=ctl,name=construct-ctl" in argv
    assert "file=/datos/a,,b/workspace.qcow2,if=virtio,format=qcow2,discard=unmap" in argv  # comas escapadas
    assert "console=ttyS0" in texto and "-nic none" in texto
    assert qemu.escapar(r"C:\datos\a,b\disco.qcow2") == "C:/datos/a,,b/disco.qcow2"


def test_linea_de_qemu_en_apple_silicon() -> None:
    texto = " ".join(qemu.argumentos(parametros(Arquitectura.AARCH64, Sistema.MACOS)))
    assert "-machine virt -accel hvf -cpu host" in texto and "console=ttyAMA0" in texto


def test_backing_relativo_sin_barras_invertidas(tmp_path: Path) -> None:
    ruta = discos.backing_relativo(tmp_path / "vms" / "x86_64" / "sistema.qcow2",
                                   tmp_path / "imagenes" / "x86_64" / "0.1.0" / "rootfs.qcow2")
    assert ruta == "../../imagenes/x86_64/0.1.0/rootfs.qcow2"


@pytest.mark.skipif(QEMU_IMG is None, reason="hace falta qemu-img")
def test_discos_reales(tmp_path: Path) -> None:
    assert QEMU_IMG is not None
    base_dir = tmp_path / "imagenes" / "x86_64" / "1"
    base_dir.mkdir(parents=True)
    subprocess.run([QEMU_IMG, "create", "-q", "-f", "qcow2", str(base_dir / "rootfs.qcow2"), "64M"], check=True)
    imagen = ImagenInstalada(Arquitectura.X86_64, "1", 1, base_dir, {"rootfs.qcow2": "aaa"})
    carpeta = tmp_path / "vms" / "x86_64"
    primera = discos.preparar(carpeta, imagen, QEMU_IMG)
    assert primera.recreado and primera.sistema.is_file() and primera.workspace.is_file()
    info = json.loads(subprocess.run([QEMU_IMG, "info", "--output=json", str(primera.sistema)],
                                     capture_output=True, text=True, check=True).stdout)
    assert info["backing-filename"] == "../../imagenes/x86_64/1/rootfs.qcow2"

    marca_workspace = primera.workspace.stat().st_mtime_ns
    assert not discos.preparar(carpeta, imagen, QEMU_IMG).recreado  # misma base: se conserva
    recompilada = ImagenInstalada(Arquitectura.X86_64, "1", 1, base_dir, {"rootfs.qcow2": "bbb"})
    assert discos.preparar(carpeta, recompilada, QEMU_IMG).recreado  # misma versión, otra base
    assert primera.workspace.stat().st_mtime_ns == marca_workspace  # el trabajo del alumno no se toca


def test_qemu_img_que_falla(tmp_path: Path) -> None:
    imagen = ImagenInstalada(Arquitectura.X86_64, "1", 1, tmp_path, {})
    with pytest.raises(discos.ErrorDeDiscos):
        discos.preparar(tmp_path / "vms", imagen, str(tmp_path / "no-existe-qemu-img"))


def info(pid: int) -> InfoSesion:
    return InfoSesion(pid=pid, puerto=1, token="t", qemu_pid=2, arquitectura="x86_64", acelerador="kvm",
                      imagen="1", agente="0.1.0", inicio=0.0, version="0.1.0")


def test_registro_de_sesion() -> None:
    dirs = directorios()
    dirs.crear()
    assert leer_info(dirs) is None
    _escribir_info(dirs, info(os.getpid()))
    assert leer_info(dirs) == info(os.getpid())
    if os.name == "posix":
        assert ruta_info(dirs).stat().st_mode & 0o777 == 0o600  # tiene el token


def test_sesion_muerta_o_archivo_roto_se_descartan() -> None:
    dirs = directorios()
    dirs.crear()
    _escribir_info(dirs, info(2**22 + 12345))
    assert leer_info(dirs) is None and not ruta_info(dirs).exists()
    ruta_info(dirs).write_text("{roto", encoding="utf-8")
    assert leer_info(dirs) is None and not ruta_info(dirs).exists()
