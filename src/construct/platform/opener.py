"""Abrir artefactos con el visor del Host y reproducir `.wav` (SPEC §3.2, PLAN Fase 4).

| | abrir | reproducir `.wav` |
| :--- | :--- | :--- |
| Windows | `os.startfile` | `winsound` (biblioteca estándar) |
| macOS | `open` | `afplay` |
| Linux | `xdg-open` (`wslview` en WSL) | `pw-play`, `paplay` o `aplay`, el primero que haya |
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from construct.platform.host_info import HostInfo, Sistema

REPRODUCTORES_LINUX = ("pw-play", "paplay", "aplay")


class SinHerramientaDelHost(ValueError):
    """Falta el programa del Host que abre o reproduce el archivo."""


def comando_abrir(host: HostInfo, ruta: Path, which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """El comando para abrir `ruta` con la aplicación predeterminada; None en Windows (usa `os.startfile`)."""
    if host.sistema is Sistema.WINDOWS:
        return None
    if host.sistema is Sistema.MACOS:
        return ["open", str(ruta)]
    for programa in (("wslview", "xdg-open") if host.wsl else ("xdg-open",)):
        if which(programa):
            return [programa, str(ruta)]
    raise SinHerramientaDelHost(
        f"no se encontró xdg-open para abrir {ruta}: instalá el paquete xdg-utils de tu distribución."
    )


def comando_reproducir(host: HostInfo, ruta: Path,
                       which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """El comando para reproducir un `.wav`; None en Windows (usa `winsound`)."""
    if host.sistema is Sistema.WINDOWS:
        return None
    if host.sistema is Sistema.MACOS:
        return ["afplay", str(ruta)]
    for programa in REPRODUCTORES_LINUX:
        if which(programa):
            return [programa, str(ruta)]
    raise SinHerramientaDelHost(
        f"no se encontró un reproductor para {ruta}: instalá pipewire-utils, pulseaudio-utils o alsa-utils."
    )


def _winsound() -> Any:
    return importlib.import_module("winsound")  # Sólo existe en Windows.


def abrir(host: HostInfo, ruta: Path) -> None:
    """Abre `ruta` con la aplicación predeterminada del Host, sin esperar a que se cierre."""
    comando = comando_abrir(host, ruta)
    if comando is None:
        getattr(os, "startfile")(str(ruta))  # noqa: B009 - os.startfile sólo existe en Windows
        return
    subprocess.Popen(comando, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def reproducir_wav(host: HostInfo, ruta: Path) -> subprocess.Popen[bytes] | None:
    """Reproduce `ruta` sin bloquear; devuelve el proceso para poder cortarlo (None en Windows)."""
    comando = comando_reproducir(host, ruta)
    if comando is None:
        winsound = _winsound()
        winsound.PlaySound(str(ruta), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return None
    return subprocess.Popen(comando, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def detener_wav(host: HostInfo, proceso: subprocess.Popen[bytes] | None) -> None:
    if proceso is not None:
        proceso.terminate()
    elif host.sistema is Sistema.WINDOWS:
        _winsound().PlaySound(None, 0)
