#!/usr/bin/env python3
"""Arma la carpeta de release de las imágenes: archivos con nombre plano, SHA256SUMS e imagenes.json.

Entrada: lo que deja build_vm.sh (DIR/<arch>/{vmlinuz,initramfs,rootfs.qcow2}).
Salida: RELEASE/{vmlinuz-<arch>, initramfs-<arch>, rootfs-<arch>.qcow2, SHA256SUMS, imagenes.json},
lista para subir como assets de un release de GitHub. Las URL del manifiesto son relativas, así que
`construct setup` las resuelve contra la URL del propio manifiesto (y sirve también con una carpeta
local: `construct setup -m RELEASE/imagenes.json`). Esquema en src/construct/core/imagenes.py.

Uso: generar_manifiesto.py DIR --version X.Y.Z --salida RELEASE [--protocolo 1]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ARCHIVOS = (("vmlinuz", "vmlinuz-{arch}"), ("initramfs", "initramfs-{arch}"), ("rootfs.qcow2", "rootfs-{arch}.qcow2"))
ARQUITECTURAS = ("x86_64", "aarch64")


def sha256(ruta: Path) -> str:
    suma = hashlib.sha256()
    with ruta.open("rb") as archivo:
        while bloque := archivo.read(1 << 20):
            suma.update(bloque)
    return suma.hexdigest()


def generar(origen: Path, version: str, salida: Path, protocolo: int) -> dict[str, object]:
    salida.mkdir(parents=True, exist_ok=True)
    imagenes: dict[str, object] = {}
    sumas = []
    for arch in ARQUITECTURAS:
        carpeta = origen / arch
        if not carpeta.is_dir():
            continue
        archivos = []
        for nombre, publicado in ARCHIVOS:
            fuente = carpeta / nombre
            if not fuente.is_file():
                raise SystemExit(f"falta {fuente}")
            destino = salida / publicado.format(arch=arch)
            shutil.copyfile(fuente, destino)
            suma = sha256(destino)
            sumas.append(f"{suma}  {destino.name}")
            archivos.append({"nombre": nombre, "url": destino.name, "sha256": suma, "tamano": destino.stat().st_size})
        imagenes[arch] = {"archivos": archivos}
    if not imagenes:
        raise SystemExit(f"no hay imágenes en {origen} (se esperaba {origen}/x86_64 o {origen}/aarch64)")
    manifiesto = {"schema_version": "1.0.0", "version": version, "protocolo": protocolo, "imagenes": imagenes}
    (salida / "imagenes.json").write_text(json.dumps(manifiesto, indent=2) + "\n", encoding="utf-8")
    (salida / "SHA256SUMS").write_text("\n".join(sumas) + "\n", encoding="utf-8")
    return manifiesto


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("origen", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--salida", type=Path, required=True)
    parser.add_argument("--protocolo", type=int, default=1)
    args = parser.parse_args(argv)
    manifiesto = generar(args.origen, args.version, args.salida, args.protocolo)
    print(f"imagenes.json {args.version}: {', '.join(manifiesto['imagenes'])} en {args.salida}")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
