"""Puente a las herramientas del perfil estudiante dentro del Guest (SPEC §3, RF-03, RF-04).

construct no reimplementa nada (dueño único de responsabilidad): arma el `argv` de cada herramienta,
lo ejecuta en la carpeta del proyecto en la VM y lee su salida `--json`, de la que sólo usa la lista
plana `hallazgos` con la forma común de `yutani.hallazgos` (LINEAMIENTOS §3.2).
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

FLAGS_NORMATIVOS = ("-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic")
SENALES_FATALES = {"SEGV", "ABRT", "FPE", "BUS", "ILL"}
BINARIO_POR_DEFECTO = "programa"
BINARIO_TETSUO = "programa-tetsuo"
FLAGS_SANITIZERS = ("-fsanitize=address,undefined", "-O0", "-g")
BINARIO_DEPURAR = "programa-depurar"
FLAGS_DEPURAR = ("-O0", "-g")
CARPETA_PRUEBAS = ".construct-build"
LIBRERIAS = ("-lp1_test", "-lbitmaps", "-lsonido", "-lm")
ARCHIVOS_LIBRERIAS = {
    "p1_test.h": "/usr/local/lib/libp1_test.a",
    "bitmaps/": "/usr/local/lib/libbitmaps.a",
    "sonido/": "/usr/local/lib/libsonido.a",
}
"""Cabecera incluida → biblioteca estática de la cátedra en el Guest (vm_image/instalar_librerias.sh)."""


@dataclass(frozen=True)
class SalidaHerramienta:
    datos: dict[str, Any]
    hallazgos: list[dict[str, Any]]


def fuentes_del_proyecto(proyecto: Path) -> list[str]:
    """Los `.c` del proyecto (sin las pruebas ni lo de construct), relativos y con `/`."""
    fuentes = []
    for ruta in sorted(proyecto.rglob("*.c")):
        relativa = ruta.relative_to(proyecto)
        if relativa.parts[0] in (".construct", CARPETA_PRUEBAS) or relativa.parts[0].startswith("."):
            continue
        if relativa.name.startswith("test_"):
            continue
        fuentes.append(relativa.as_posix())
    return fuentes


def pruebas_del_proyecto(proyecto: Path) -> list[str]:
    """Las suites de treadstone: `test_*.c` en `tests/` (o en la raíz)."""
    return sorted(r.relative_to(proyecto).as_posix() for patron in ("tests/test_*.c", "test_*.c")
                  for r in proyecto.glob(patron))


# --- argv de cada herramienta -----------------------------------------------------------------


def librerias_necesarias(textos: Iterable[str]) -> list[str]:
    """Las bibliotecas de la cátedra que usa el código, según sus #include."""
    usadas = []
    contenido = "\n".join(textos)
    for cabecera, archivo in ARCHIVOS_LIBRERIAS.items():
        if f"<{cabecera}" in contenido or f'"{cabecera}' in contenido:
            usadas.append(archivo)
    return usadas


def argv_build(fuentes: Iterable[str], salida: str = BINARIO_POR_DEFECTO, librerias: Iterable[str] = ()) -> list[str]:
    """daedalus con los flags normativos (los pone él: -Wall -Wextra -Werror -pedantic -std=c11).

    daedalus agrega sus `--flags` antes de los fuentes, donde un `-lbitmaps` no resuelve nada al
    enlazar estático; por eso las bibliotecas de la cátedra van como entradas, después de los `.c`.
    """
    return ["daedalus", "compile", *fuentes, *librerias, "-o", salida, "--json"]


def argv_check(objetivo: str = ".") -> list[str]:
    """ripley orquesta gaff, spunkmeyer y kaneda (RF-03)."""
    return ["ripley", "check", objetivo, "--json"]


def argv_fix(rutas: Iterable[str]) -> list[str]:
    return ["gaff", "fix", *rutas]


def argv_tetsuo(objetivo: str, entrada: str = "") -> list[str]:
    return ["tetsuo", "run", objetivo, "--json", *(["--input", entrada] if entrada else [])]


def argv_hal(binario: str, argumentos: Iterable[str] = (), entrada: str = "") -> list[str]:
    return ["hal", "run", binario, *argumentos, "--json", *(["--stdin", entrada] if entrada else [])]


