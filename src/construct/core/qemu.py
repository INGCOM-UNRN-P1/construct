"""Línea de comandos de QEMU para el Guest (PLAN §3.1).

Función pura: recibe todo resuelto (acelerador, rutas, puerto, token) y devuelve el `argv`, así se
prueba igual en cualquier plataforma.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from construct.platform.accel import Aceleracion
from construct.platform.host_info import Arquitectura

CONSOLA = {Arquitectura.X86_64: "ttyS0", Arquitectura.AARCH64: "ttyAMA0"}
NOMBRE_PUERTO = "construct-ctl"


@dataclass(frozen=True)
class ParametrosVM:
    qemu: str
    aceleracion: Aceleracion
    kernel: Path
    initramfs: Path
    sistema: Path
    """Overlay qcow2 del disco del sistema (backing: la imagen base)."""
    workspace: Path
    consola: Path
    """Archivo donde queda la consola serie (diagnóstico)."""
    puerto_agente: int
    token: str
    memoria_mb: int = 1024
    cpus: int = 2


def escapar(valor: str | Path) -> str:
    """Valor de una opción de QEMU: las comas se duplican (`a,b` → `a,,b`) y las barras son `/`."""
    texto = valor.as_posix() if isinstance(valor, Path) else str(valor).replace("\\", "/")
    return texto.replace(",", ",,")


def argumentos(p: ParametrosVM) -> list[str]:
    arq = p.aceleracion.arquitectura_guest
    return [
        p.qemu, *p.aceleracion.argumentos_qemu(),
        "-m", str(p.memoria_mb), "-smp", str(p.cpus),
        "-nodefaults", "-no-user-config", "-display", "none", "-nic", "none",
        "-chardev", f"file,id=consola,path={escapar(p.consola)}", "-serial", "chardev:consola",
        "-kernel", str(p.kernel), "-initrd", str(p.initramfs),
        "-append", f"root=/dev/vda rootfstype=ext4 rw console={CONSOLA[arq]} quiet",
        "-drive", f"file={escapar(p.sistema)},if=virtio,format=qcow2,discard=unmap",
        "-drive", f"file={escapar(p.workspace)},if=virtio,format=qcow2,discard=unmap",
        "-device", "virtio-serial-pci",
        "-chardev", f"socket,id=ctl,host=127.0.0.1,port={p.puerto_agente}",
        "-device", f"virtserialport,chardev=ctl,name={NOMBRE_PUERTO}",
        "-fw_cfg", f"name=opt/construct/token,string={p.token}",
    ]
