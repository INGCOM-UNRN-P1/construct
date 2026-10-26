"""Marcos del protocolo `construct-ctl` entre el Host y el agente del Guest (docs/protocolo.md).

Sólo codifica y decodifica: el transporte (TCP sobre loopback, autenticación, multiplexado) lo
arma `core/channel.py` en la Fase 3.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

PROTOCOLO = 1
LARGO_MAXIMO = 1 << 20
CABECERA = struct.Struct(">BII")


class Tipo(IntEnum):
    HOLA = 0x01
    BIENVENIDA = 0x02
    ERROR = 0x03
    LATIDO = 0x04
    LATIDO_OK = 0x05
    EJECUTAR = 0x10
    INICIADO = 0x11
    ENTRADA = 0x12
    SALIDA = 0x13
    ERRORES = 0x14
    TERMINADO = 0x15
    SENAL = 0x16
    TAMANO = 0x17
    TELEMETRIA = 0x18
    ESCRIBIR = 0x20
    DATOS = 0x21
    FIN = 0x22
    HECHO = 0x23
    LEER = 0x24
    APAGAR = 0x30


class ErrorDeProtocolo(ValueError):
    """Marco inválido: la conexión con el agente no se puede seguir usando."""


@dataclass(frozen=True)
class Marco:
    tipo: Tipo
    id: int
    datos: bytes = b""

    def json(self) -> dict[str, Any]:
        """Los datos como objeto JSON (para los mensajes de control)."""
        try:
            valor = json.loads(self.datos.decode("utf-8")) if self.datos else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ErrorDeProtocolo(f"el mensaje {self.tipo.name} no trae un JSON válido.") from None
        if not isinstance(valor, dict):
            raise ErrorDeProtocolo(f"el mensaje {self.tipo.name} no trae un objeto JSON.")
        return valor


def codificar(tipo: Tipo, id: int = 0, datos: bytes | dict[str, Any] = b"") -> bytes:
    if isinstance(datos, dict):
        datos = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(datos) > LARGO_MAXIMO:
        raise ErrorDeProtocolo(f"un marco no puede superar {LARGO_MAXIMO} bytes ({len(datos)}).")
    return CABECERA.pack(tipo, id, len(datos)) + datos


def trocear(tipo: Tipo, id: int, datos: bytes, bloque: int = 256 * 1024) -> list[bytes]:
    """Varios marcos de flujo (`ENTRADA`, `DATOS`) para datos de cualquier tamaño."""
    return [codificar(tipo, id, datos[i:i + bloque]) for i in range(0, len(datos), bloque)]


class Decodificador:
    """Arma marcos a partir de bytes que llegan en pedazos arbitrarios."""

    def __init__(self) -> None:
        self._pendiente = bytearray()

    def alimentar(self, datos: bytes) -> list[Marco]:
        self._pendiente += datos
        marcos = []
        while len(self._pendiente) >= CABECERA.size:
            codigo, id, largo = CABECERA.unpack_from(self._pendiente)
            if largo > LARGO_MAXIMO:
                raise ErrorDeProtocolo(f"marco de {largo} bytes (máximo {LARGO_MAXIMO}).")
            if len(self._pendiente) < CABECERA.size + largo:
                break
            try:
                tipo = Tipo(codigo)
            except ValueError:
                raise ErrorDeProtocolo(f"tipo de mensaje desconocido 0x{codigo:02x}.") from None
            marcos.append(Marco(tipo, id, bytes(self._pendiente[CABECERA.size:CABECERA.size + largo])))
            del self._pendiente[:CABECERA.size + largo]
        return marcos
