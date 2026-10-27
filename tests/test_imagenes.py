from __future__ import annotations

import hashlib
import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from construct.core import imagenes
from construct.platform.host_info import Arquitectura


def publicar(carpeta: Path, version: str = "0.1.0", *, protocolo: int = 1, corromper: str | None = None,
             arquitecturas: tuple[str, ...] = ("x86_64", "aarch64"), **extra: Any) -> Path:
    """Arma un release falso: archivos de imagen y manifiesto con URLs relativas."""
    carpeta.mkdir(parents=True, exist_ok=True)
    entradas = {}
    for arq in arquitecturas:
        archivos = []
        for nombre in ("vmlinuz", "rootfs.qcow2"):
            contenido = f"{arq}-{nombre}-{version}".encode() * 1000
            publicado = f"{nombre}-{arq}-{version}"
            (carpeta / publicado).write_bytes(contenido)
            suma = hashlib.sha256(b"otra cosa" if corromper == nombre else contenido).hexdigest()
            archivos.append({"nombre": nombre, "url": publicado, "sha256": suma, "tamano": len(contenido)})
        entradas[arq] = {"archivos": archivos}
    datos = {"schema_version": "1.0.0", "version": version, "protocolo": protocolo, "imagenes": entradas, **extra}
    manifiesto = carpeta / "imagenes.json"
    manifiesto.write_text(json.dumps(datos), encoding="utf-8")
    return manifiesto


def test_instala_y_registra(tmp_path: Path) -> None:
    manifiesto = imagenes.obtener_manifiesto(str(publicar(tmp_path / "release")))
    avances: list[tuple[str, int, int | None]] = []
    imagen, descargada = imagenes.instalar(manifiesto, Arquitectura.X86_64, tmp_path / "imagenes",
                                           progreso=lambda *a: avances.append(a))
    assert descargada and imagen.completa()
    assert imagen.ruta("rootfs.qcow2").read_bytes().startswith(b"x86_64-rootfs.qcow2-0.1.0")
    assert {nombre for nombre, _, _ in avances} == {"vmlinuz", "rootfs.qcow2"}
    registrada = imagenes.instalada(tmp_path / "imagenes", Arquitectura.X86_64)
    assert registrada == imagen
    assert imagenes.instalada(tmp_path / "imagenes", Arquitectura.AARCH64) is None


def test_no_vuelve_a_bajar_lo_que_ya_esta_salvo_que_se_fuerce(tmp_path: Path) -> None:
    manifiesto = imagenes.obtener_manifiesto(str(publicar(tmp_path / "release")))
    destino = tmp_path / "imagenes"
    imagenes.instalar(manifiesto, Arquitectura.AARCH64, destino)
    assert imagenes.instalar(manifiesto, Arquitectura.AARCH64, destino)[1] is False
    assert imagenes.instalar(manifiesto, Arquitectura.AARCH64, destino, forzar=True)[1] is True


def test_version_nueva_reemplaza_y_borra_la_anterior(tmp_path: Path) -> None:
    destino = tmp_path / "imagenes"
    vieja, _ = imagenes.instalar(imagenes.obtener_manifiesto(str(publicar(tmp_path / "r1", "0.1.0"))),
                                 Arquitectura.X86_64, destino)
    nueva, descargada = imagenes.instalar(imagenes.obtener_manifiesto(str(publicar(tmp_path / "r2", "0.2.0"))),
                                          Arquitectura.X86_64, destino)
    assert descargada and nueva.version == "0.2.0"
    assert not vieja.carpeta.exists()


def test_sha_que_no_coincide_no_deja_nada(tmp_path: Path) -> None:
    manifiesto = imagenes.obtener_manifiesto(str(publicar(tmp_path / "release", corromper="rootfs.qcow2")))
    destino = tmp_path / "imagenes"
    with pytest.raises(imagenes.ErrorDeImagen, match="se bajó dañado"):
        imagenes.instalar(manifiesto, Arquitectura.X86_64, destino)
    assert not list(destino.rglob("*.parcial"))
    assert not (destino / "x86_64" / "0.1.0" / "rootfs.qcow2").exists()
    assert imagenes.instalada(destino, Arquitectura.X86_64) is None


