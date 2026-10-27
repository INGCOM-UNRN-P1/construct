from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from construct.cli import app
from construct.core.config import Config, ConfigVM
from construct.core.setup import preparar
from construct.platform.host_info import Arquitectura, Sistema
from tests.conftest import crear_host
from tests.test_doctor import entorno
from tests.test_imagenes import construir, publicar


def test_prepara_carpetas_y_baja_la_imagen_de_la_arquitectura_del_host(tmp_path: Path) -> None:
    manifiesto = str(publicar(tmp_path / "release"))
    e = entorno(crear_host(Sistema.MACOS, Arquitectura.AARCH64),
                {"/opt/homebrew/bin/qemu-system-aarch64": "QEMU emulator version 10.0.0",
                 "/opt/homebrew/bin/qemu-img": "qemu-img version 10.0.0",
                 "/opt/homebrew/bin/git": "git version 2.46.0"})
    resultado = preparar(e, manifiesto=manifiesto)
    assert all(d.is_dir() for d in e.directorios.todos())
    assert resultado.ok, resultado
    assert resultado.imagen is not None and resultado.imagen.arquitectura is Arquitectura.AARCH64
    assert resultado.descargada
    assert not preparar(e, manifiesto=manifiesto).descargada


def test_host_incompleto_igual_baja_la_imagen_pero_no_da_ok(tmp_path: Path) -> None:
    resultado = preparar(entorno(crear_host(Sistema.WINDOWS), {}, whpx=(False, "no")),
                         manifiesto=str(publicar(tmp_path / "release")))
    assert resultado.imagen is not None
    assert not resultado.ok
    assert {c.nombre for c in resultado.faltantes} >= {"aceleración", "qemu-system-x86_64", "git"}


def test_error_de_imagen_se_informa_sin_excepcion(tmp_path: Path) -> None:
    resultado = preparar(entorno(crear_host()), manifiesto=str(tmp_path / "no-existe.json"))
    assert resultado.imagen is None and not resultado.ok
    assert resultado.error_imagen is not None and "no se pudo descargar" in resultado.error_imagen


def test_config_invalida_no_elige_imagen(tmp_path: Path) -> None:
    resultado = preparar(entorno(crear_host(), config=Config(vm=ConfigVM(accel="whpx"))),
                         manifiesto=str(publicar(tmp_path / "release")))
    assert resultado.error_imagen is not None and "no se puede elegir la imagen" in resultado.error_imagen


def test_sin_imagen_no_descarga(tmp_path: Path) -> None:
    resultado = preparar(entorno(crear_host()), manifiesto="no-se-usa", sin_imagen=True)
    assert resultado.imagen is None and resultado.error_imagen is None and not resultado.ok


def test_imagen_local_en_lugar_del_manifiesto(tmp_path: Path) -> None:
    construir(tmp_path / "dist" / "aarch64")
    e = entorno(crear_host(Sistema.MACOS, Arquitectura.AARCH64))
    resultado = preparar(e, manifiesto="no-se-usa", imagen_local=tmp_path / "dist")
    assert resultado.error_imagen is None and resultado.descargada
    assert resultado.imagen is not None and resultado.imagen.version.startswith("local-")


runner = CliRunner()


def test_cli_setup_json_con_manifiesto_local(tmp_path: Path, construct_home: Path) -> None:
    salida = runner.invoke(app, ["setup", "--json", "--manifiesto", str(publicar(tmp_path / "release"))])
    datos = json.loads(salida.stdout)
    assert datos["herramienta"] == "construct"
    assert datos["error_imagen"] is None
    assert datos["imagen"]["descargada"] is True
    assert Path(datos["imagen"]["carpeta"]).is_relative_to(construct_home)
    assert salida.exit_code == (0 if datos["ok"] else 1)


def test_cli_setup_sin_publicacion(tmp_path: Path) -> None:
    salida = runner.invoke(app, ["setup", "--manifiesto", str(tmp_path / "nada.json")])
    assert salida.exit_code == 1
    assert "Imagen del Guest" in salida.output


def test_cli_setup_con_imagen_local(tmp_path: Path, construct_home: Path) -> None:
    construir(tmp_path / "dist" / "x86_64")
    construir(tmp_path / "dist" / "aarch64")
    salida = runner.invoke(app, ["setup", "--json", "--imagen", str(tmp_path / "dist")])
    datos = json.loads(salida.stdout)
    assert datos["error_imagen"] is None and datos["imagen"]["descargada"] is True
    assert datos["imagen"]["version"].startswith("local-")


def test_cli_setup_imagen_no_se_combina_con_manifiesto(tmp_path: Path) -> None:
    salida = runner.invoke(app, ["setup", "--imagen", str(tmp_path), "--manifiesto", "x.json"])
    assert salida.exit_code == 2 and "no se combina" in salida.output


@pytest.mark.parametrize("opcion", ["-h", "--help"])
def test_cli_setup_ayuda(opcion: str) -> None:
    assert runner.invoke(app, ["setup", opcion]).exit_code == 0
