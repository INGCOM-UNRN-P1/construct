from __future__ import annotations

import pytest

from construct.core.protocolo import (
    CABECERA,
    LARGO_MAXIMO,
    Decodificador,
    ErrorDeProtocolo,
    Marco,
    Tipo,
    codificar,
    trocear,
)


def test_ida_y_vuelta_en_pedazos_arbitrarios() -> None:
    flujo = (codificar(Tipo.HOLA, 0, {"protocolo": 1, "token": "ñ"}) + codificar(Tipo.SALIDA, 7, b"\x00\xff")
             + codificar(Tipo.LATIDO))
    decodificador = Decodificador()
    marcos = [m for byte in flujo for m in decodificador.alimentar(bytes([byte]))]
    assert [m.tipo for m in marcos] == [Tipo.HOLA, Tipo.SALIDA, Tipo.LATIDO]
    assert marcos[0].json() == {"protocolo": 1, "token": "ñ"}
    assert marcos[1] == Marco(Tipo.SALIDA, 7, b"\x00\xff")


def test_cabecera_big_endian() -> None:
    assert codificar(Tipo.EJECUTAR, 0x01020304, b"ab")[:CABECERA.size] == b"\x10\x01\x02\x03\x04\x00\x00\x00\x02"


def test_marco_demasiado_largo() -> None:
    with pytest.raises(ErrorDeProtocolo):
        codificar(Tipo.DATOS, 1, b"x" * (LARGO_MAXIMO + 1))
    with pytest.raises(ErrorDeProtocolo, match="máximo"):
        Decodificador().alimentar(CABECERA.pack(Tipo.DATOS, 1, LARGO_MAXIMO + 1))


def test_tipo_desconocido() -> None:
    with pytest.raises(ErrorDeProtocolo, match="desconocido"):
        Decodificador().alimentar(CABECERA.pack(0x7F, 0, 0))


@pytest.mark.parametrize("datos", [b"{", b"[1]", b"\xff"])
def test_json_invalido(datos: bytes) -> None:
    with pytest.raises(ErrorDeProtocolo):
        Marco(Tipo.ERROR, 0, datos).json()


def test_trocear() -> None:
    marcos = Decodificador().alimentar(b"".join(trocear(Tipo.DATOS, 3, b"x" * 10, bloque=4)))
    assert [len(m.datos) for m in marcos] == [4, 4, 2]
    assert trocear(Tipo.DATOS, 3, b"") == []
