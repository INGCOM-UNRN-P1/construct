"""Fakes de Host para probar cualquier plataforma desde cualquier plataforma."""

from __future__ import annotations

from pathlib import Path

import pytest

from construct.platform.accel import SondasAceleracion
from construct.platform.binarios import SondasBinarios
from construct.platform.host_info import Arquitectura, Distro, HostInfo, Sistema

FEDORA = Distro("fedora", (), "Fedora Linux 44")
UBUNTU = Distro("ubuntu", ("debian",), "Ubuntu 24.04 LTS")


def crear_host(
    sistema: Sistema = Sistema.LINUX,
    arquitectura: Arquitectura | None = Arquitectura.X86_64,
    *,
    emulado: bool = False,
    wsl: bool = False,
    distro: Distro | None = None,
    ram_mb: int | None = 8192,
) -> HostInfo:
    nativa = arquitectura.value if arquitectura else "i686"
    return HostInfo(
        sistema=sistema,
        arquitectura=arquitectura,
        maquina="x86_64" if emulado else nativa,
        maquina_nativa=nativa,
        emulado=emulado,
        wsl=wsl,
        distro=distro if distro is not None else (UBUNTU if sistema is Sistema.LINUX else None),
        cpus=4,
        ram_mb=ram_mb,
    )


def sondas_aceleracion(*, kvm: tuple[bool, str] = (True, "ok"), whpx: tuple[bool, str] = (True, "ok"),
                       hvf: tuple[bool, str] = (True, "ok")) -> SondasAceleracion:
    return SondasAceleracion(kvm=lambda: kvm, whpx=lambda: whpx, hvf=lambda: hvf)


def sondas_binarios(archivos: dict[str, str], *, path: dict[str, str] | None = None,
                    entorno: dict[str, str] | None = None) -> SondasBinarios:
    """`archivos`: ruta → primera línea de `--version`; `path`: nombre → ruta que da `which`."""
    return SondasBinarios(
        which=lambda nombre: (path or {}).get(nombre),
        es_archivo=lambda ruta: ruta in archivos,
        version=lambda ruta, _args: archivos.get(ruta),
        entorno=entorno or {},
    )


@pytest.fixture(autouse=True)
def construct_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Ningún test toca las carpetas reales del usuario."""
    raiz = tmp_path / "construct-home"
    monkeypatch.setenv("CONSTRUCT_HOME", str(raiz))
    return raiz
