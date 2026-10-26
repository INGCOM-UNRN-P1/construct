from __future__ import annotations

import io
from pathlib import Path

from construct.platform.paths import ancestro_existente, directorios
from construct.platform.terminal import preparar_consola


def test_construct_home_reemplaza_todas_las_carpetas(tmp_path: Path) -> None:
    dirs = directorios({"CONSTRUCT_HOME": str(tmp_path)})
    assert all(d.is_relative_to(tmp_path) for d in dirs.todos())
    dirs.crear()
    assert all(d.is_dir() for d in dirs.todos())


def test_sin_construct_home_usa_platformdirs() -> None:
    dirs = directorios({})
    assert dirs.imagenes.parent == dirs.datos
    assert dirs.estado != dirs.datos
    assert "construct" in str(dirs.datos)


def test_ancestro_existente(tmp_path: Path) -> None:
    assert ancestro_existente(tmp_path / "no" / "existe") == tmp_path


def test_preparar_consola_pasa_a_utf8() -> None:
    flujo = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    preparar_consola((flujo,))
    assert flujo.encoding == "utf-8"
    flujo.write("Revisá la «ñ»")
    flujo.flush()


def test_preparar_consola_tolera_flujos_sin_reconfigure() -> None:
    preparar_consola((io.StringIO(),))
