from __future__ import annotations

from pathlib import Path

from yutani.doctor import Chequeo, informe

from construct.core.config import Config, ConfigDev, ConfigVM
from construct.core.doctor import Entorno, chequeos
from construct.platform.host_info import Arquitectura, HostInfo, Sistema
from construct.platform.paths import directorios
from tests.conftest import crear_host, sondas_aceleracion, sondas_binarios

LINUX_COMPLETO = {"/usr/bin/qemu-system-x86_64": "QEMU emulator version 9.2.0",
                  "/usr/bin/qemu-img": "qemu-img version 9.2.0", "/usr/bin/git": "git version 2.45.0"}


def entorno(host: HostInfo, archivos: dict[str, str] | None = None, *, config: Config | None = None,
            libre_gb: float = 50, **aceleradores: tuple[bool, str]) -> Entorno:
    return Entorno(
        host=host,
        config=config or Config(),
        directorios=directorios(),
        sondas_aceleracion=sondas_aceleracion(**aceleradores),
        sondas_binarios=sondas_binarios(LINUX_COMPLETO if archivos is None else archivos),
        espacio_libre=lambda _ruta: int(libre_gb * 1024**3),
    )


def por_nombre(lista: list[Chequeo]) -> dict[str, Chequeo]:
    return {c.nombre: c for c in lista}


def instalar_imagen(tmp_path: Path, arquitectura: Arquitectura = Arquitectura.X86_64) -> None:
    from construct.core import imagenes
    from tests.test_imagenes import publicar

    manifiesto = imagenes.obtener_manifiesto(str(publicar(tmp_path / "release")))
    imagenes.instalar(manifiesto, arquitectura, directorios().imagenes)


def test_linux_completo_da_ok(tmp_path: Path) -> None:
    instalar_imagen(tmp_path)
    resultado = chequeos(entorno(crear_host()))
    datos = informe("construct", "0.1.0", resultado)
    assert datos["ok"], datos
    assert list(por_nombre(resultado)) == ["sistema", "aceleración", "qemu-system-x86_64", "qemu-img",
                                           "imagen del Guest (x86_64)", "git", "espacio en disco", "memoria"]


def test_sin_imagen_sugiere_setup() -> None:
    imagen = por_nombre(chequeos(entorno(crear_host())))["imagen del Guest (x86_64)"]
    assert imagen.requerido and not imagen.ok and "construct setup" in imagen.sugerencia


def test_imagen_incompleta(tmp_path: Path) -> None:
    instalar_imagen(tmp_path)
    next(directorios().imagenes.rglob("rootfs.qcow2")).unlink()
    imagen = por_nombre(chequeos(entorno(crear_host())))["imagen del Guest (x86_64)"]
    assert not imagen.ok and "--forzar" in imagen.sugerencia


def test_sin_chequeo_de_imagen_para_setup() -> None:
    assert not any(c.nombre.startswith("imagen") for c in chequeos(entorno(crear_host()), imagen=False))


def test_sin_permiso_sobre_kvm_sugiere_el_grupo() -> None:
    resultado = por_nombre(chequeos(entorno(crear_host(), kvm=(False, "sin permiso de lectura y escritura"))))
    acel = resultado["aceleración"]
    assert acel.requerido and not acel.ok
    assert "usermod -aG kvm" in acel.sugerencia


def test_windows_sin_whpx_y_sin_qemu() -> None:
    host = crear_host(Sistema.WINDOWS)
    resultado = por_nombre(chequeos(entorno(host, {}, whpx=(False, "no está instalada"))))
    assert "HypervisorPlatform" in resultado["aceleración"].sugerencia
    assert "winget install --id SoftwareFreedomConservancy.QEMU" in resultado["qemu-system-x86_64"].sugerencia
    assert "winget install --id Git.Git" in resultado["git"].sugerencia
    assert not informe("construct", "0.1.0", resultado.values())["ok"]


def test_apple_silicon_bajo_rosetta(tmp_path: Path) -> None:
    instalar_imagen(tmp_path, Arquitectura.AARCH64)
    host = crear_host(Sistema.MACOS, Arquitectura.AARCH64, emulado=True)
    archivos = {"/opt/homebrew/bin/qemu-system-aarch64": "QEMU emulator version 10.0.0",
                "/opt/homebrew/bin/qemu-img": "qemu-img version 10.0.0", "/opt/homebrew/bin/git": "git version 2.46.0"}
    resultado = por_nombre(chequeos(entorno(host, archivos)))
    assert resultado["qemu-img"].ok
    assert "qemu-system-aarch64" in resultado and resultado["qemu-system-aarch64"].ok
    arquitectura = resultado["arquitectura"]
    assert not arquitectura.ok and not arquitectura.requerido  # Advertencia, no impide usar construct.
    assert informe("construct", "0.1.0", resultado.values())["ok"]


def test_tcg_elegido_por_el_usuario_es_advertencia(tmp_path: Path) -> None:
    instalar_imagen(tmp_path)
    config = Config(vm=ConfigVM(accel="tcg"))
    resultado = por_nombre(chequeos(entorno(crear_host(), config=config)))
    assert not resultado["aceleración"].requerido
    assert informe("construct", "0.1.0", resultado.values())["ok"]


def test_config_invalida_queda_como_chequeo_fallido() -> None:
    resultado = por_nombre(chequeos(entorno(crear_host(), config=Config(vm=ConfigVM(accel="hvf")))))
    assert not resultado["aceleración"].ok
    assert "qemu-system-x86_64" not in resultado


def test_qemu_viejo() -> None:
    archivos = {**LINUX_COMPLETO, "/usr/bin/qemu-system-x86_64": "QEMU emulator version 6.2.0"}
    qemu = por_nombre(chequeos(entorno(crear_host(), archivos)))["qemu-system-x86_64"]
    assert not qemu.ok and "8.2.0 o posterior" in qemu.proposito


def test_poco_espacio() -> None:
    espacio = por_nombre(chequeos(entorno(crear_host(), libre_gb=0.5)))["espacio en disco"]
    assert not espacio.ok and "CONSTRUCT_HOME" in espacio.sugerencia


def test_poca_memoria_es_advertencia() -> None:
    memoria = por_nombre(chequeos(entorno(crear_host(ram_mb=2048))))["memoria"]
    assert not memoria.ok and not memoria.requerido and "memoria_mb" in memoria.sugerencia


def test_wsl_avisa() -> None:
    assert "wsl" in por_nombre(chequeos(entorno(crear_host(wsl=True))))


def test_dev_por_opcion_o_por_config() -> None:
    assert "gdb (desarrollo)" not in por_nombre(chequeos(entorno(crear_host())))
    con_opcion = por_nombre(chequeos(entorno(crear_host()), dev=True))["gdb (desarrollo)"]
    assert con_opcion.requerido and not con_opcion.ok
    con_config = chequeos(entorno(crear_host(), config=Config(dev=ConfigDev(habilitado=True))))
    assert "gdb (desarrollo)" in por_nombre(con_config)


def test_dev_en_apple_silicon() -> None:
    gdb = por_nombre(chequeos(entorno(crear_host(Sistema.MACOS, Arquitectura.AARCH64)), dev=True))["gdb (desarrollo)"]
    assert not gdb.ok and "Apple Silicon" in gdb.detalle


def test_espacio_se_mide_en_una_carpeta_existente(construct_home: Path) -> None:
    medidas: list[str] = []
    e = entorno(crear_host())
    e.espacio_libre = lambda ruta: medidas.append(ruta) or 10 * 1024**3
    chequeos(e)
    assert Path(medidas[0]).exists()
