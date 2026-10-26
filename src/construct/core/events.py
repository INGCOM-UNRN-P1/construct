"""Comandos y eventos inmutables entre el núcleo y la interfaz (SPEC §4, PLAN Fase 3).

La interfaz (la TUI de Textual o una GUI futura) nunca habla con la VM: pone *comandos* en la
`cmd_queue` y dibuja los *eventos* que el `Orquestador` (`core/orquestador.py`) publica en la
`evt_queue`. Todos son `dataclass(frozen=True)` con colecciones inmutables, así un evento se puede
pasar entre tareas o hilos sin copiarlo.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from construct.core.telemetry import Muestra

Hallazgo = Mapping[str, Any]


class Tarea(StrEnum):
    COMPILAR = "compilar"
    AUDITAR = "auditar"
    AUTOFIX = "autofix"
    EJECUTAR = "ejecutar"
    PROBAR = "probar"
    SINCRONIZAR = "sincronizar"
    DEPURAR = "depurar"
    MEMORIA = "memoria"
    REPRODUCTOR = "reproductor"


class ModoEjecucion(StrEnum):
    NORMAL = "normal"
    """Con telemetría (RF-06) y post-mortem de hal ante una señal fatal (RF-04)."""
    TETSUO = "tetsuo"
    """Binario instrumentado con ASan/UBSan y explicado por tetsuo."""
    AISLADO = "aislado"
    """Dentro del sandbox de nostromo, con sus cuotas."""


class Paso(StrEnum):
    SIGUIENTE = "siguiente"
    """F10: step over."""
    ENTRAR = "entrar"
    """F11: step into."""
    CONTINUAR = "continuar"
    """Hasta el próximo breakpoint o el final."""


class AccionSync(StrEnum):
    COMPLETA = "completa"
    SUBIR = "subir"
    BAJAR = "bajar"
    ARTEFACTOS = "artefactos"


# --- Comandos (interfaz → núcleo) -------------------------------------------------------------


@dataclass(frozen=True)
class Compilar:
    """F7: daedalus con los flags normativos."""

    salida: str = "programa"


@dataclass(frozen=True)
class Auditar:
    """F8 o al guardar: ripley (gaff, spunkmeyer, kaneda)."""

    objetivo: str = "."


@dataclass(frozen=True)
class AplicarAutofix:
    """Ctrl+. (o su alternativa): gaff fix y la vuelta de los arreglos al Host."""

    rutas: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ejecutar:
    """F5 / Shift+F5. `entrada` sólo se usa en TETSUO y AISLADO, que la reciben de una vez."""

    argumentos: tuple[str, ...] = ()
    modo: ModoEjecucion = ModoEjecucion.NORMAL
    entrada: bytes = b""


@dataclass(frozen=True)
class EnviarEntrada:
    """Entrada estándar para el programa que está corriendo; vacío = fin de la entrada."""

    datos: bytes


@dataclass(frozen=True)
class Interrumpir:
    """Ctrl+C sobre el programa que está corriendo: nunca es una señal del Host (PLAN §3.2)."""

    senal: str = "INT"


@dataclass(frozen=True)
class Probar:
    """F6: las suites de treadstone (tests/test_*.c)."""


@dataclass(frozen=True)
class Sincronizar:
    """Ctrl+S: exactamente `construct sync` (o una de sus acciones)."""

    accion: AccionSync = AccionSync.COMPLETA


@dataclass(frozen=True)
class AlternarBreakpoint:
    """F9: pone o saca un breakpoint (ruta relativa al proyecto); vale también antes de depurar."""

    archivo: str
    linea: int


@dataclass(frozen=True)
class Depurar:
    """Compila con -g -O0 y corre el programa en el GDB del Guest hasta el primer breakpoint.

    `entrada` es la entrada estándar completa del programa (GDB usa la suya para los comandos MI).
    """

    argumentos: tuple[str, ...] = ()
    entrada: bytes = b""


@dataclass(frozen=True)
class Avanzar:
    paso: Paso = Paso.SIGUIENTE


@dataclass(frozen=True)
class TerminarDepuracion:
    pass


@dataclass(frozen=True)
class InspeccionarMemoria:
    """bishop: foto de la pila y el heap en `archivo:linea`, con la misma entrada de la depuración."""

    archivo: str
    linea: int
    entrada: bytes = b""


@dataclass(frozen=True)
class GuardarReproductor:
    """El botón del modal de hal: `hal_reproducer.sh` del último crash, a `.construct/artefactos/`."""


Comando = (Compilar | Auditar | AplicarAutofix | Ejecutar | EnviarEntrada | Interrumpir | Probar | Sincronizar
           | AlternarBreakpoint | Depurar | Avanzar | TerminarDepuracion | InspeccionarMemoria | GuardarReproductor)


# --- Eventos (núcleo → interfaz) --------------------------------------------------------------


@dataclass(frozen=True)
class TareaIniciada:
    tarea: Tarea


@dataclass(frozen=True)
class TareaTerminada:
    tarea: Tarea
    ok: bool
    resumen: str = ""


@dataclass(frozen=True)
class ErrorDeTarea:
    """La tarea no se pudo hacer (VM caída, git ausente, ...): mensaje en español para mostrar tal cual."""

    tarea: Tarea
    mensaje: str


@dataclass(frozen=True)
class HallazgosRecibidos:
    """Hallazgos con la forma de yutani y rutas relativas al proyecto, para el gutter y el panel."""

    fuente: str
    hallazgos: tuple[Hallazgo, ...]


@dataclass(frozen=True)
class SalidaDeHerramienta:
    """Lo que dijo una herramienta cuando no hubo JSON que leer (falló antes de informarlo)."""

    herramienta: str
    texto: str


@dataclass(frozen=True)
class SalidaPrograma:
    datos: bytes
    errores: bool = False


@dataclass(frozen=True)
class ProgramaTerminado:
    codigo: int
    senal: str | None = None
    detalle: str = ""
    """Para AISLADO, el motivo de nostromo (tiempo o memoria agotados)."""


@dataclass(frozen=True)
class MuestraTelemetria:
    """Una muestra de /proc cada 100 ms, para el monitor de recursos (RF-06)."""

    muestra: Muestra


@dataclass(frozen=True)
class PostMortem:
    """El diagnóstico de hal ante una señal fatal: abre `hal_postmortem_modal`."""

    datos: Mapping[str, Any]


@dataclass(frozen=True)
class ResultadoPrueba:
    prueba: str
    ok: bool
    compila: bool
    salida: str
    codigo: int | None = None
    senal: str | None = None


@dataclass(frozen=True)
class SincronizacionTerminada:
    subidos: bool
    bajados: bool
    conflictos: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtefactoDescargado:
    """Un .bmp, .wav, .html/.mmd o reproducer.sh nuevo en `.construct/artefactos/`."""

    ruta: Path


@dataclass(frozen=True)
class BreakpointsActualizados:
    breakpoints: frozenset[tuple[str, int]]


@dataclass(frozen=True)
class MarcoDePila:
    nivel: int
    funcion: str
    archivo: str | None
    linea: int | None


@dataclass(frozen=True)
class DepuracionDetenida:
    """El programa se detuvo (breakpoint o paso): el editor marca la línea y se actualiza Registros."""

    motivo: str
    archivo: str | None
    linea: int | None
    funcion: str | None
    pila: tuple[MarcoDePila, ...] = ()
    variables: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class DepuracionTerminada:
    codigo: int | None
    senal: str | None = None


@dataclass(frozen=True)
class MemoriaBishop:
    """El snapshot de bishop (`frames`/`stack`, `heap`, `fugas_detectadas`…) para `bishop_view`."""

    datos: Mapping[str, Any]


Evento = (TareaIniciada | TareaTerminada | ErrorDeTarea | HallazgosRecibidos | SalidaDeHerramienta | SalidaPrograma
          | ProgramaTerminado | MuestraTelemetria | PostMortem | ResultadoPrueba | SincronizacionTerminada
          | ArtefactoDescargado | BreakpointsActualizados | DepuracionDetenida | DepuracionTerminada | MemoriaBishop)
