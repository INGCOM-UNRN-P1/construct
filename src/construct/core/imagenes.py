"""Imágenes del Guest: manifiesto publicado, descarga verificada e inventario local (PLAN Fases 1 y 2).

El CI publica, junto con cada versión de la imagen, un manifiesto JSON:

```json
{
  "schema_version": "1.0.0",
  "version": "0.1.0",
  "protocolo": 1,
  "imagenes": {
    "x86_64":  {"archivos": [{"nombre": "vmlinuz", "url": "vmlinuz-x86_64", "sha256": "…", "tamano": 123},
                             {"nombre": "rootfs.qcow2", "url": "rootfs-x86_64.qcow2", "sha256": "…"}]},
    "aarch64": {"archivos": […]}
  }
}
```

Las `url` relativas se resuelven contra la del manifiesto (así sirven igual un *release* de GitHub
que una carpeta local con `file://`). Cada archivo se baja a un `.parcial`, se verifica su SHA-256 y
recién entonces se renombra; el inventario `instaladas.json` registra qué versión hay por arquitectura.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from construct.platform.host_info import Arquitectura

SCHEMA_MANIFIESTO = "1.0.0"
MANIFIESTO_POR_DEFECTO = "https://github.com/INGCOM-UNRN-P1/construct/releases/latest/download/imagenes.json"
VARIABLE_MANIFIESTO = "CONSTRUCT_MANIFIESTO"
INVENTARIO = "instaladas.json"
PROTOCOLOS_SOPORTADOS = frozenset({1})
"""Versiones del protocolo construct-ctl (PLAN §3.2) que entiende este Host."""
BLOQUE = 1024 * 1024
_NOMBRE_SEGURO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""Nombres de archivo y versiones del manifiesto: nada de rutas ni «..» (se usan como nombres locales)."""
TIMEOUT_S = 30

Progreso = Callable[[str, int, int | None], None]
"""(nombre del archivo, bytes bajados, total o None)."""


class ErrorDeImagen(ValueError):
    """No se pudo obtener, verificar o encontrar la imagen del Guest."""


@dataclass(frozen=True)
class ArchivoDeImagen:
    nombre: str
    url: str
    sha256: str
    tamano: int | None = None


@dataclass(frozen=True)
class Manifiesto:
    version: str
    protocolo: int
    imagenes: dict[Arquitectura, tuple[ArchivoDeImagen, ...]]
    origen: str


def url_del_manifiesto(entorno: dict[str, str] | None = None, explicita: str | None = None) -> str:
    entorno = dict(os.environ) if entorno is None else entorno
    return explicita or entorno.get(VARIABLE_MANIFIESTO) or MANIFIESTO_POR_DEFECTO


def _como_url(origen: str) -> str:
    """Acepta una URL o una ruta local (la convierte a `file://`)."""
    if "://" in origen:
        return origen
    return Path(origen).resolve().as_uri()


def _es_loopback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_SIN_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _abrir(url: str) -> Any:
    # Detrás de un proxy (http_proxy/https_proxy) las descargas lo usan; el loopback nunca, porque
    # urllib no entiende rangos como 127.0.0.0/8 en no_proxy y lo mandaría al proxy.
    abrir = _SIN_PROXY.open if _es_loopback(url) else urllib.request.urlopen
    try:
        return abrir(url, timeout=TIMEOUT_S)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ErrorDeImagen(f"no hay nada publicado en {url} (¿todavía no se publicó una imagen?).") from None
        raise ErrorDeImagen(f"el servidor respondió {error.code} al pedir {url}.") from None
    except (urllib.error.URLError, OSError) as error:
        motivo = getattr(error, "reason", error)
        raise ErrorDeImagen(f"no se pudo descargar {url} ({motivo}): revisá tu conexión a internet.") from None


