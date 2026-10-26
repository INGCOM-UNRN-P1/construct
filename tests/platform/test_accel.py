from __future__ import annotations

import pytest

from construct.platform.accel import Acelerador, acelerador_esperado, detectar, probar_kvm
from construct.platform.host_info import Arquitectura, Sistema
from tests.conftest import crear_host, sondas_aceleracion


@pytest.mark.parametrize(("sistema", "arquitectura", "acelerador"), [
    (Sistema.LINUX, Arquitectura.X86_64, Acelerador.KVM),
    (Sistema.LINUX, Arquitectura.AARCH64, Acelerador.KVM),
    (Sistema.WINDOWS, Arquitectura.X86_64, Acelerador.WHPX),
    (Sistema.MACOS, Arquitectura.X86_64, Acelerador.HVF),
    (Sistema.MACOS, Arquitectura.AARCH64, Acelerador.HVF),
])
def test_acelerador_por_host_con_guest_de_la_misma_arquitectura(
    sistema: Sistema, arquitectura: Arquitectura, acelerador: Acelerador
) -> None:
    elegida = detectar(crear_host(sistema, arquitectura), sondas=sondas_aceleracion())
    assert elegida.acelerador is acelerador
    assert elegida.arquitectura_guest is arquitectura
    assert not elegida.compatibilidad


def test_apple_silicon_usa_guest_aarch64_con_hvf() -> None:
    """Decisión D1: HVF no acelera x86_64 en Apple Silicon, así que el Guest es aarch64."""
    elegida = detectar(crear_host(Sistema.MACOS, Arquitectura.AARCH64, emulado=True), sondas=sondas_aceleracion())
    assert elegida.binario_qemu == "qemu-system-aarch64"
    assert elegida.argumentos_qemu() == ["-machine", "virt", "-accel", "hvf", "-cpu", "host"]


def test_apple_silicon_con_x86_64_forzado_es_modo_compatibilidad() -> None:
    elegida = detectar(crear_host(Sistema.MACOS, Arquitectura.AARCH64), arquitectura="x86_64",
                       sondas=sondas_aceleracion())
    assert elegida.acelerador is Acelerador.TCG
    assert elegida.forzado and elegida.esperado is None
    assert elegida.argumentos_qemu() == ["-machine", "q35", "-accel", "tcg", "-cpu", "max"]


def test_windows_on_arm_cae_en_tcg_sin_acelerador_esperado() -> None:
    elegida = detectar(crear_host(Sistema.WINDOWS, Arquitectura.AARCH64), sondas=sondas_aceleracion())
    assert elegida.acelerador is Acelerador.TCG
    assert elegida.esperado is None and not elegida.forzado


def test_whpx_lleva_kernel_irqchip_off() -> None:
    elegida = detectar(crear_host(Sistema.WINDOWS), sondas=sondas_aceleracion())
    assert elegida.argumentos_qemu() == ["-machine", "q35", "-accel", "whpx,kernel-irqchip=off", "-cpu", "max"]


def test_acelerador_no_disponible_cae_en_tcg_con_el_motivo() -> None:
    elegida = detectar(crear_host(Sistema.WINDOWS),
                       sondas=sondas_aceleracion(whpx=(False, "la característica no está instalada")))
    assert elegida.acelerador is Acelerador.TCG
    assert elegida.esperado is Acelerador.WHPX
    assert not elegida.forzado
    assert "WHPX no disponible" in elegida.detalle


def test_tcg_pedido_explicitamente() -> None:
    elegida = detectar(crear_host(), acelerador="tcg", sondas=sondas_aceleracion())
    assert elegida.acelerador is Acelerador.TCG and elegida.forzado


def test_acelerador_de_otro_sistema_es_error() -> None:
    with pytest.raises(ValueError, match="no se puede usar en este Host"):
        detectar(crear_host(Sistema.LINUX), acelerador="hvf", sondas=sondas_aceleracion())


@pytest.mark.parametrize(("clave", "valor"), [("acelerador", "vmware"), ("arquitectura", "riscv64")])
def test_valores_invalidos(clave: str, valor: str) -> None:
    with pytest.raises(ValueError, match="tiene que ser"):
        detectar(crear_host(), sondas=sondas_aceleracion(), **{clave: valor})


def test_host_de_arquitectura_desconocida_pide_configurar_arch() -> None:
    with pytest.raises(ValueError, match=r"\[vm\] arch"):
        detectar(crear_host(arquitectura=None), sondas=sondas_aceleracion())


def test_wsl_espera_kvm() -> None:
    assert acelerador_esperado(crear_host(wsl=True), Arquitectura.X86_64) is Acelerador.KVM


def test_probar_kvm_sin_dispositivo(tmp_path) -> None:
    disponible, detalle = probar_kvm(str(tmp_path / "kvm"))
    assert not disponible and "no existe" in detalle


def test_probar_kvm_con_algo_que_no_es_kvm(tmp_path) -> None:
    falso = tmp_path / "kvm"
    falso.write_text("")
    disponible, detalle = probar_kvm(str(falso))
    assert not disponible and "no responde como KVM" in detalle
