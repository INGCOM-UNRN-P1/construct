"""Diagnóstico del Host para `construct doctor` (RF-02, LINEAMIENTOS §3.3, PLAN Fase 1).

Usa el sobre JSON común de `yutani.doctor`. Cada chequeo fallido trae la instrucción concreta para
el sistema y la distro del alumno. Si la VM está corriendo, se suma el `mother doctor --json` del
perfil estudiante dentro del Guest (`chequeos_del_guest`).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from yutani.doctor import Chequeo

from construct.core import imagenes
from construct.core.channel import Canal
from construct.core.config import Config
from construct.platform import accel as aceleracion
from construct.platform import binarios
from construct.platform.host_info import Arquitectura, HostInfo, Sistema
from construct.platform.installer_hints import Componente, sugerencia
from construct.platform.paths import Directorios, ancestro_existente

ESPACIO_MINIMO_GB = 2.0
RAM_RECOMENDADA_MB = 4096
ARGV_MOTHER = ["mother", "doctor", "--perfil", "estudiante", "--json"]
TIMEOUT_MOTHER_S = 180
NOMBRE_GUEST = "perfil estudiante (VM)"
PROPOSITO_GUEST = "herramientas de la cátedra dentro de la VM (mother doctor)"


@dataclass
class Entorno:
    """Lo que necesita el diagnóstico, inyectable para los tests."""

    host: HostInfo
    config: Config
    directorios: Directorios
    sondas_aceleracion: aceleracion.SondasAceleracion | None = None
    sondas_binarios: binarios.SondasBinarios | None = None
    espacio_libre: Callable[[str], int] = field(default=lambda ruta: shutil.disk_usage(ruta).free)


def _sistema(host: HostInfo) -> Chequeo:
    return Chequeo("sistema", True, True, host.describir(), "Host donde corre construct")


def _arquitectura(host: HostInfo) -> Chequeo | None:
    if host.arquitectura is None:
        return Chequeo("arquitectura", True, False, f"{host.maquina_nativa} no está soportada",
                       "construct necesita un Host x86_64 o aarch64",
                       "Usá una computadora x86_64 (Intel/AMD) o ARM de 64 bits.")
    if host.emulado:
        return Chequeo("arquitectura", False, False,
                       f"Python {host.maquina} emulado sobre hardware {host.maquina_nativa}",
                       "Python nativo evita la emulación del propio construct",
                       sugerencia(Componente.PYTHON_NATIVO, host))
    return None


def _wsl(host: HostInfo) -> Chequeo | None:
    if not host.wsl:
        return None
    return Chequeo("wsl", False, False, "construct corre dentro de WSL",
                   "en WSL la VM sólo se acelera con virtualización anidada", sugerencia(Componente.WSL, host))


def _componente_aceleracion(host: HostInfo, esperado: aceleracion.Acelerador, detalle: str) -> Componente:
    if esperado is aceleracion.Acelerador.WHPX:
        return Componente.WHPX
    if esperado is aceleracion.Acelerador.HVF:
        return Componente.HVF
    return Componente.KVM_PERMISOS if "permiso" in detalle else Componente.KVM_AUSENTE


def resolver_aceleracion(entorno: Entorno) -> aceleracion.Aceleracion:
    """Acelerador y arquitectura del Guest según el Host y `[vm]`; ValueError si la configuración es inválida."""
    vm = entorno.config.vm
    return aceleracion.detectar(entorno.host, acelerador=vm.accel, arquitectura=vm.arch,
                                sondas=entorno.sondas_aceleracion)


def _aceleracion(entorno: Entorno) -> tuple[Chequeo, Arquitectura | None]:
    host = entorno.host
    try:
        elegida = resolver_aceleracion(entorno)
    except ValueError as error:
        return Chequeo("aceleración", True, False, str(error), "acelerar la VM por hardware",
                       "Corregí `[vm]` en project.toml."), None
    nombre = aceleracion.NOMBRES_ACELERADOR[elegida.acelerador]
    arq = elegida.arquitectura_guest
    if not elegida.compatibilidad:
        return Chequeo("aceleración", True, True, f"{nombre} para un Guest {arq.value} ({elegida.detalle})",
                       "acelerar la VM por hardware"), arq
    if elegida.forzado or elegida.esperado is None:
        # El usuario eligió TCG, o este Host no tiene acelerador soportado: es lento pero funciona.
        return Chequeo("aceleración", False, False, f"{nombre} para un Guest {arq.value}: {elegida.detalle}",
                       "sin aceleración la VM funciona, pero varias veces más lenta"), arq
    componente = _componente_aceleracion(host, elegida.esperado, elegida.detalle)
    return Chequeo("aceleración", True, False, elegida.detalle, "acelerar la VM por hardware",
                   sugerencia(componente, host)), arq


def _imagen(entorno: Entorno, arq: Arquitectura) -> Chequeo:
    nombre = f"imagen del Guest ({arq.value})"
    proposito = "sistema Debian con el perfil estudiante"
    try:
        imagen = imagenes.instalada(entorno.directorios.imagenes, arq)
    except imagenes.ErrorDeImagen as error:
        return Chequeo(nombre, True, False, str(error), proposito, "Ejecutá `construct setup --forzar`.")
    if imagen is None:
        return Chequeo(nombre, True, False, "no descargada", proposito, "Ejecutá `construct setup`.")
    if not imagen.completa():
        return Chequeo(nombre, True, False, f"versión {imagen.version} incompleta en {imagen.carpeta}", proposito,
                       "Ejecutá `construct setup --forzar`.")
    return Chequeo(nombre, True, True, f"versión {imagen.version} (protocolo {imagen.protocolo}) en {imagen.carpeta}",
                   proposito)


def _version_ok(encontrado: binarios.Ejecutable, minima: binarios.Version) -> bool:
    return encontrado.version is not None and encontrado.version >= minima


def _qemu(entorno: Entorno, arq: Arquitectura) -> Chequeo:
    nombre = f"qemu-system-{arq.value}"
    encontrado = binarios.buscar_qemu(entorno.host, arq, ruta_configurada=entorno.config.vm.qemu or None,
                                      sondas=entorno.sondas_binarios)
    minima = binarios.formatear_version(binarios.VERSION_MINIMA_QEMU)
    if encontrado is None:
        return Chequeo(nombre, True, False, "no encontrado", "ejecutar la máquina virtual",
                       sugerencia(Componente.QEMU, entorno.host, arq))
    if not _version_ok(encontrado, binarios.VERSION_MINIMA_QEMU):
        return Chequeo(nombre, True, False, f"{encontrado.texto_version or 'versión desconocida'} ({encontrado.ruta})",
                       f"construct necesita QEMU {minima} o posterior", sugerencia(Componente.QEMU, entorno.host, arq))
    return Chequeo(nombre, True, True, f"{encontrado.texto_version} ({encontrado.ruta})", "ejecutar la máquina virtual")


def _qemu_img(entorno: Entorno, arq: Arquitectura) -> Chequeo:
    qemu_sistema = binarios.buscar_qemu(entorno.host, arq, ruta_configurada=entorno.config.vm.qemu or None,
                                        sondas=entorno.sondas_binarios)
    encontrado = binarios.buscar_qemu_img(entorno.host, qemu_sistema, sondas=entorno.sondas_binarios)
    proposito = "crear los discos de la VM"
    if encontrado is None:
        return Chequeo("qemu-img", True, False, "no encontrado", proposito,
                       sugerencia(Componente.QEMU_IMG, entorno.host, arq))
    return Chequeo("qemu-img", True, True, f"{encontrado.texto_version} ({encontrado.ruta})", proposito)


def _git(entorno: Entorno) -> Chequeo:
    encontrado = binarios.buscar_git(entorno.host, sondas=entorno.sondas_binarios)
    minima = binarios.formatear_version(binarios.VERSION_MINIMA_GIT)
    proposito = "sincronizar el código con la VM (construct sync)"
    if encontrado is None:
        return Chequeo("git", True, False, "no encontrado", proposito, sugerencia(Componente.GIT, entorno.host))
    if not _version_ok(encontrado, binarios.VERSION_MINIMA_GIT):
        return Chequeo("git", True, False, f"{encontrado.texto_version} ({encontrado.ruta})",
                       f"construct necesita git {minima} o posterior", sugerencia(Componente.GIT, entorno.host))
    return Chequeo("git", True, True, f"{encontrado.texto_version} ({encontrado.ruta})", proposito)


def _espacio(entorno: Entorno) -> Chequeo:
    carpeta = ancestro_existente(entorno.directorios.datos)
    libre_gb = entorno.espacio_libre(str(carpeta)) / 1024**3
    ok = libre_gb >= ESPACIO_MINIMO_GB
    return Chequeo("espacio en disco", True, ok, f"{libre_gb:.1f} GB libres en {carpeta}",
                   f"imágenes y discos de la VM (mínimo {ESPACIO_MINIMO_GB:.0f} GB)",
                   "" if ok else f"Liberá espacio en {carpeta} o elegí otra carpeta con CONSTRUCT_HOME.")


def _memoria(host: HostInfo) -> Chequeo:
    if host.ram_mb is None:
        return Chequeo("memoria", False, False, "no se pudo medir", "la VM usa 1 GB por defecto")
    ok = host.ram_mb >= RAM_RECOMENDADA_MB
    return Chequeo("memoria", False, ok, f"{host.ram_mb / 1024:.1f} GB", "la VM usa 1 GB por defecto",
                   "" if ok else "Con menos de 4 GB, bajá `[vm] memoria_mb` en project.toml (por ejemplo, a 768).")


def _gdb_dev(entorno: Entorno) -> Chequeo:
    host = entorno.host
    proposito = "modo desarrollo: GDB del Host contra el gdbserver del Guest"
    if host.sistema is Sistema.MACOS and host.arquitectura is Arquitectura.AARCH64:
        return Chequeo("gdb (desarrollo)", True, False, "GDB no soporta Apple Silicon", proposito,
                       sugerencia(Componente.GDB_DEV, host))
    encontrado = binarios.buscar_gdb(host, sondas=entorno.sondas_binarios)
    if encontrado is None:
        return Chequeo("gdb (desarrollo)", True, False, "no encontrado", proposito, sugerencia(Componente.GDB_DEV, host))
    return Chequeo("gdb (desarrollo)", True, True, f"{encontrado.texto_version} ({encontrado.ruta})", proposito)


def chequeos(entorno: Entorno, *, dev: bool = False, imagen: bool = True) -> list[Chequeo]:
    """Todos los chequeos del Host; `imagen=False` omite el de la imagen (lo usa `setup` antes de bajarla)."""
    resultado = [_sistema(entorno.host)]
    resultado += [c for c in (_arquitectura(entorno.host), _wsl(entorno.host)) if c is not None]
    chequeo_aceleracion, arq = _aceleracion(entorno)
    resultado.append(chequeo_aceleracion)
    if arq is not None:
        resultado.append(_qemu(entorno, arq))
        resultado.append(_qemu_img(entorno, arq))
        if imagen:
            resultado.append(_imagen(entorno, arq))
    resultado += [_git(entorno), _espacio(entorno), _memoria(entorno.host)]
    if dev or entorno.config.dev.habilitado:
        resultado.append(_gdb_dev(entorno))
    return resultado


# --- Guest: mother doctor del perfil estudiante -------------------------------------------------


def guest_sin_consultar(detalle: str, sugerencia: str = "") -> Chequeo:
    """El Guest no se pudo consultar: informativo, no hace fallar al doctor del Host."""
    return Chequeo(NOMBRE_GUEST, False, False, detalle, PROPOSITO_GUEST, sugerencia)


def chequeos_del_guest(salida: str, codigo: int) -> list[Chequeo]:
    """El resumen del `mother doctor --json` del Guest más cada chequeo requerido que falló."""
    try:
        datos = json.loads(salida)
        # construct es del perfil estudiante pero corre en el Host: su doctor dentro de la VM no aplica.
        lista = [c for c in datos.get("chequeos", []) if isinstance(c, dict) and c.get("nombre") != "construct"]
    except (ValueError, AttributeError):
        return [Chequeo(NOMBRE_GUEST, True, False, f"mother doctor --json no devolvió JSON (código {codigo})",
                        PROPOSITO_GUEST, "Actualizá la imagen con `construct setup --forzar`.")]
    fallidos = [c for c in lista if c.get("requerido") and not c.get("ok")]
    ok = not fallidos  # el `ok` de mother contaría a construct
    version = f"mother {datos['version']}: " if datos.get("version") else ""
    if ok:
        detalle = f"{version}{sum(1 for c in lista if c.get('requerido'))} componentes requeridos en verde"
    else:
        detalle = f"{version}faltan " + ", ".join(str(c.get("nombre", "?")) for c in fallidos) if fallidos else \
            f"{version}informa un problema (código {codigo})"
    resultado = [Chequeo(NOMBRE_GUEST, True, ok, detalle, PROPOSITO_GUEST,
                         "" if ok else "Si no se arregla, actualizá la imagen con `construct setup --forzar`.")]
    for c in fallidos:
        sugerencia = str(c.get("sugerencia") or "")
        resultado.append(Chequeo(f"VM: {c.get('nombre', '?')}", True, False, str(c.get("detalle", "")),
                                 str(c.get("proposito", "")), f"En la VM: {sugerencia}" if sugerencia else ""))
    return resultado


async def consultar_guest(canal: Canal, *, timeout: float = TIMEOUT_MOTHER_S) -> list[Chequeo]:
    try:
        resultado = await asyncio.wait_for(canal.correr(ARGV_MOTHER), timeout)
    except TimeoutError:
        return [guest_sin_consultar(f"mother doctor no respondió en {timeout:.0f} s")]
    if resultado.error:
        return [Chequeo(NOMBRE_GUEST, True, False, resultado.error, PROPOSITO_GUEST,
                        "Actualizá la imagen con `construct setup --forzar`.")]
    return chequeos_del_guest(resultado.texto(), resultado.codigo)