def parsear_manifiesto(datos: Any, origen: str) -> Manifiesto:
    if not isinstance(datos, dict) or not isinstance(datos.get("imagenes"), dict):
        raise ErrorDeImagen(f"el manifiesto {origen} no tiene la forma esperada (falta «imagenes»).")
    if str(datos.get("schema_version", "")).split(".")[0] != SCHEMA_MANIFIESTO.split(".")[0]:
        raise ErrorDeImagen(
            f"el manifiesto {origen} usa el esquema {datos.get('schema_version')!r} y esta versión de construct "
            f"entiende el {SCHEMA_MANIFIESTO}: actualizá construct con `uv tool upgrade construct`."
        )
    version = str(datos.get("version", ""))
    if not _NOMBRE_SEGURO.fullmatch(version) or ".." in version:
        raise ErrorDeImagen(f"el manifiesto {origen} tiene una versión inválida ({version!r}).")
    imagenes: dict[Arquitectura, tuple[ArchivoDeImagen, ...]] = {}
    for nombre_arq, imagen in datos["imagenes"].items():
        try:
            arq = Arquitectura(nombre_arq)
            archivos = tuple(
                ArchivoDeImagen(nombre=a["nombre"], url=urljoin(origen, a["url"]), sha256=a["sha256"].lower(),
                                tamano=a.get("tamano"))
                for a in imagen["archivos"]
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ErrorDeImagen(f"el manifiesto {origen} tiene una entrada inválida para «{nombre_arq}».") from None
        for archivo in archivos:
            if not _NOMBRE_SEGURO.fullmatch(archivo.nombre) or ".." in archivo.nombre:
                raise ErrorDeImagen(f"el manifiesto {origen} tiene un nombre de archivo inválido ({archivo.nombre!r}).")
        imagenes[arq] = archivos
    return Manifiesto(version=version, protocolo=int(datos.get("protocolo", 0)),
                      imagenes=imagenes, origen=origen)


def obtener_manifiesto(origen: str) -> Manifiesto:
    url = _como_url(origen)
    with _abrir(url) as respuesta:
        try:
            datos = json.loads(respuesta.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ErrorDeImagen(f"el manifiesto {url} no es un JSON válido.") from None
    return parsear_manifiesto(datos, url)


# --- Inventario local ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ImagenInstalada:
    arquitectura: Arquitectura
    version: str
    protocolo: int
    carpeta: Path
    archivos: dict[str, str]
    """nombre → sha256."""

    def ruta(self, nombre: str) -> Path:
        return self.carpeta / nombre

    def completa(self) -> bool:
        return all(self.ruta(nombre).is_file() for nombre in self.archivos)


def _leer_inventario(directorio: Path) -> dict[str, Any]:
    archivo = directorio / INVENTARIO
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        raise ErrorDeImagen(f"{archivo} está dañado: borralo y ejecutá `construct setup` de nuevo.") from None
    return datos if isinstance(datos, dict) else {}


def instalada(directorio: Path, arquitectura: Arquitectura) -> ImagenInstalada | None:
    entrada = _leer_inventario(directorio).get(arquitectura.value)
    if not isinstance(entrada, dict):
        return None
    return ImagenInstalada(arquitectura, str(entrada.get("version", "")), int(entrada.get("protocolo", 0)),
                           directorio / arquitectura.value / str(entrada.get("version", "")),
                           dict(entrada.get("archivos", {})))


def _registrar(directorio: Path, imagen: ImagenInstalada) -> None:
    inventario = _leer_inventario(directorio)
    inventario[imagen.arquitectura.value] = {"version": imagen.version, "protocolo": imagen.protocolo,
                                             "archivos": imagen.archivos}
    temporal = directorio / (INVENTARIO + ".parcial")
    temporal.write_text(json.dumps(inventario, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporal, directorio / INVENTARIO)


# --- Descarga ------------------------------------------------------------------------------------


def _descargar(archivo: ArchivoDeImagen, destino: Path, progreso: Progreso | None) -> None:
    parcial = destino.with_name(destino.name + ".parcial")
    suma = hashlib.sha256()
    bajados = 0
    try:
        with _abrir(archivo.url) as respuesta, parcial.open("wb") as salida:
            total = archivo.tamano or (int(respuesta.headers["Content-Length"])
                                       if respuesta.headers.get("Content-Length") else None)
            while bloque := respuesta.read(BLOQUE):
                salida.write(bloque)
                suma.update(bloque)
                bajados += len(bloque)
                if progreso is not None:
                    progreso(archivo.nombre, bajados, total)
        if suma.hexdigest() != archivo.sha256:
            raise ErrorDeImagen(
                f"{archivo.nombre} se bajó dañado (el SHA-256 no coincide): ejecutá `construct setup` de nuevo."
            )
        os.replace(parcial, destino)
    finally:
        parcial.unlink(missing_ok=True)


def instalar(manifiesto: Manifiesto, arquitectura: Arquitectura, directorio: Path, *, forzar: bool = False,
             progreso: Progreso | None = None) -> tuple[ImagenInstalada, bool]:
    """Baja la imagen de `arquitectura` si hace falta; devuelve (imagen, si se descargó algo)."""
    if manifiesto.protocolo not in PROTOCOLOS_SOPORTADOS:
        raise ErrorDeImagen(
            f"la imagen {manifiesto.version} usa el protocolo {manifiesto.protocolo} y esta versión de construct "
            "no lo soporta: actualizá construct con `uv tool upgrade construct`."
        )
    archivos = manifiesto.imagenes.get(arquitectura)
    if not archivos:
        disponibles = ", ".join(a.value for a in manifiesto.imagenes) or "ninguna"
        raise ErrorDeImagen(f"el manifiesto {manifiesto.origen} no trae una imagen {arquitectura.value} "
                            f"(trae: {disponibles}).")
    previa = instalada(directorio, arquitectura)
    esperados = {a.nombre: a.sha256 for a in archivos}
    if not forzar and previa is not None and previa.version == manifiesto.version \
            and previa.archivos == esperados and previa.completa():
        return previa, False

    carpeta = directorio / arquitectura.value / manifiesto.version
    carpeta.mkdir(parents=True, exist_ok=True)
    for archivo in archivos:
        _descargar(archivo, carpeta / archivo.nombre, progreso)
    imagen = ImagenInstalada(arquitectura, manifiesto.version, manifiesto.protocolo, carpeta, esperados)
    _registrar(directorio, imagen)
    if previa is not None and previa.carpeta != carpeta and previa.carpeta.parent == carpeta.parent:
        shutil.rmtree(previa.carpeta, ignore_errors=True)  # La versión anterior ya no se usa.
    return imagen, True