def test_arquitectura_que_no_viene_en_el_manifiesto(tmp_path: Path) -> None:
    manifiesto = imagenes.obtener_manifiesto(str(publicar(tmp_path / "r", arquitecturas=("x86_64",))))
    with pytest.raises(imagenes.ErrorDeImagen, match="no trae una imagen aarch64"):
        imagenes.instalar(manifiesto, Arquitectura.AARCH64, tmp_path / "imagenes")


def test_protocolo_no_soportado(tmp_path: Path) -> None:
    manifiesto = imagenes.obtener_manifiesto(str(publicar(tmp_path / "r", protocolo=99)))
    with pytest.raises(imagenes.ErrorDeImagen, match="protocolo 99"):
        imagenes.instalar(manifiesto, Arquitectura.X86_64, tmp_path / "imagenes")


@pytest.mark.parametrize(("datos", "mensaje"), [
    ([], "no tiene la forma esperada"),
    ({"schema_version": "2.0.0", "version": "1", "imagenes": {}}, "esquema"),
    ({"schema_version": "1.0.0", "version": "../x", "imagenes": {}}, "versión inválida"),
    ({"schema_version": "1.0.0", "version": "1", "imagenes": {"riscv64": {"archivos": []}}}, "entrada inválida"),
    ({"schema_version": "1.0.0", "version": "1",
      "imagenes": {"x86_64": {"archivos": [{"nombre": "../../.bashrc", "url": "x", "sha256": "0"}]}}},
     "nombre de archivo inválido"),
])
def test_manifiestos_invalidos(datos: Any, mensaje: str) -> None:
    with pytest.raises(imagenes.ErrorDeImagen, match=mensaje):
        imagenes.parsear_manifiesto(datos, "file:///release/imagenes.json")


def test_urls_relativas_se_resuelven_contra_el_manifiesto() -> None:
    datos = {"schema_version": "1.0.0", "version": "1", "imagenes": {
        "x86_64": {"archivos": [{"nombre": "vmlinuz", "url": "vmlinuz-x86_64", "sha256": "AB"}]}}}
    manifiesto = imagenes.parsear_manifiesto(datos, "https://ejemplo.org/releases/download/v1/imagenes.json")
    archivo = manifiesto.imagenes[Arquitectura.X86_64][0]
    assert archivo.url == "https://ejemplo.org/releases/download/v1/vmlinuz-x86_64"
    assert archivo.sha256 == "ab"


def test_archivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(imagenes.ErrorDeImagen, match="no se pudo descargar"):
        imagenes.obtener_manifiesto(str(tmp_path / "no-existe.json"))


def test_json_invalido(tmp_path: Path) -> None:
    (tmp_path / "m.json").write_text("{", encoding="utf-8")
    with pytest.raises(imagenes.ErrorDeImagen, match="no es un JSON válido"):
        imagenes.obtener_manifiesto(str(tmp_path / "m.json"))


def test_inventario_danado(tmp_path: Path) -> None:
    (tmp_path / imagenes.INVENTARIO).write_text("{", encoding="utf-8")
    with pytest.raises(imagenes.ErrorDeImagen, match="está dañado"):
        imagenes.instalada(tmp_path, Arquitectura.X86_64)


@pytest.fixture
def servidor(tmp_path: Path) -> Iterator[str]:
    raiz = tmp_path / "web"
    publicar(raiz)

    class Manejador(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(raiz), **kwargs)

        def log_message(self, *args: Any) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_descarga_por_http(servidor: str, tmp_path: Path) -> None:
    manifiesto = imagenes.obtener_manifiesto(f"{servidor}/imagenes.json")
    imagen, descargada = imagenes.instalar(manifiesto, Arquitectura.X86_64, tmp_path / "imagenes")
    assert descargada and imagen.completa()


