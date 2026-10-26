"""Fakes de Host para probar cualquier plataforma desde cualquier plataforma."""

from __future__ import annotations

import shutil
import subprocess
import sys
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


FUENTES_AGENTE = Path(__file__).resolve().parent.parent / "vm_image" / "guest_agent"
HAY_AGENTE = sys.platform.startswith("linux") and shutil.which("make") is not None and shutil.which("cc") is not None


@pytest.fixture(scope="session")
def agente_compilado(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """El agente del Guest compilado para el Host (sólo Linux: usa signalfd, forkpty y setfsuid)."""
    if not HAY_AGENTE:
        pytest.skip("el agente usa APIs de Linux y se compila con cc")
    salida = tmp_path_factory.mktemp("agente") / "construct_daemon"
    subprocess.run(["make", "-s", "-C", str(FUENTES_AGENTE), f"SALIDA={salida}"], check=True)
    return salida
