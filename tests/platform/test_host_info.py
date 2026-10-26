from __future__ import annotations

import pytest

from construct.platform.host_info import (
    Arquitectura,
    Distro,
    Sistema,
    SistemaNoSoportado,
    Sondas,
    detectar_host,
    detectar_sistema,
    normalizar_arquitectura,
    parsear_os_release,
)


def sondas(sys_platform: str, maquina: str, *, nativa: str | None = None, archivos: dict[str, str] | None = None,
           entorno: dict[str, str] | None = None, ram_mb: int | None = 8192) -> Sondas:
    return Sondas(sys_platform=sys_platform, maquina=maquina, maquina_nativa=lambda: nativa,
                  leer=lambda ruta: (archivos or {}).get(ruta), entorno=entorno or {}, cpus=8,
                  ram_mb=lambda: ram_mb)


@pytest.mark.parametrize(("valor", "esperado"), [
    ("linux", Sistema.LINUX), ("win32", Sistema.WINDOWS), ("darwin", Sistema.MACOS),
])
def test_detectar_sistema(valor: str, esperado: Sistema) -> None:
    assert detectar_sistema(valor) is esperado


def test_sistema_no_soportado_es_error_de_datos() -> None:
    with pytest.raises(SistemaNoSoportado, match="no está soportado"):
        detectar_sistema("freebsd14")
    assert issubclass(SistemaNoSoportado, ValueError)  # TyperConErrores lo muestra sin traceback.


@pytest.mark.parametrize(("maquina", "esperada"), [
    ("x86_64", Arquitectura.X86_64), ("AMD64", Arquitectura.X86_64), ("arm64", Arquitectura.AARCH64),
    ("aarch64", Arquitectura.AARCH64), ("ARM64", Arquitectura.AARCH64), ("i686", None), ("riscv64", None),
])
def test_normalizar_arquitectura(maquina: str, esperada: Arquitectura | None) -> None:
    assert normalizar_arquitectura(maquina) is esperada


def test_os_release() -> None:
    distro = parsear_os_release('NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu 24.04 LTS"\n')
    assert distro == Distro("ubuntu", ("debian",), "Ubuntu 24.04 LTS")
    assert distro.familia() == "debian"


@pytest.mark.parametrize(("distro", "familia"), [
    (Distro("fedora"), "fedora"), (Distro("linuxmint", ("ubuntu", "debian")), "debian"),
    (Distro("endeavouros", ("arch",)), "arch"), (Distro("opensuse-tumbleweed", ("opensuse", "suse")), "suse"),
    (Distro("nixos"), "otra"),
])
def test_familia_de_distro(distro: Distro, familia: str) -> None:
    assert distro.familia() == familia


def test_linux_con_distro() -> None:
    host = detectar_host(sondas("linux", "x86_64", archivos={"/etc/os-release": "ID=fedora\nPRETTY_NAME=Fedora"}))
    assert host.sistema is Sistema.LINUX
    assert host.arquitectura is Arquitectura.X86_64
    assert host.distro is not None and host.distro.id == "fedora"
    assert not host.wsl and not host.emulado


def test_wsl_por_kernel_y_por_variable() -> None:
    por_kernel = sondas("linux", "x86_64", archivos={"/proc/sys/kernel/osrelease": "5.15.153.1-microsoft-standard-WSL2"})
    assert detectar_host(por_kernel).wsl
    assert detectar_host(sondas("linux", "x86_64", entorno={"WSL_DISTRO_NAME": "Ubuntu"})).wsl


def test_mac_apple_silicon_con_python_bajo_rosetta() -> None:
    host = detectar_host(sondas("darwin", "x86_64", nativa="arm64"))
    assert host.sistema is Sistema.MACOS
    assert host.arquitectura is Arquitectura.AARCH64  # Decide el hardware, no el intérprete.
    assert host.emulado
    assert host.distro is None


def test_mac_intel() -> None:
    host = detectar_host(sondas("darwin", "x86_64", nativa=None))
    assert host.arquitectura is Arquitectura.X86_64 and not host.emulado


def test_windows_on_arm_con_python_x64() -> None:
    host = detectar_host(sondas("win32", "AMD64", nativa="ARM64"))
    assert host.sistema is Sistema.WINDOWS
    assert host.arquitectura is Arquitectura.AARCH64
    assert host.emulado


def test_arquitectura_no_soportada() -> None:
    host = detectar_host(sondas("linux", "i686"))
    assert host.arquitectura is None and not host.emulado


def test_describir() -> None:
    host = detectar_host(sondas("win32", "AMD64", ram_mb=16384))
    assert host.describir() == "Windows, x86_64, 8 CPU, 16.0 GB de RAM"


def test_deteccion_real_no_falla() -> None:
    host = detectar_host()
    assert host.cpus >= 1
