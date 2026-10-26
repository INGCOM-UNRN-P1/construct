"""Aceleración por hardware para QEMU: KVM, WHPX, HVF o, si no hay, TCG (PLAN §2.1, decisión D1).

El Guest usa la arquitectura del hardware del Host (`x86_64` o `aarch64`), que es la única que se
puede acelerar. Forzar otra arquitectura (o `accel = "tcg"`) deja la VM en *modo compatibilidad*:
emulada por TCG, exacta pero varias veces más lenta.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from construct.platform.host_info import Arquitectura, HostInfo, Sistema, sysctl


class Acelerador(StrEnum):
    KVM = "kvm"
    WHPX = "whpx"
    HVF = "hvf"
    TCG = "tcg"


NOMBRES_ACELERADOR = {
    Acelerador.KVM: "KVM",
    Acelerador.WHPX: "WHPX (Plataforma del hipervisor de Windows)",
    Acelerador.HVF: "HVF (Hypervisor.framework)",
    Acelerador.TCG: "TCG (emulación, modo compatibilidad)",
}

ACELERADOR_DEL_SISTEMA = {Sistema.LINUX: Acelerador.KVM, Sistema.WINDOWS: Acelerador.WHPX,
                          Sistema.MACOS: Acelerador.HVF}

Prueba = tuple[bool, str]
"""(disponible, detalle) de un acelerador."""


@dataclass(frozen=True)
class SondasAceleracion:
    kvm: Callable[[], Prueba]
    whpx: Callable[[], Prueba]
    hvf: Callable[[], Prueba]


@dataclass(frozen=True)
class Aceleracion:
    acelerador: Acelerador
    arquitectura_guest: Arquitectura
    esperado: Acelerador | None
    """El acelerador por hardware que corresponde a este Host, o None si no hay ninguno soportado."""
    detalle: str
    forzado: bool = False
    """El usuario pidió TCG o una arquitectura distinta de la del Host en `project.toml`."""

    @property
    def compatibilidad(self) -> bool:
        return self.acelerador is Acelerador.TCG

    @property
    def binario_qemu(self) -> str:
        return f"qemu-system-{self.arquitectura_guest.value}"

    def argumentos_qemu(self) -> list[str]:
        """Máquina, acelerador y CPU. Valores de partida: se confirman en la Fase 0 del plan."""
        maquina = "q35" if self.arquitectura_guest is Arquitectura.X86_64 else "virt"
        accel = {Acelerador.WHPX: "whpx,kernel-irqchip=off"}.get(self.acelerador, self.acelerador.value)
        cpu = "host" if self.acelerador in (Acelerador.KVM, Acelerador.HVF) else "max"
        return ["-machine", maquina, "-accel", accel, "-cpu", cpu]


# --- Pruebas reales ------------------------------------------------------------------------------

KVM_GET_API_VERSION = 0xAE00  # _IO(KVMIO, 0x00)
KVM_API_VERSION = 12


def probar_kvm(ruta: str = "/dev/kvm") -> Prueba:
    if not os.path.exists(ruta):
        return False, f"no existe {ruta}"
    try:
        descriptor = os.open(ruta, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    except PermissionError:
        return False, f"sin permiso de lectura y escritura sobre {ruta}"
    except OSError as error:
        return False, f"no se pudo abrir {ruta}: {error.strerror}"
    try:
        import fcntl

        version = fcntl.ioctl(descriptor, KVM_GET_API_VERSION)
    except ImportError:
        return False, "KVM sólo existe en Linux"
    except OSError as error:
        return False, f"{ruta} no responde como KVM: {error.strerror}"
    finally:
        os.close(descriptor)
    if version != KVM_API_VERSION:
        return False, f"versión de la API de KVM inesperada ({version})"
    return True, f"{ruta} accesible (API {version})"


WHV_CAPABILITY_CODE_HYPERVISOR_PRESENT = 0


def probar_whpx() -> Prueba:
    try:
        # getattr: ctypes.WinDLL sólo existe en Windows y mypy lo rechaza en el resto.
        biblioteca = getattr(ctypes, "WinDLL")("WinHvPlatform.dll")  # noqa: B009
    except (AttributeError, OSError):
        return False, "la característica «Plataforma del hipervisor de Windows» no está instalada"
    presente = ctypes.c_int(0)
    escrito = ctypes.c_uint32(0)
    resultado = biblioteca.WHvGetCapability(WHV_CAPABILITY_CODE_HYPERVISOR_PRESENT, ctypes.byref(presente),
                                            ctypes.sizeof(presente), ctypes.byref(escrito))
    if resultado != 0:
        return False, f"WHvGetCapability falló (HRESULT 0x{resultado & 0xFFFFFFFF:08X})"
    if not presente.value:
        return False, "la Plataforma del hipervisor está instalada pero el hipervisor no está activo"
    return True, "hipervisor de Windows presente"


def probar_hvf() -> Prueba:
    valor = sysctl("kern.hv_support")
    if valor == "1":
        return True, "kern.hv_support = 1"
    return False, f"kern.hv_support = {valor or 'desconocido'}"


def sondas_reales() -> SondasAceleracion:
    return SondasAceleracion(kvm=probar_kvm, whpx=probar_whpx, hvf=probar_hvf)


# --- Selección -----------------------------------------------------------------------------------


def acelerador_esperado(host: HostInfo, arquitectura: Arquitectura) -> Acelerador | None:
    """El acelerador por hardware para un Guest de `arquitectura`, si este Host tiene uno soportado."""
    if host.arquitectura is not arquitectura:
        return None  # Otra arquitectura sólo se puede emular.
    if host.sistema is Sistema.WINDOWS and arquitectura is Arquitectura.AARCH64:
        return None  # WHPX para Guests aarch64 todavía no está validado (PLAN §1.2).
    if host.sistema is Sistema.LINUX and host.wsl:
        return Acelerador.KVM  # Sólo con virtualización anidada habilitada en WSL2.
    return ACELERADOR_DEL_SISTEMA[host.sistema]


def detectar(
    host: HostInfo,
    *,
    acelerador: str = "auto",
    arquitectura: str = "auto",
    sondas: SondasAceleracion | None = None,
) -> Aceleracion:
    """Elige acelerador y arquitectura del Guest; `acelerador`/`arquitectura` vienen de `project.toml`."""
    if arquitectura == "auto":
        if host.arquitectura is None:
            raise ValueError(
                f"la arquitectura del Host ({host.maquina_nativa}) no es x86_64 ni aarch64: "
                "configurá `[vm] arch` en project.toml para usar el modo compatibilidad."
            )
        arq = host.arquitectura
    else:
        try:
            arq = Arquitectura(arquitectura)
        except ValueError:
            raise ValueError(f"`[vm] arch` tiene que ser auto, x86_64 o aarch64 (no «{arquitectura}»).") from None
    try:
        pedido = None if acelerador == "auto" else Acelerador(acelerador)
    except ValueError:
        raise ValueError(f"`[vm] accel` tiene que ser auto, kvm, whpx, hvf o tcg (no «{acelerador}»).") from None

    esperado = acelerador_esperado(host, arq)
    forzado = arquitectura != "auto" and arq is not host.arquitectura
    if pedido is Acelerador.TCG:
        return Aceleracion(Acelerador.TCG, arq, esperado, "TCG pedido en project.toml", forzado=True)
    if pedido is not None and pedido is not esperado:
        correspondiente = NOMBRES_ACELERADOR[esperado] if esperado else "ninguno"
        raise ValueError(
            f"`[vm] accel = \"{pedido.value}\"` no se puede usar en este Host con un Guest {arq.value} "
            f"(el que corresponde es {correspondiente})."
        )
    if esperado is None:
        motivo = (f"un Guest {arq.value} no se puede acelerar en un Host {host.maquina_nativa}"
                  if host.arquitectura is not arq else "este Host no tiene un acelerador soportado")
        return Aceleracion(Acelerador.TCG, arq, None, motivo, forzado=forzado)

    sondas = sondas or sondas_reales()
    disponible, detalle = {Acelerador.KVM: sondas.kvm, Acelerador.WHPX: sondas.whpx,
                           Acelerador.HVF: sondas.hvf}[esperado]()
    if disponible:
        return Aceleracion(esperado, arq, esperado, detalle)
    return Aceleracion(Acelerador.TCG, arq, esperado, f"{esperado.value.upper()} no disponible: {detalle}")
