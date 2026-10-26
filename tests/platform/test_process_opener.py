from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from construct.platform import process
from construct.platform.host_info import Sistema
from construct.platform.opener import (
    REPRODUCTORES_LINUX,
    SinHerramientaDelHost,
    comando_abrir,
    comando_reproducir,
)
from construct.platform.paths import ruta_corta
from tests.conftest import crear_host

DORMIR = [sys.executable, "-c", "import time; time.sleep(60)"]


def esperar(condicion: Callable[[], bool], segundos: float = 10) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return False


def test_lanzar_y_terminar() -> None:
    hijo = process.lanzar_vinculado(DORMIR)
    try:
        assert process.proceso_vivo(hijo.pid)
        process.terminar(hijo.pid)
        hijo.wait(timeout=10)
        assert not process.proceso_vivo(hijo.pid) or hijo.poll() is not None
    finally:
        hijo.kill()


def test_pid_invalido_no_esta_vivo() -> None:
    assert not process.proceso_vivo(0)
    assert not process.proceso_vivo(-5)


@pytest.mark.skipif(not process.MUERE_CON_EL_PADRE, reason="este sistema no lo garantiza (macOS: pidfile y watchdog)")
def test_el_hijo_muere_cuando_muere_construct() -> None:
    # Un "construct" intermedio lanza al nieto vinculado, informa su PID y muere por la fuerza.
    intermedio = subprocess.Popen(
        [sys.executable, "-c",
         ("import sys, time\n"
          "from construct.platform.process import lanzar_vinculado\n"
          f"p = lanzar_vinculado({DORMIR!r})\n"
          "print(p.pid, flush=True)\n"
          "time.sleep(60)\n")],
        stdout=subprocess.PIPE, text=True,
    )
    assert intermedio.stdout is not None
    nieto = int(intermedio.stdout.readline())
    try:
        assert process.proceso_vivo(nieto)
        intermedio.kill()
        intermedio.wait(timeout=10)
        assert esperar(lambda: not process.proceso_vivo(nieto)), "el nieto sobrevivió a su padre"
    finally:
        process.terminar(nieto)


def test_comandos_para_abrir() -> None:
    archivo = Path("salida.bmp")
    assert comando_abrir(crear_host(Sistema.WINDOWS), archivo) is None
    assert comando_abrir(crear_host(Sistema.MACOS), archivo) == ["open", "salida.bmp"]
    assert comando_abrir(crear_host(), archivo, which=lambda p: f"/usr/bin/{p}") == ["xdg-open", "salida.bmp"]
    assert comando_abrir(crear_host(wsl=True), archivo, which=lambda p: f"/usr/bin/{p}")[0] == "wslview"
    with pytest.raises(SinHerramientaDelHost, match="xdg-utils"):
        comando_abrir(crear_host(), archivo, which=lambda _p: None)


def test_comandos_para_reproducir() -> None:
    sonido = Path("tono.wav")
    assert comando_reproducir(crear_host(Sistema.WINDOWS), sonido) is None
    assert comando_reproducir(crear_host(Sistema.MACOS), sonido) == ["afplay", "tono.wav"]
    solo_aplay = {"aplay": "/usr/bin/aplay"}
    assert comando_reproducir(crear_host(), sonido, which=solo_aplay.get) == ["aplay", "tono.wav"]
    assert comando_reproducir(crear_host(), sonido, which=lambda p: p)[0] == REPRODUCTORES_LINUX[0]
    with pytest.raises(SinHerramientaDelHost, match="reproductor"):
        comando_reproducir(crear_host(), sonido, which=lambda _p: None)


def test_ruta_corta_apunta_al_mismo_archivo(tmp_path: Path) -> None:
    carpeta = tmp_path / "Usuario Martín" / "imágenes"
    carpeta.mkdir(parents=True)
    archivo = carpeta / "rootfs.qcow2"
    archivo.write_bytes(b"x")
    assert ruta_corta(archivo).samefile(archivo)