def test_404_explica_que_no_hay_publicacion(servidor: str) -> None:
    with pytest.raises(imagenes.ErrorDeImagen, match="todavía no se publicó"):
        imagenes.obtener_manifiesto(f"{servidor}/no-existe.json")


def test_url_del_manifiesto() -> None:
    assert imagenes.url_del_manifiesto({}) == imagenes.MANIFIESTO_POR_DEFECTO
    assert imagenes.url_del_manifiesto({"CONSTRUCT_MANIFIESTO": "x.json"}) == "x.json"
    assert imagenes.url_del_manifiesto({"CONSTRUCT_MANIFIESTO": "x.json"}, "y.json") == "y.json"


def construir(carpeta: Path, marca: str = "1") -> Path:
    """Lo que deja build_vm.sh para una arquitectura."""
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre in imagenes.ARCHIVOS_DE_IMAGEN:
        (carpeta / nombre).write_bytes(f"{carpeta.name}-{nombre}-{marca}".encode() * 100)
    return carpeta


@pytest.mark.parametrize("pasada", ["salida", "arquitectura"])
def test_imagen_local_de_build_vm(tmp_path: Path, pasada: str) -> None:
    arq = construir(tmp_path / "dist" / "x86_64")
    manifiesto = imagenes.manifiesto_local(tmp_path / "dist" if pasada == "salida" else arq, Arquitectura.X86_64)
    rootfs = hashlib.sha256((arq / "rootfs.qcow2").read_bytes()).hexdigest()
    assert manifiesto.version == "local-" + rootfs[:12] and manifiesto.protocolo == 1
    imagen, copiada = imagenes.instalar(manifiesto, Arquitectura.X86_64, tmp_path / "imagenes")
    assert copiada and imagen.completa() and imagen.archivos["rootfs.qcow2"] == rootfs
    assert imagen.ruta("initramfs").read_bytes() == (arq / "initramfs").read_bytes()


def test_imagen_local_reconstruida_se_reinstala(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    construir(dist / "x86_64")
    previa, _ = imagenes.instalar(imagenes.manifiesto_local(dist, Arquitectura.X86_64), Arquitectura.X86_64,
                                  tmp_path / "imagenes")
    _, otra_vez = imagenes.instalar(imagenes.manifiesto_local(dist, Arquitectura.X86_64), Arquitectura.X86_64,
                                    tmp_path / "imagenes")
    assert not otra_vez
    construir(dist / "x86_64", marca="2")
    nueva, copiada = imagenes.instalar(imagenes.manifiesto_local(dist, Arquitectura.X86_64), Arquitectura.X86_64,
                                       tmp_path / "imagenes")
    assert copiada and nueva.version != previa.version and not previa.carpeta.exists()


def test_imagen_local_con_release(tmp_path: Path) -> None:
    publicar(tmp_path / "release", "0.2.0")
    assert imagenes.manifiesto_local(tmp_path / "release", Arquitectura.AARCH64).version == "0.2.0"


def test_imagen_local_de_otra_arquitectura(tmp_path: Path) -> None:
    with pytest.raises(imagenes.ErrorDeImagen, match="es una imagen aarch64 y este Host necesita una x86_64"):
        imagenes.manifiesto_local(construir(tmp_path / "aarch64"), Arquitectura.X86_64)


def test_imagen_local_incompleta(tmp_path: Path) -> None:
    carpeta = construir(tmp_path / "x86_64")
    (carpeta / "initramfs").unlink()
    with pytest.raises(imagenes.ErrorDeImagen, match="le falta initramfs"):
        imagenes.manifiesto_local(carpeta, Arquitectura.X86_64)
    with pytest.raises(imagenes.ErrorDeImagen, match="no existe la carpeta"):
        imagenes.manifiesto_local(tmp_path / "nada", Arquitectura.X86_64)
