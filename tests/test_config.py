from __future__ import annotations

from pathlib import Path

import pytest

from construct.core.config import Config, ErrorDeConfiguracion, cargar, cargar_del_directorio


def escribir(tmp_path: Path, texto: str) -> Path:
    archivo = tmp_path / "project.toml"
    archivo.write_text(texto, encoding="utf-8")
    return archivo


def test_sin_archivo_usa_los_valores_por_defecto(tmp_path: Path) -> None:
    config = cargar_del_directorio(tmp_path)
    assert config == Config()
    assert config.vm.accel == "auto" and config.vm.memoria_mb == 1024
    assert config.sync.automatico and not config.dev.habilitado


def test_carga_parcial(tmp_path: Path) -> None:
    config = cargar(escribir(tmp_path, '[vm]\naccel = "tcg"\nmemoria_mb = 768\n\n[dev]\nhabilitado = true\n'))
    assert config.vm.accel == "tcg" and config.vm.memoria_mb == 768 and config.vm.cpus == 2
    assert config.dev.habilitado


@pytest.mark.parametrize(("texto", "mensaje"), [
    ("[vm\n", "no es un TOML válido"),
    ("[red]\nx = 1\n", "secciones desconocidas red"),
    ("[vm]\nram = 1\n", "no tiene la clave «ram»"),
    ('[vm]\ncpus = "dos"\n', "tiene que ser un número entero"),
    ("[sync]\nautomatico = 1\n", "tiene que ser true o false"),
    ("[vm]\nmemoria_mb = 64\n", "entre 256 y 16384"),
    ("vm = 3\n", "tiene que ser una tabla"),
])
def test_errores_en_espanol(tmp_path: Path, texto: str, mensaje: str) -> None:
    with pytest.raises(ErrorDeConfiguracion, match=mensaje):
        cargar(escribir(tmp_path, texto))
