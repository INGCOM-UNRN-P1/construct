"""Telemetría en vivo del programa: RAM, CPU, descriptores e hilos (RF-06, SPEC §5).

Con las muestras de `/proc` que manda el agente cada 100 ms: el último valor y la historia como
*sparkline*. Las alertas (espera activa, deadlock, fugas) llegan como hallazgos de telemetría.
"""

from __future__ import annotations

from collections import deque

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Sparkline, Static

from construct.core.telemetry import Muestra

HISTORIA = 120


def cpu_porcentaje(anterior: Muestra | None, actual: Muestra) -> float:
    if anterior is None or actual.t_ms <= anterior.t_ms:
        return 0.0
    return max(0.0, min(100.0, 100 * (actual.cpu_ms - anterior.cpu_ms) / (actual.t_ms - anterior.t_ms)))


def resumen(muestra: Muestra, cpu: float) -> str:
    rss = f"{muestra.rss_kb / 1024:.1f} MB" if muestra.rss_kb >= 1024 else f"{muestra.rss_kb} kB"
    return (f"RAM {rss} · CPU {cpu:.0f}% · {muestra.fds} descriptor(es) · {muestra.hilos} hilo(s)"
            + (f" ({muestra.hilos_en_futex} esperando un candado)" if muestra.hilos_en_futex else ""))


class ResourceMonitor(Vertical):
    DEFAULT_CSS = """
    ResourceMonitor Sparkline { height: 3; margin: 0 0 1 0; }
    ResourceMonitor Label { color: $text-muted; }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._anterior: Muestra | None = None
        self._cpu: deque[float] = deque(maxlen=HISTORIA)
        self._ram: deque[float] = deque(maxlen=HISTORIA)
        self.ultima: tuple[Muestra, float] | None = None

    def compose(self) -> ComposeResult:
        yield Static("[dim]Sin programa corriendo: ejecutá con F5.[/dim]", id="resumen")
        yield Label("CPU")
        yield Sparkline([], id="cpu")
        yield Label("RAM (RSS)")
        yield Sparkline([], id="ram")

    def empezar(self) -> None:
        self._anterior = None
        self._cpu.clear()
        self._ram.clear()

    def agregar(self, muestra: Muestra) -> None:
        cpu = cpu_porcentaje(self._anterior, muestra)
        self._anterior = muestra
        self._cpu.append(cpu)
        self._ram.append(float(muestra.rss_kb))
        self.ultima = (muestra, cpu)
        self.query_one("#resumen", Static).update(resumen(muestra, cpu))
        self.query_one("#cpu", Sparkline).data = list(self._cpu)
        self.query_one("#ram", Sparkline).data = list(self._ram)
