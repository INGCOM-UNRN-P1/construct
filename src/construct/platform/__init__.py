"""Capa de plataforma del Host (PLAN §2).

Es el único paquete de construct que consulta el sistema operativo: `core` y `ui` reciben lo que
necesitan (`HostInfo`, `Aceleracion`, rutas, ejecutables) ya resuelto desde acá.
"""

from __future__ import annotations

from functools import cache

from construct.platform.host_info import Arquitectura, HostInfo, Sistema, detectar_host


@cache
def host() -> HostInfo:
    """El Host actual (se detecta una sola vez por proceso)."""
    return detectar_host()


__all__ = ["Arquitectura", "HostInfo", "Sistema", "detectar_host", "host"]
