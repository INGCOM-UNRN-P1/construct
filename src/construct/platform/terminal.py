"""Consola del Host (PLAN §2): UTF-8 en todas las plataformas.

La consola de Windows arranca en cp1252/cp850 y rompe las tildes y la «ñ» de los mensajes; acá
se fuerza UTF-8 antes de imprimir cualquier cosa. El modo raw para `construct jonesy` (Fase 5)
también va a vivir en este módulo.
"""

from __future__ import annotations

import sys
from typing import TextIO


def preparar_consola(flujos: tuple[TextIO, ...] | None = None) -> None:
    """Reconfigura stdout y stderr a UTF-8 (sin romper si no son flujos de texto reconfigurables)."""
    for flujo in flujos if flujos is not None else (sys.stdout, sys.stderr):
        reconfigurar = getattr(flujo, "reconfigure", None)
        if reconfigurar is None or (getattr(flujo, "encoding", "") or "").lower().replace("-", "") == "utf8":
            continue
        try:
            reconfigurar(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue  # Flujo ya usado en modo binario o cerrado: se deja como está.
