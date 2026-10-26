"""Discos de trabajo de la VM (PLAN §2.2): overlay del sistema y `workspace.qcow2`.

- `vms/<arch>/sistema.qcow2`: overlay sobre la imagen base, con el `backing_file` **relativo**
  (sin rutas absolutas del Host, ni barras de Windows, dentro del disco). Si cambia la imagen base
  (su SHA-256, no sólo la versión: una imagen recompilada invalida el overlay), se rehace y el
  sistema vuelve a quedar limpio.
- `vms/<arch>/workspace.qcow2`: el disco del alumno (`/workspace`). Nunca se borra solo; lo
  formatea el Guest la primera vez.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from construct.core.imagenes import ImagenInstalada

TAMANO_WORKSPACE = "8G"


class ErrorDeDiscos(ValueError):
    """No se pudieron preparar los discos de la VM."""


@dataclass(frozen=True)
class Discos:
    sistema: Path
    workspace: Path
    recreado: bool
    """El overlay del sistema se rehízo (primera vez o imagen nueva)."""


def _qemu_img(qemu_img: str, *argumentos: str) -> None:
    try:
        salida = subprocess.run([qemu_img, *argumentos], capture_output=True, text=True, check=False,
                                encoding="utf-8", errors="replace")
    except OSError as error:
        raise ErrorDeDiscos(f"no se pudo ejecutar {qemu_img}: {error.strerror}.") from None
    if salida.returncode != 0:
        raise ErrorDeDiscos(f"qemu-img {argumentos[0]} falló: {salida.stderr.strip() or salida.stdout.strip()}")


def backing_relativo(overlay: Path, base: Path) -> str:
    """Ruta de `base` relativa a la carpeta de `overlay`, siempre con `/`."""
    return os.path.relpath(base, overlay.parent).replace("\\", "/")


def preparar(carpeta: Path, imagen: ImagenInstalada, qemu_img: str) -> Discos:
    carpeta.mkdir(parents=True, exist_ok=True)
    sistema = carpeta / "sistema.qcow2"
    workspace = carpeta / "workspace.qcow2"
    estado = carpeta / "sistema.json"
    base = imagen.ruta("rootfs.qcow2")

    try:
        anterior = json.loads(estado.read_text(encoding="utf-8")) if estado.is_file() else {}
    except (OSError, json.JSONDecodeError):
        anterior = {}
    huella = imagen.archivos.get("rootfs.qcow2", imagen.version)
    recrear = not sistema.is_file() or anterior.get("base") != huella
    if recrear:
        temporal = carpeta / "sistema.qcow2.parcial"
        temporal.unlink(missing_ok=True)
        _qemu_img(qemu_img, "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", backing_relativo(temporal, base),
                  str(temporal))
        os.replace(temporal, sistema)
        estado.write_text(json.dumps({"imagen": imagen.version, "base": huella}), encoding="utf-8")
    if not workspace.is_file():
        temporal = carpeta / "workspace.qcow2.parcial"
        temporal.unlink(missing_ok=True)
        _qemu_img(qemu_img, "create", "-q", "-f", "qcow2", str(temporal), TAMANO_WORKSPACE)
        os.replace(temporal, workspace)
    return Discos(sistema, workspace, recrear)