def argv_nostromo(binario: str, argumentos: Iterable[str] = (), *, entrada: str = "", timeout_s: float = 10,
                  memoria_mb: int = 128) -> list[str]:
    """Ejecución confinada (RF-04): bwrap + setrlimit con nostromo."""
    argv = ["nostromo", "run", "--json", "--exit-code", "--timeout", str(timeout_s), "--memory", str(memoria_mb)]
    if entrada:
        argv += ["--stdin", entrada]
    return [*argv, binario, *argumentos]


def argv_reproductor(binario: str, argumentos: Iterable[str] = (), entrada: str = "",
                     salida: str = "hal_reproducer.sh") -> list[str]:
    """El script de hal que repite el crash con la misma entrada (vuelve al Host como artefacto)."""
    argv = ["hal", "generate-reproducer", binario, "--output", salida, "--json"]
    if entrada:
        argv += ["--stdin", entrada]
    lista = list(argumentos)
    if lista:
        argv += ["--args", shlex.join(lista)]
    return argv


def argv_bishop(fuente: str, linea: int) -> list[str]:
    """Foto de la pila y el heap en `fuente:linea` (bishop compila y corre el programa por su cuenta)."""
    return ["bishop", "snapshot", fuente, "--line", str(linea), "--json"]


def argv_prueba(prueba: str, fuentes: Iterable[str]) -> tuple[list[str], str]:
    """Compila una suite de treadstone con el código del alumno (sin su main) y p1_test."""
    binario = f"{CARPETA_PRUEBAS}/{PurePosixPath(prueba).stem}"
    sin_main = [f for f in fuentes if PurePosixPath(f).name != "main.c"]
    compilar = ["sh", "-c", 'mkdir -p "$1" && shift && exec "$@"', "sh", CARPETA_PRUEBAS,
                "gcc", *FLAGS_NORMATIVOS, "-I.", prueba, *sin_main, *LIBRERIAS, "-o", binario]
    return compilar, binario


# --- Lectura de las salidas -------------------------------------------------------------------


def extraer_json(texto: str) -> dict[str, Any] | None:
    """El objeto JSON de la salida de una herramienta (tolera texto antes o después)."""
    texto = texto.strip()
    try:
        valor = json.loads(texto)
        return valor if isinstance(valor, dict) else None
    except json.JSONDecodeError:
        pass
    inicio = texto.find("{")
    while inicio >= 0:
        try:
            valor, _ = json.JSONDecoder().raw_decode(texto, inicio)
            if isinstance(valor, dict):
                return valor
        except json.JSONDecodeError:
            pass
        inicio = texto.find("{", inicio + 1)
    return None


def ruta_en_host(archivo: str | None, carpeta_vm: str) -> str | None:
    """Rutas de los hallazgos: relativas al proyecto (las absolutas de la VM se recortan)."""
    if not archivo:
        return archivo
    ruta = PurePosixPath(archivo)
    raiz = PurePosixPath(carpeta_vm)
    if ruta.is_absolute():
        try:
            return ruta.relative_to(raiz).as_posix()
        except ValueError:
            return archivo  # cabeceras del sistema, etc.
    return ruta.as_posix().removeprefix("./")


def leer_salida(texto: str, carpeta_vm: str) -> SalidaHerramienta:
    datos = extraer_json(texto) or {}
    hallazgos = []
    for h in datos.get("hallazgos", []) or []:
        if isinstance(h, dict):
            hallazgos.append({**h, "archivo": ruta_en_host(h.get("archivo"), carpeta_vm)})
    return SalidaHerramienta(datos, hallazgos)


PLUGINS_DE_RIPLEY = {"style": "gaff", "antipatterns": "spunkmeyer", "security": "kaneda"}


def herramienta_de(hallazgo: Mapping[str, Any]) -> str:
    """El satélite detrás de un hallazgo: ripley los informa con el nombre de su plugin (`style`, …)."""
    nombre = str(hallazgo.get("herramienta") or "")
    return PLUGINS_DE_RIPLEY.get(nombre, nombre)


def es_crash(senal: str | None) -> bool:
    return senal in SENALES_FATALES
