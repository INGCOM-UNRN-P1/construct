"""vm_image/manifiesto_guest.py saca a construct del manifiesto que usa mother dentro de la imagen."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

RUTA = Path(__file__).resolve().parent.parent / "vm_image" / "manifiesto_guest.py"
_spec = importlib.util.spec_from_file_location("manifiesto_guest", RUTA)
assert _spec is not None and _spec.loader is not None
manifiesto_guest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manifiesto_guest)

MANIFIESTO = """# Ecosistema
version = 1

[perfiles]
estudiante = "Lo del alumno"

[[repo]]
nombre = "gaff"
perfiles = ["estudiante"]

[[repo]]
nombre = "construct"
# Corre en el Host.
perfiles = ["estudiante"]
sistema = ["git", "qemu-system-x86_64"]

[[repo]]
nombre = "jonesy"
perfiles = ["estudiante"]
"""


def test_saca_solo_a_construct(tmp_path: Path) -> None:
    entrada, salida = tmp_path / "ecosistema.toml", tmp_path / "guest.toml"
    entrada.write_text(MANIFIESTO, encoding="utf-8")
    assert manifiesto_guest.main([str(entrada), str(salida), "construct"]) == 0
    datos = tomllib.loads(salida.read_text(encoding="utf-8"))
    assert [r["nombre"] for r in datos["repo"]] == ["gaff", "jonesy"]
    assert datos["perfiles"] == {"estudiante": "Lo del alumno"} and datos["version"] == 1


def test_falla_si_el_repo_no_esta() -> None:
    with pytest.raises(ValueError, match="no están en el manifiesto: sulaco"):
        manifiesto_guest.sin_repos(MANIFIESTO, {"sulaco"})


def test_falla_si_queda_referenciado() -> None:
    texto = MANIFIESTO + '\n[cuatrimestres.2026-2]\nmatriz = { construct = "v0.1.0" }\n'
    with pytest.raises(ValueError, match="quedan referencias a: construct"):
        manifiesto_guest.sin_repos(texto, {"construct"})


def test_manifiesto_real_de_p1_tools() -> None:
    real = RUTA.parents[2] / "p1-tools" / "ecosistema.toml"
    if not real.is_file():
        pytest.skip("p1-tools no está clonado al lado de construct")
    texto = manifiesto_guest.sin_repos(real.read_text(encoding="utf-8"), {"construct"})
    nombres = {r["nombre"] for r in tomllib.loads(texto)["repo"]}
    assert "construct" not in nombres and {"gaff", "ripley", "mother"} <= nombres
