"""Cómo resolver lo que falta, con el comando exacto de cada sistema y distro (PLAN Fase 1).

`doctor` las muestra como sugerencia de cada chequeo fallido. construct nunca instala nada con
privilegios por su cuenta: sólo dice qué ejecutar.
"""

from __future__ import annotations

from enum import StrEnum

from construct.platform.host_info import Arquitectura, HostInfo, Sistema


class Componente(StrEnum):
    QEMU = "qemu"
    GIT = "git"
    KVM_PERMISOS = "kvm-permisos"
    KVM_AUSENTE = "kvm-ausente"
    WHPX = "whpx"
    HVF = "hvf"
    GDB_DEV = "gdb-dev"
    PYTHON_NATIVO = "python-nativo"
    WSL = "wsl"


_GESTOR = {
    "debian": "sudo apt install",
    "fedora": "sudo dnf install",
    "arch": "sudo pacman -S",
    "suse": "sudo zypper install",
    "alpine": "sudo apk add",
}

_PAQUETE_QEMU = {
    "debian": {Arquitectura.X86_64: "qemu-system-x86", Arquitectura.AARCH64: "qemu-system-arm"},
    "fedora": {Arquitectura.X86_64: "qemu-system-x86", Arquitectura.AARCH64: "qemu-system-aarch64"},
    "arch": {Arquitectura.X86_64: "qemu-system-x86", Arquitectura.AARCH64: "qemu-system-aarch64"},
    "suse": {Arquitectura.X86_64: "qemu-x86", Arquitectura.AARCH64: "qemu-arm"},
    "alpine": {Arquitectura.X86_64: "qemu-system-x86_64", Arquitectura.AARCH64: "qemu-system-aarch64"},
}

_PAQUETE_GDB = {"debian": "gdb-multiarch", "fedora": "gdb", "arch": "gdb", "suse": "gdb", "alpine": "gdb-multiarch"}


def _familia(host: HostInfo) -> str:
    return host.distro.familia() if host.distro is not None else "otra"


def _instalar_linux(host: HostInfo, paquete: str, generico: str) -> str:
    gestor = _GESTOR.get(_familia(host))
    if gestor is None:
        return f"Instalá {generico} con el gestor de paquetes de tu distribución."
    return f"Ejecutá `{gestor} {paquete}`."


def sugerencia(componente: Componente, host: HostInfo, arquitectura: Arquitectura | None = None) -> str:
    arq = arquitectura or host.arquitectura or Arquitectura.X86_64
    sistema = host.sistema

    if componente is Componente.QEMU:
        if sistema is Sistema.WINDOWS:
            return ("Ejecutá `winget install --id SoftwareFreedomConservancy.QEMU -e` (o bajá el instalador de "
                    "https://qemu.weilnetz.de); construct lo encuentra en C:\\Program Files\\qemu aunque no esté "
                    "en el PATH.")
        if sistema is Sistema.MACOS:
            return "Ejecutá `brew install qemu` (Homebrew: https://brew.sh)."
        paquete = _PAQUETE_QEMU.get(_familia(host), {}).get(arq, f"qemu-system-{arq.value}")
        return _instalar_linux(host, paquete, f"qemu-system-{arq.value}")

    if componente is Componente.GIT:
        if sistema is Sistema.WINDOWS:
            return "Ejecutá `winget install --id Git.Git -e` y abrí una terminal nueva."
        if sistema is Sistema.MACOS:
            return "Ejecutá `xcode-select --install` o `brew install git`."
        return _instalar_linux(host, "git", "git")

    if componente is Componente.KVM_PERMISOS:
        return ("Agregá tu usuario al grupo kvm con `sudo usermod -aG kvm $USER` y cerrá la sesión y volvé a "
                "entrar para que tome el grupo nuevo.")

    if componente is Componente.KVM_AUSENTE:
        if host.wsl:
            return sugerencia(Componente.WSL, host)
        return ("Activá la virtualización (Intel VT-x o AMD-V/SVM) en el BIOS/UEFI y verificá que esté cargado "
                "el módulo del kernel (`sudo modprobe kvm_intel` o `sudo modprobe kvm_amd`). Si estás dentro de "
                "otra máquina virtual, habilitá la virtualización anidada en el anfitrión.")

    if componente is Componente.WHPX:
        return ("Abrí PowerShell como administrador, ejecutá "
                "`Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform` y reiniciá. Si sigue "
                "sin aparecer, activá la virtualización (Intel VT-x o AMD-V/SVM) en el BIOS/UEFI.")

    if componente is Componente.HVF:
        return ("La Mac no expone Hypervisor.framework (kern.hv_support distinto de 1). Si estás dentro de una "
                "máquina virtual, ejecutá construct directamente en la Mac.")

    if componente is Componente.GDB_DEV:
        if sistema is Sistema.MACOS and host.arquitectura is Arquitectura.AARCH64:
            return ("GDB no soporta Apple Silicon: en esta Mac usá el GDB del Guest (el modo por defecto, sin "
                    "--dev).")
        if sistema is Sistema.MACOS:
            return "Ejecutá `brew install gdb` (requiere firmar el binario para depurar procesos locales)."
        if sistema is Sistema.WINDOWS:
            return ("Instalá MSYS2 (https://www.msys2.org) y, desde la terminal UCRT64, ejecutá "
                    "`pacman -S mingw-w64-ucrt-x86_64-gdb-multiarch`.")
        return _instalar_linux(host, _PAQUETE_GDB.get(_familia(host), "gdb"), "gdb con soporte multiarquitectura")

    if componente is Componente.PYTHON_NATIVO:
        return ("Tu Python corre emulado sobre otra arquitectura. Reinstalá uv para la arquitectura nativa "
                "(https://docs.astral.sh/uv/) y reinstalá construct con `uv tool install --reinstall`.")

    if componente is Componente.WSL:
        return ("Ejecutá construct directamente en Windows (PowerShell o Windows Terminal), no dentro de WSL: "
                "WSL no expone la aceleración por hardware salvo con virtualización anidada.")

    raise ValueError(f"componente desconocido: {componente}")
