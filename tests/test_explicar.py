"""«Explicar este hallazgo» (RF-09): sólo viaja la línea afectada y el hallazgo."""

from __future__ import annotations

from pathlib import Path

import pytest
from yutani import ia

from construct.core import explicar

HALLAZGO = {"herramienta": "spunkmeyer", "codigo": "SPK-001", "severidad": "advertencia",
            "mensaje": "Evitá while(!feof(f))", "archivo": "main.c", "linea": 3, "columna": None}


class Falso:
    def __init__(self, respuesta: str = "Porque feof sólo avisa después de leer.") -> None:
        self.respuesta, self.pedidos = respuesta, []

    def generar(self, prompt: str, *, sistema: str | None = None, modelo: str | None = None) -> str:
        self.pedidos.append((prompt, sistema))
        return self.respuesta


def proyecto(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "main.c").write_text("int main(void)\n{\n    while (!feof(f)) {\n    SECRETO\n}\n", encoding="utf-8")
    return tmp_path


def test_manda_solo_la_linea_y_el_hallazgo(tmp_path: Path) -> None:
    falso = Falso()
    texto = explicar.explicar(proyecto(tmp_path), HALLAZGO, cliente=falso)
    assert texto == "Porque feof sólo avisa después de leer."
    [(prompt, sistema)] = falso.pedidos
    assert "while (!feof(f)) {" in prompt and "SPK-001" in prompt
    assert "SECRETO" not in prompt and "int main" not in prompt and "columna" not in prompt
    assert sistema is not None and "rioplatense" in sistema


def test_sin_linea_o_fuera_del_proyecto(tmp_path: Path) -> None:
    raiz = proyecto(tmp_path / "tp")
    assert explicar.linea_afectada(raiz, {**HALLAZGO, "linea": None}) is None
    assert explicar.linea_afectada(raiz, {**HALLAZGO, "linea": 99}) is None
    (tmp_path / "afuera.c").write_text("x\ny\nz\n")
    assert explicar.linea_afectada(raiz, {**HALLAZGO, "archivo": "../afuera.c"}) is None
    assert explicar.linea_afectada(raiz, {**HALLAZGO, "archivo": "/usr/include/stdio.h"}) is None
    assert "```c" not in explicar.armar_prompt(HALLAZGO, None)


def test_sin_razonamiento(tmp_path: Path) -> None:
    falso = Falso("<think>pensando…</think>\nRespuesta.")
    assert explicar.explicar(proyecto(tmp_path), HALLAZGO, cliente=falso) == "Respuesta."


def test_ia_no_disponible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fallar(**_kwargs: object) -> object:
        raise ia.ErrorIA("no hay un servidor en http://localhost:11434/v1")

    monkeypatch.setattr(ia, "cliente", fallar)
    with pytest.raises(ValueError, match="no se pudo consultar a la IA: no hay un servidor"):
        explicar.explicar(proyecto(tmp_path), HALLAZGO)
