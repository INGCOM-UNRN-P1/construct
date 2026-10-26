"""«Explicar este hallazgo» con `yutani.ia` (RF-09, LINEAMIENTOS §3.4, PLAN Fase 4).

La consulta sale **del Host**, así funciona con el `ollama` o `llama.cpp` del alumno sin darle red a
la VM. Al modelo sólo le llegan la línea afectada y el objeto del hallazgo: nunca el archivo entero
ni el resto del proyecto. El proveedor lo elige la configuración común de `yutani.ia`
(`~/.config/p1/ia.toml`, `P1_IA_PROVEEDOR`, …); sin configurar, se usa el servidor local.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from yutani import ia

HERRAMIENTA = "construct"
PROVEEDOR_POR_DEFECTO = "local"
SISTEMA = (
    "Sos docente de Programación 1 (C11) en la UNRN. Explicás en español rioplatense, con voseo, en "
    "pocas oraciones y sin jerga innecesaria, qué significa un hallazgo de una herramienta de la "
    "cátedra, por qué pasa y qué tiene que revisar el alumno. No escribas la solución completa ni "
    "reescribas el programa: como mucho, un ejemplo mínimo de una o dos líneas."
)
CLAVES = ("herramienta", "codigo", "id", "categoria", "severidad", "mensaje", "sugerencia", "explicacion",
          "archivo", "linea", "columna")


class Generador(Protocol):
    def generar(self, prompt: str, *, sistema: str | None = None, modelo: str | None = None) -> str: ...


def linea_afectada(proyecto: Path, hallazgo: Mapping[str, Any]) -> str | None:
    """La línea del hallazgo, leída del archivo del Host (None si no hay archivo o línea)."""
    archivo, numero = hallazgo.get("archivo"), hallazgo.get("linea")
    if not archivo or not isinstance(numero, int) or numero < 1:
        return None
    ruta = (proyecto / str(archivo)).resolve()
    if not ruta.is_relative_to(proyecto.resolve()) or not ruta.is_file():
        return None  # cabeceras del sistema u otra ruta fuera del proyecto: no se manda nada de ahí
    lineas = ruta.read_text(encoding="utf-8", errors="replace").splitlines()
    return lineas[numero - 1] if numero <= len(lineas) else None


def armar_prompt(hallazgo: Mapping[str, Any], linea: str | None) -> str:
    datos = {clave: hallazgo[clave] for clave in CLAVES if hallazgo.get(clave) not in (None, "")}
    partes = ["Explicale a un alumno este hallazgo:", json.dumps(datos, ensure_ascii=False, indent=2)]
    if linea is not None:
        partes += ["La línea del código donde aparece es:", f"```c\n{linea}\n```"]
    return "\n\n".join(partes)


def explicar(proyecto: Path, hallazgo: Mapping[str, Any], *, cliente: Generador | None = None) -> str:
    """La explicación del modelo; ValueError con el motivo en español si no hay IA disponible."""
    prompt = armar_prompt(hallazgo, linea_afectada(proyecto, hallazgo))
    try:
        generador = cliente or ia.cliente(herramienta=HERRAMIENTA, proveedor_por_defecto=PROVEEDOR_POR_DEFECTO)
        texto = generador.generar(prompt, sistema=SISTEMA)
    except ia.ErrorIA as error:
        raise ValueError(f"no se pudo consultar a la IA: {error} (configurala en {ia.ruta_configuracion()}).") from None
    return ia.sin_razonamiento(texto).strip()
