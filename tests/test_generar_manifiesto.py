"""El manifiesto que arma vm_image/generar_manifiesto.py es el que entiende `construct setup`."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from construct.core import imagenes
from construct.platform.host_info import Arquitectura

RUTA = Path(__file__).resolve().parent.parent / "vm_image" / "generar_manifiesto.py"
_spec = importlib.util.spec_from_file_location("generar_manifiesto", RUTA)
assert _spec is not None and _spec.loader is not None
generar_manifiesto = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generar_manifiesto)


def construir(origen: Path, *arquitecturas: str) -> None:
    for arch in arquitecturas:
        carpeta = origen / arch
        carpeta.mkdir(parents=True)
        for nombre in ("vmlinuz", "initramfs", "rootfs.qcow2"):
            (carpeta / nombre).write_bytes(f"{arch}:{nombre}".encode() * 100)


def test_release_instalable_con_setup(tmp_path: Path) -> None:
    construir(tmp_path / "dist", "x86_64", "aarch64")
    generar_manifiesto.main([str(tmp_path / "dist"), "--version", "0.1.0", "--salida", str(tmp_path / "release")])
    release = tmp_path / "release"
    assert {p.name for p in release.iterdir()} == {
        "imagenes.json", "SHA256SUMS", "vmlinuz-x86_64", "initramfs-x86_64", "rootfs-x86_64.qcow2",
        "vmlinuz-aarch64", "initramfs-aarch64", "rootfs-aarch64.qcow2"}
    manifiesto = imagenes.obtener_manifiesto(str(release / "imagenes.json"))
    imagen, _ = imagenes.instalar(manifiesto, Arquitectura.AARCH64, tmp_path / "instaladas")
    assert imagen.ruta("rootfs.qcow2").read_bytes().startswith(b"aarch64:rootfs.qcow2")
    assert len((release / "SHA256SUMS").read_text().splitlines()) == 6


def test_una_sola_arquitectura(tmp_path: Path) -> None:
    construir(tmp_path / "dist", "x86_64")
    datos = generar_manifiesto.generar(tmp_path / "dist", "0.1.0", tmp_path / "release", 1)
    assert list(datos["imagenes"]) == ["x86_64"]


def test_sin_imagenes(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no hay imágenes"):
        generar_manifiesto.generar(tmp_path, "0.1.0", tmp_path / "release", 1)


def test_imagen_incompleta(tmp_path: Path) -> None:
    construir(tmp_path / "dist", "x86_64")
    (tmp_path / "dist" / "x86_64" / "initramfs").unlink()
    with pytest.raises(SystemExit, match="falta"):
        generar_manifiesto.generar(tmp_path / "dist", "0.1.0", tmp_path / "release", 1)
