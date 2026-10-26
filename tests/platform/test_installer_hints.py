from __future__ import annotations

import pytest

from construct.platform.host_info import Arquitectura, Distro, Sistema
from construct.platform.installer_hints import Componente, sugerencia
from tests.conftest import FEDORA, crear_host


@pytest.mark.parametrize(("distro", "comando"), [
    (Distro("ubuntu", ("debian",)), "sudo apt install qemu-system-x86"),
    (FEDORA, "sudo dnf install qemu-system-x86"),
    (Distro("arch"), "sudo pacman -S qemu-system-x86"),
    (Distro("opensuse-leap", ("suse", "opensuse")), "sudo zypper install qemu-x86"),
])
def test_qemu_por_distro(distro: Distro, comando: str) -> None:
    assert comando in sugerencia(Componente.QEMU, crear_host(distro=distro))


def test_qemu_aarch64_en_debian() -> None:
    texto = sugerencia(Componente.QEMU, crear_host(arquitectura=Arquitectura.AARCH64), Arquitectura.AARCH64)
    assert "qemu-system-arm" in texto


def test_qemu_en_distro_desconocida() -> None:
    assert "gestor de paquetes" in sugerencia(Componente.QEMU, crear_host(distro=Distro("nixos")))


@pytest.mark.parametrize(("sistema", "fragmento"), [
    (Sistema.WINDOWS, "winget install --id SoftwareFreedomConservancy.QEMU"),
    (Sistema.MACOS, "brew install qemu"),
])
def test_qemu_en_windows_y_mac(sistema: Sistema, fragmento: str) -> None:
    assert fragmento in sugerencia(Componente.QEMU, crear_host(sistema))


def test_whpx_explica_la_caracteristica_y_el_bios() -> None:
    texto = sugerencia(Componente.WHPX, crear_host(Sistema.WINDOWS))
    assert "HypervisorPlatform" in texto and "BIOS" in texto


def test_kvm_en_wsl_recomienda_windows_nativo() -> None:
    assert "no dentro de WSL" in sugerencia(Componente.KVM_AUSENTE, crear_host(wsl=True))


def test_gdb_dev_en_apple_silicon_remite_al_guest() -> None:
    assert "Apple Silicon" in sugerencia(Componente.GDB_DEV, crear_host(Sistema.MACOS, Arquitectura.AARCH64))


@pytest.mark.parametrize("componente", list(Componente))
@pytest.mark.parametrize("sistema", list(Sistema))
def test_hay_sugerencia_para_todo(componente: Componente, sistema: Sistema) -> None:
    assert sugerencia(componente, crear_host(sistema)).strip()
