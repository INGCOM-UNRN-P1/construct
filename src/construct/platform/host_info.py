"""Qué Host es: sistema operativo, arquitectura real del hardware, WSL, distro, RAM y CPUs (PLAN §2).

La arquitectura que informa el intérprete puede mentir: un Python `x86_64` en una Mac con Apple
Silicon corre bajo Rosetta, y uno `AMD64` en Windows on ARM corre emulado. Por eso se distingue la
máquina del intérprete (`maquina`) de la del hardware (`maquina_nativa`), que es la que decide qué
Guest se puede acelerar.

Todo lo que se consulta al sistema pasa por `Sondas`, así los tests simulan cualquier Host desde
cualquier plataforma.
"""

from __future__ import annotations

import ctypes
import os
import platform as _plataforma
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class Sistema(StrEnum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"


class Arquitectura(StrEnum):
    X86_64 = "x86_64"
    AARCH64 = "aarch64"


NOMBRES_SISTEMA = {Sistema.LINUX: "Linux", Sistema.WINDOWS: "Windows", Sistema.MACOS: "macOS"}


class SistemaNoSoportado(ValueError):
    """El Host no es Windows, macOS ni Linux."""


@dataclass(frozen=True)
class Distro:
    """Distribución de Linux según `/etc/os-release`."""

    id: str
    id_like: tuple[str, ...] = ()
    nombre: str = ""

    def familia(self) -> str:
        """`debian`, `fedora`, `arch`, `suse`, `alpine` u `otra` (para elegir el gestor de paquetes)."""
        for candidata in (self.id, *self.id_like):
            for familia, miembros in _FAMILIAS.items():
                if candidata in miembros:
                    return familia
        return "otra"


_FAMILIAS = {
    "debian": {"debian", "ubuntu", "linuxmint", "pop", "elementary", "raspbian"},
    "fedora": {"fedora", "rhel", "centos", "rocky", "almalinux"},
    "arch": {"arch", "manjaro", "endeavouros", "garuda"},
    "suse": {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "suse", "sles"},
    "alpine": {"alpine"},
}


@dataclass(frozen=True)
class HostInfo:
    sistema: Sistema
    arquitectura: Arquitectura | None
    """Arquitectura real del hardware; None si no es x86_64 ni aarch64."""
    maquina: str
    """La que informa el intérprete de Python (puede ser emulada)."""
    maquina_nativa: str
    emulado: bool
    """El intérprete corre emulado (Rosetta 2, x64 sobre Windows on ARM)."""
    wsl: bool
    distro: Distro | None
    cpus: int
    ram_mb: int | None

    def describir(self) -> str:
        partes = [NOMBRES_SISTEMA[self.sistema]]
        if self.distro is not None and self.distro.nombre:
            partes[0] += f" ({self.distro.nombre})"
        if self.wsl:
            partes[0] += " dentro de WSL"
        partes.append(self.arquitectura.value if self.arquitectura else self.maquina_nativa)
        partes.append(f"{self.cpus} CPU")
        if self.ram_mb is not None:
            partes.append(f"{self.ram_mb / 1024:.1f} GB de RAM")
        return ", ".join(partes)


@dataclass(frozen=True)
class Sondas:
    """Lo que se le pregunta al sistema; `sondas_reales()` da las de la máquina actual."""

    sys_platform: str
    maquina: str
    maquina_nativa: Callable[[], str | None]
    leer: Callable[[str], str | None]
    entorno: Mapping[str, str]
    cpus: int
    ram_mb: Callable[[], int | None]


def detectar_sistema(sys_platform: str) -> Sistema:
    if sys_platform.startswith("win"):
        return Sistema.WINDOWS
    if sys_platform == "darwin":
        return Sistema.MACOS
    if sys_platform.startswith("linux"):
        return Sistema.LINUX
    raise SistemaNoSoportado(
        f"construct funciona en Windows, macOS y Linux; este sistema ({sys_platform}) no está soportado."
    )


def normalizar_arquitectura(maquina: str) -> Arquitectura | None:
    nombre = maquina.strip().lower()
    if nombre in {"x86_64", "amd64", "x64"}:
        return Arquitectura.X86_64
    if nombre in {"aarch64", "arm64", "armv8", "armv8l"}:
        return Arquitectura.AARCH64
    return None


def parsear_os_release(texto: str) -> Distro:
    valores: dict[str, str] = {}
    for linea in texto.splitlines():
        clave, separador, valor = linea.partition("=")
        if separador:
            valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return Distro(
        id=valores.get("ID", "").lower(),
        id_like=tuple(valores.get("ID_LIKE", "").lower().split()),
        nombre=valores.get("PRETTY_NAME", valores.get("NAME", "")),
    )


def _es_wsl(sondas: Sondas) -> bool:
    if "WSL_DISTRO_NAME" in sondas.entorno or "WSL_INTEROP" in sondas.entorno:
        return True
    version = sondas.leer("/proc/sys/kernel/osrelease") or ""
    return "microsoft" in version.lower()


def detectar_host(sondas: Sondas | None = None) -> HostInfo:
    sondas = sondas or sondas_reales()
    sistema = detectar_sistema(sondas.sys_platform)
    nativa = sondas.maquina_nativa() or sondas.maquina
    arq_nativa = normalizar_arquitectura(nativa)
    arq_python = normalizar_arquitectura(sondas.maquina)
    distro = None
    wsl = False
    if sistema is Sistema.LINUX:
        texto = sondas.leer("/etc/os-release")
        distro = parsear_os_release(texto) if texto else None
        wsl = _es_wsl(sondas)
    return HostInfo(
        sistema=sistema,
        arquitectura=arq_nativa,
        maquina=sondas.maquina,
        maquina_nativa=nativa,
        emulado=arq_nativa is not None and arq_python is not None and arq_nativa is not arq_python,
        wsl=wsl,
        distro=distro,
        cpus=sondas.cpus,
        ram_mb=sondas.ram_mb(),
    )


# --- Sondas reales -------------------------------------------------------------------------------


def _leer(ruta: str) -> str | None:
    try:
        with open(ruta, encoding="utf-8", errors="replace") as archivo:
            return archivo.read()
    except OSError:
        return None


def sysctl(nombre: str) -> str | None:
    """Valor de `sysctl -n nombre` en macOS; None si no existe."""
    try:
        salida = subprocess.run(["sysctl", "-n", nombre], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return salida.stdout.strip() if salida.returncode == 0 and salida.stdout.strip() else None


def _maquina_nativa_macos() -> str | None:
    # Bajo Rosetta, `uname -m` y platform.machine() dicen x86_64; hw.optional.arm64 no miente.
    return "arm64" if sysctl("hw.optional.arm64") == "1" else None


def _maquina_nativa_windows() -> str | None:
    # IsWow64Process2 (Windows 10 1709+) da la máquina nativa aunque el proceso corra emulado.
    try:
        # getattr: ctypes.WinDLL sólo existe en Windows y mypy lo rechaza en el resto.
        kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
        proceso = ctypes.c_ushort(0)
        nativa = ctypes.c_ushort(0)
        if not kernel32.IsWow64Process2(kernel32.GetCurrentProcess(), ctypes.byref(proceso),
                                        ctypes.byref(nativa)):
            return None
    except (AttributeError, OSError):
        return None
    return {0xAA64: "ARM64", 0x8664: "AMD64"}.get(nativa.value)


def _ram_mb_linux() -> int | None:
    for linea in (_leer("/proc/meminfo") or "").splitlines():
        if linea.startswith("MemTotal:"):
            return int(linea.split()[1]) // 1024
    return None


def _ram_mb_macos() -> int | None:
    valor = sysctl("hw.memsize")
    return int(valor) // (1024 * 1024) if valor and valor.isdigit() else None


def _ram_mb_windows() -> int | None:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    estado = MEMORYSTATUSEX()
    estado.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        if not getattr(ctypes, "WinDLL")("kernel32").GlobalMemoryStatusEx(ctypes.byref(estado)):  # noqa: B009
            return None
    except (AttributeError, OSError):
        return None
    return int(estado.ullTotalPhys) // (1024 * 1024)


def sondas_reales() -> Sondas:
    sistema = detectar_sistema(sys.platform)
    nativa: Callable[[], str | None] = {
        Sistema.LINUX: lambda: None,
        Sistema.MACOS: _maquina_nativa_macos,
        Sistema.WINDOWS: _maquina_nativa_windows,
    }[sistema]
    ram = {Sistema.LINUX: _ram_mb_linux, Sistema.MACOS: _ram_mb_macos, Sistema.WINDOWS: _ram_mb_windows}[sistema]
    return Sondas(
        sys_platform=sys.platform,
        maquina=_plataforma.machine(),
        maquina_nativa=nativa,
        leer=_leer,
        entorno=os.environ,
        cpus=os.cpu_count() or 1,
        ram_mb=ram,
    )
