"""Atajos del IDE con alternativas y `[teclas]` de project.toml (decisión D4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from construct.core.config import ErrorDeConfiguracion, cargar
from construct.ui import keymap


def test_atajos_de_la_spec_primero_y_alternativas() -> None:
    teclas = keymap.armar()
    assert teclas["compilar"][0] == "f7" and teclas["auditar"][0] == "f8" and teclas["probar"][0] == "f6"
    assert teclas["ejecutar"][0] == "f5" and teclas["ejecutar_tetsuo"] == ("shift+f5",)
    assert teclas["autofix"] == ("ctrl+full_stop", "alt+enter")
    assert teclas["breakpoint"] == ("f9", "ctrl+b")
    assert teclas["paso_sobre"] == ("f10", "ctrl+n")
    assert "ctrl+i" not in teclas["paso_dentro"]  # en la terminal es Tab
    assert teclas["sincronizar"] == ("ctrl+s",)


def test_ninguna_tecla_repetida() -> None:
    todas = [t for lista in keymap.armar().values() for t in lista]
    assert len(todas) == len(set(todas))


def test_sobrescribir(tmp_path: Path) -> None:
    archivo = tmp_path / "project.toml"
    archivo.write_text('[teclas]\npaso_dentro = ["F11", "ctrl+e"]\nautofix = "alt+enter"\n', encoding="utf-8")
    teclas = keymap.armar(cargar(archivo).teclas)
    assert teclas["paso_dentro"] == ("f11", "ctrl+e") and teclas["autofix"] == ("alt+enter",)


@pytest.mark.parametrize(("sobrescritas", "mensaje"), [
    ({"volar": "f2"}, "no hay una acción «volar»"),
    ({"compilar": "f5"}, "f5 está en «ejecutar» y en «compilar»"),
    ({"compilar": "control x"}, "no es una tecla"),
    ({"compilar": []}, "una tecla o una lista"),
])
def test_errores(sobrescritas: dict[str, object], mensaje: str) -> None:
    with pytest.raises(ErrorDeConfiguracion, match=mensaje):
        keymap.armar(sobrescritas)  # type: ignore[arg-type]


def test_forma_invalida_en_project_toml(tmp_path: Path) -> None:
    archivo = tmp_path / "project.toml"
    archivo.write_text("[teclas]\ncompilar = 7\n", encoding="utf-8")
    with pytest.raises(ErrorDeConfiguracion, match="una tecla o una lista"):
        cargar(archivo)


def test_describir() -> None:
    assert keymap.describir("ctrl+full_stop") == "Ctrl+."
    assert keymap.describir("shift+f5") == "Shift+F5"
    assert keymap.describir_todas(("alt+enter", "f11")) == "Alt+Enter / F11"
