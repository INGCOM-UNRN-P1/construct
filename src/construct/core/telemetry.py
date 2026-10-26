"""Telemetría de la ejecución como `hallazgos` de yutani (RF-06, RF-07).

Dos fuentes, las dos del Guest:

- las muestras `TELEMETRIA` que manda el agente cada `telemetria_ms` desde `/proc/<pid>` (CPU, RSS,
  hilos y cuántos duermen en un futex, descriptores abiertos y E/S acumulada);
- el informe de `libconstruct_preload.so` al terminar: archivos sin `fclose` y bloques sin `free`,
  con el desplazamiento de quien los pidió, que `addr2line` traduce a archivo:línea.

Las anomalías salen con la forma común de `yutani.hallazgos`, así el IDE las muestra en el mismo
panel que los reportes de ripley.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from yutani.hallazgos import hallazgo as _hallazgo

HERRAMIENTA = "telemetria"
PRELOAD = "/usr/local/lib/libconstruct_preload.so"
VENTANA_MS = 2000
UMBRAL_CPU = 0.9
MODULOS_DEL_SISTEMA = ("/lib/", "/lib64/", "/usr/lib/", "/usr/lib64/", "linux-vdso")


def hallazgo(*args: Any, explicacion: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """`yutani.hallazgos.hallazgo` + la clave opcional `explicacion` (el yutani publicado no la recibe)."""
    datos = _hallazgo(*args, **kwargs)
    if explicacion:
        datos["explicacion"] = explicacion
    return datos


@dataclass(frozen=True)
class Muestra:
    t_ms: int
    cpu_ms: int
    rss_kb: int
    hilos: int
    hilos_en_futex: int
    fds: int
    lectura: int
    escritura: int
    estado: str = "?"

    @classmethod
    def desde(cls, datos: Mapping[str, Any]) -> Muestra:
        def entero(clave: str) -> int:
            valor = datos.get(clave, 0)
            return int(valor) if isinstance(valor, (int, float)) else 0

        return cls(entero("t_ms"), entero("cpu_ms"), entero("rss_kb"), entero("hilos"), entero("hilos_en_futex"),
                   entero("fds"), entero("lectura"), entero("escritura"), str(datos.get("estado", "?")))


@dataclass(frozen=True)
class Ubicacion:
    funcion: str | None
    archivo: str | None
    linea: int | None


def _tramos(muestras: Sequence[Muestra], condicion: Any) -> Iterable[tuple[Muestra, Muestra]]:
    """Tramos consecutivos de al menos VENTANA_MS donde se cumple `condicion(anterior, actual)`."""
    inicio: Muestra | None = None
    for anterior, actual in itertools.pairwise(muestras):
        if condicion(anterior, actual):
            inicio = inicio or anterior
            if actual.t_ms - inicio.t_ms >= VENTANA_MS:
                yield inicio, actual
                inicio = None
                return
        else:
            inicio = None


def analizar_muestras(muestras: Sequence[Muestra]) -> list[dict[str, Any]]:
    resultado = []

    def espera_activa(a: Muestra, b: Muestra) -> bool:
        transcurrido = b.t_ms - a.t_ms
        return (transcurrido > 0 and (b.cpu_ms - a.cpu_ms) >= UMBRAL_CPU * transcurrido
                and b.lectura == a.lectura and b.escritura == a.escritura)

    for desde, hasta in _tramos(muestras, espera_activa):
        segundos = (hasta.t_ms - desde.t_ms) / 1000
        resultado.append(hallazgo(
            HERRAMIENTA, "TLM-CPU-01", "rendimiento", "advertencia",
            f"Espera activa: el programa usó el 100% de la CPU durante {segundos:.1f} s sin leer ni escribir nada.",
            sugerencia="Revisá los lazos que esperan una condición: si nada dentro del lazo la cambia, no termina nunca.",
            explicacion="Un lazo que consulta una y otra vez una condición que no cambia ocupa todo el procesador "
                        "sin avanzar (espera activa); suele ser un lazo infinito por una condición mal escrita."))

    def trabado(a: Muestra, b: Muestra) -> bool:
        return b.hilos >= 2 and b.hilos_en_futex >= b.hilos and b.cpu_ms == a.cpu_ms

    for desde, hasta in _tramos(muestras, trabado):
        resultado.append(hallazgo(
            HERRAMIENTA, "TLM-LOCK-01", "control", "advertencia",
            f"Posible deadlock: los {hasta.hilos} hilos quedaron esperando un candado durante "
            f"{(hasta.t_ms - desde.t_ms) / 1000:.1f} s sin usar la CPU.",
            sugerencia="Revisá el orden en que cada hilo toma los mutex: tienen que tomarse siempre en el mismo orden.",
            explicacion="Cuando cada hilo espera un recurso que tiene otro, ninguno puede avanzar."))
    return resultado


def offsets_por_modulo(informe: Mapping[str, Any]) -> dict[str, list[str]]:
    """Qué desplazamientos hay que pasarle a addr2line, agrupados por módulo (sólo los del programa)."""
    pedidos: dict[str, list[str]] = {}
    llamadores = [a.get("llamador") for a in informe.get("archivos_abiertos", [])]
    llamadores += [s.get("llamador") for s in (informe.get("heap") or {}).get("sitios", [])]
    for llamador in llamadores:
        if isinstance(llamador, dict) and not _del_sistema(llamador):
            pedidos.setdefault(str(llamador["modulo"]), []).append(str(llamador["offset"]))
    return pedidos


def _del_sistema(llamador: Mapping[str, Any]) -> bool:
    return str(llamador.get("modulo", "")).startswith(MODULOS_DEL_SISTEMA)


def argv_addr2line(modulo: str, offsets: Sequence[str]) -> list[str]:
    return ["addr2line", "-f", "-C", "-e", modulo, *offsets]


def parsear_addr2line(texto: str, offsets: Sequence[str]) -> dict[str, Ubicacion]:
    lineas = texto.splitlines()
    ubicaciones = {}
    for i, offset in enumerate(offsets):
        funcion = lineas[2 * i] if 2 * i < len(lineas) else "??"
        lugar = lineas[2 * i + 1] if 2 * i + 1 < len(lineas) else "??:0"
        archivo, _, linea = lugar.split(" ")[0].rpartition(":")
        numero = int(linea) if linea.isdigit() and linea != "0" else None
        ubicaciones[offset] = Ubicacion(None if funcion == "??" else funcion,
                                        None if archivo in ("", "??") else archivo, numero)
    return ubicaciones


def analizar_informe(informe: Mapping[str, Any], ubicar: Mapping[tuple[str, str], Ubicacion],
                     ruta_en_host: Any = lambda r: r) -> list[dict[str, Any]]:
    resultado = []

    def donde(llamador: Any) -> Ubicacion:
        if not isinstance(llamador, dict):
            return Ubicacion(None, None, None)
        return ubicar.get((str(llamador.get("modulo")), str(llamador.get("offset"))), Ubicacion(None, None, None))

    for abierto in informe.get("archivos_abiertos", []):
        lugar = donde(abierto.get("llamador"))
        resultado.append(hallazgo(
            HERRAMIENTA, "TLM-FD-01", "archivos", "advertencia",
            f"El archivo «{abierto.get('ruta')}» (abierto en modo «{abierto.get('modo')}») quedó abierto: "
            "faltó fclose() antes de terminar.",
            archivo=ruta_en_host(lugar.archivo), linea=lugar.linea,
            sugerencia="Cerrá cada archivo con fclose() en todos los caminos de salida, también en los de error.",
            explicacion="Cada fopen() ocupa un descriptor del sistema; si no se cierra, lo escrito puede no llegar "
                        "al disco y un programa largo se queda sin descriptores."))

    heap = informe.get("heap") or {}
    propios = [s for s in heap.get("sitios", []) if isinstance(s.get("llamador"), dict) and not _del_sistema(s["llamador"])]
    if propios:
        bloques = sum(int(s.get("bloques", 0)) for s in propios)
        total = sum(int(s.get("bytes", 0)) for s in propios)
        mayor = propios[0]
        lugar = donde(mayor.get("llamador"))
        en = f" en {lugar.funcion}()" if lugar.funcion else ""
        resultado.append(hallazgo(
            HERRAMIENTA, "TLM-MEM-01", "memoria", "advertencia",
            f"Quedaron {bloques} bloque(s) de memoria sin liberar ({total} bytes); el que más dejó reservó "
            f"{mayor.get('bloques')} bloque(s){en}.",
            archivo=ruta_en_host(lugar.archivo), linea=lugar.linea,
            sugerencia="Cada malloc/calloc/realloc necesita su free cuando el dato ya no se usa.",
            explicacion="La memoria que no se libera queda ocupada hasta que el programa termina (una fuga)."))
    return resultado
