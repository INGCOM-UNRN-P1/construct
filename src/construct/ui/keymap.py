"""Atajos portables del IDE (decisión D4, PLAN Fase 4).

Los atajos de la SPEC (§5) son los primeros de cada acción; las alternativas cubren las terminales
que no los transmiten: `Ctrl+.` no tiene código de control y casi ninguna terminal lo manda, y
macOS se queda con F10/F11. `Ctrl+I` no sirve de alternativa para F11 porque la terminal lo manda
igual que Tab. Todas las acciones están, además, en la paleta de comandos (`Ctrl+P`).

Se cambian en `project.toml`, con el nombre de la acción y una tecla o una lista:

```toml
[teclas]
paso_dentro = ["f11", "ctrl+o"]
autofix = "alt+enter"
```
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from construct.core.config import ErrorDeConfiguracion


@dataclass(frozen=True)
class Accion:
    nombre: str
    descripcion: str
    teclas: tuple[str, ...]
    """La primera es la que muestra el pie de página."""
    visible: bool = True


ACCIONES: tuple[Accion, ...] = (
    Accion("ejecutar", "Ejecutar (nostromo) o continuar la depuración", ("f5", "ctrl+r")),
    Accion("ejecutar_tetsuo", "Ejecutar con tetsuo (ASan/UBSan)", ("shift+f5",), visible=False),
    Accion("probar", "Pruebas de treadstone", ("f6", "ctrl+t")),
    Accion("compilar", "Compilar con daedalus", ("f7", "ctrl+l")),
    Accion("auditar", "Auditar con ripley", ("f8", "ctrl+g")),
    Accion("breakpoint", "Poner o sacar un breakpoint", ("f9", "ctrl+b")),
    Accion("paso_sobre", "Step over", ("f10", "ctrl+n")),
    Accion("paso_dentro", "Step into", ("f11", "ctrl+o")),
    Accion("depurar", "Empezar a depurar", ("ctrl+f5",), visible=False),
    Accion("terminar_depuracion", "Terminar la depuración", ("ctrl+f2",), visible=False),
    Accion("memoria", "Memoria con bishop en la línea actual", ("f12",), visible=False),
    Accion("sincronizar", "Guardar y sincronizar (git y artefactos)", ("ctrl+s",)),
    Accion("autofix", "Aplicar el autofix de gaff", ("ctrl+full_stop", "alt+enter"), visible=False),
    Accion("explicar", "Explicar este hallazgo (yutani.ia)", ("f1",), visible=False),
    Accion("salir", "Salir", ("ctrl+q",), visible=False),
)
POR_NOMBRE = {a.nombre: a for a in ACCIONES}

_TECLA = re.compile(r"((ctrl|shift|alt|super)\+)*[a-z0-9_]+")
_NOMBRES = {"full_stop": ".", "enter": "Enter", "escape": "Esc"}


def _normalizar(nombre: str, valor: str | Sequence[str]) -> tuple[str, ...]:
    teclas = (valor,) if isinstance(valor, str) else tuple(valor)
    if not teclas or not all(isinstance(t, str) for t in teclas):
        raise ErrorDeConfiguracion(f"[teclas] {nombre} tiene que ser una tecla o una lista de teclas.")
    normalizadas = tuple(t.strip().lower() for t in teclas)
    for tecla in normalizadas:
        if not _TECLA.fullmatch(tecla):
            raise ErrorDeConfiguracion(f"[teclas] {nombre}: «{tecla}» no es una tecla (por ejemplo: f5, ctrl+r, alt+enter).")
    return normalizadas


def armar(sobrescritas: Mapping[str, str | Sequence[str]] | None = None) -> dict[str, tuple[str, ...]]:
    """Acción → teclas, con las de `[teclas]` aplicadas; ErrorDeConfiguracion si algo no cierra."""
    teclas = {a.nombre: a.teclas for a in ACCIONES}
    for nombre, valor in (sobrescritas or {}).items():
        if nombre not in teclas:
            raise ErrorDeConfiguracion(f"[teclas] no hay una acción «{nombre}» (las válidas son: {', '.join(teclas)}).")
        teclas[nombre] = _normalizar(nombre, valor)
    duenos: dict[str, str] = {}
    for nombre, lista in teclas.items():
        for tecla in lista:
            if tecla in duenos:
                raise ErrorDeConfiguracion(f"[teclas] {tecla} está en «{duenos[tecla]}» y en «{nombre}».")
            duenos[tecla] = nombre
    return teclas


def describir(tecla: str) -> str:
    """`ctrl+full_stop` → `Ctrl+.`, `f5` → `F5`."""
    partes = tecla.split("+")
    *modificadores, base = partes
    base = _NOMBRES.get(base, base.upper() if len(base) <= 3 else base.capitalize())
    return "+".join([*(m.capitalize() for m in modificadores), base])


def describir_todas(teclas: Sequence[str]) -> str:
    return " / ".join(describir(t) for t in teclas)
