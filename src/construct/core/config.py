"""Configuración declarativa de `project.toml` (SPEC §7, PLAN §2.1, §3.3 y §3.4).

```toml
[vm]
accel = "auto"      # auto | kvm | whpx | hvf | tcg
arch = "auto"       # auto | x86_64 | aarch64
memoria_mb = 1024
cpus = 2
qemu = ""           # ruta a qemu-system-* si no está en el PATH

[sync]
automatico = true   # build/check/test/run suben el código antes de correr

[dev]
habilitado = false  # modo desarrollo: gdbserver tunelizado para GDB en el Host
```

Todas las claves son opcionales; las que faltan toman el valor por defecto.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

NOMBRE_ARCHIVO = "project.toml"


class ErrorDeConfiguracion(ValueError):
    """`project.toml` mal formado o con valores inválidos."""


@dataclass(frozen=True)
class ConfigVM:
    accel: str = "auto"
    arch: str = "auto"
    memoria_mb: int = 1024
    cpus: int = 2
    qemu: str = ""


@dataclass(frozen=True)
class ConfigSync:
    automatico: bool = True


@dataclass(frozen=True)
class ConfigDev:
    habilitado: bool = False


@dataclass(frozen=True)
class Config:
    vm: ConfigVM = field(default_factory=ConfigVM)
    sync: ConfigSync = field(default_factory=ConfigSync)
    dev: ConfigDev = field(default_factory=ConfigDev)
    origen: Path | None = None


_LIMITES = {"memoria_mb": (256, 16384), "cpus": (1, 16)}


def _seccion(clase: type[Any], nombre: str, datos: Any, archivo: Path) -> Any:
    if datos is None:
        return clase()
    if not isinstance(datos, dict):
        raise ErrorDeConfiguracion(f"{archivo}: [{nombre}] tiene que ser una tabla.")
    conocidas = {campo.name: campo for campo in fields(clase)}
    valores: dict[str, Any] = {}
    for clave, valor in datos.items():
        if clave not in conocidas:
            raise ErrorDeConfiguracion(
                f"{archivo}: [{nombre}] no tiene la clave «{clave}» (las válidas son: {', '.join(conocidas)})."
            )
        esperado = type(getattr(clase(), clave))
        if type(valor) is not esperado:
            raise ErrorDeConfiguracion(
                f"{archivo}: [{nombre}] {clave} tiene que ser {_NOMBRES_TIPO[esperado]}, no «{valor!r}»."
            )
        if clave in _LIMITES:
            minimo, maximo = _LIMITES[clave]
            if not minimo <= valor <= maximo:
                raise ErrorDeConfiguracion(f"{archivo}: [{nombre}] {clave} tiene que estar entre {minimo} y {maximo}.")
        valores[clave] = valor
    return clase(**valores)


_NOMBRES_TIPO = {str: "un texto", int: "un número entero", bool: "true o false"}


def cargar(archivo: Path) -> Config:
    try:
        datos = tomllib.loads(archivo.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ErrorDeConfiguracion(f"{archivo} no es un TOML válido: {error}.") from None
    secciones = {"vm": ConfigVM, "sync": ConfigSync, "dev": ConfigDev}
    desconocidas = set(datos) - set(secciones)
    if desconocidas:
        raise ErrorDeConfiguracion(
            f"{archivo}: secciones desconocidas {', '.join(sorted(desconocidas))} (las válidas son vm, sync y dev)."
        )
    return Config(**{nombre: _seccion(clase, nombre, datos.get(nombre), archivo)
                     for nombre, clase in secciones.items()}, origen=archivo)


def cargar_del_directorio(directorio: Path) -> Config:
    """`project.toml` del directorio de trabajo, o la configuración por defecto si no hay."""
    archivo = directorio / NOMBRE_ARCHIVO
    return cargar(archivo) if archivo.is_file() else Config()
