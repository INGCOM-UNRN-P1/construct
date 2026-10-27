#!/usr/bin/env python3
"""Manifiesto de mother para el Guest: el ecosistema.toml de p1-tools sin las herramientas del Host.

construct es del perfil estudiante pero corre en el Host, y mother no tiene cómo excluir una
herramienta de un perfil. Sin este filtro, `mother instalar --perfil estudiante` intenta instalarlo
dentro de la VM (y en el CI falla, porque su repo es privado y en el build no hay credenciales).

Uso: manifiesto_guest.py ENTRADA SALIDA NOMBRE...
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

_TABLA = re.compile(r"^\s*\[")


def _bloques(texto: str) -> list[list[str]]:
    """Corta el texto en bloques que empiezan en cada encabezado de tabla (`[x]` o `[[x]]`)."""
    bloques: list[list[str]] = [[]]
    for linea in texto.splitlines(keepends=True):
        if _TABLA.match(linea):
            bloques.append([])
        bloques[-1].append(linea)
    return bloques


def _nombre(bloque: list[str]) -> str | None:
    if not bloque or bloque[0].strip() != "[[repo]]":
        return None
    nombre = tomllib.loads("".join(bloque[1:])).get("nombre")
    return nombre if isinstance(nombre, str) else None


def _menciona(valor: object, nombre: str) -> bool:
    """Si `nombre` aparece como clave o como valor (p. ej. en la matriz de un cuatrimestre)."""
    if isinstance(valor, dict):
        return any(k == nombre or _menciona(v, nombre) for k, v in valor.items())
    if isinstance(valor, list):
        return any(_menciona(v, nombre) for v in valor)
    return valor == nombre


def sin_repos(texto: str, nombres: set[str]) -> str:
    """El manifiesto sin los `[[repo]]` de `nombres`; falla si alguno no está o queda referenciado."""
    quedan = [b for b in _bloques(texto) if _nombre(b) not in nombres]
    resultado = "".join("".join(b) for b in quedan)
    antes = {r["nombre"] for r in tomllib.loads(texto).get("repo", [])}
    despues = {r["nombre"] for r in tomllib.loads(resultado).get("repo", [])}
    if faltan := nombres - antes:
        raise ValueError(f"no están en el manifiesto: {', '.join(sorted(faltan))}")
    if antes - despues != nombres:
        raise ValueError("el filtro sacó repos de más")
    datos = tomllib.loads(resultado)
    if referencias := sorted(n for n in nombres if _menciona(datos, n)):
        raise ValueError(f"quedan referencias a: {', '.join(referencias)}")
    return resultado


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    entrada, salida, *nombres = argv
    try:
        texto = sin_repos(Path(entrada).read_text(encoding="utf-8"), set(nombres))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"manifiesto_guest: {error}", file=sys.stderr)
        return 1
    Path(salida).write_text(texto, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
