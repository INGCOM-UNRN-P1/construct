"""El agente del Guest compilado en el Host (Linux) y manejado con el protocolo real.

Usa un socket Unix en lugar del puerto VirtIO (`--conectar unix:`) y un archivo en lugar del
fw_cfg (`--token-archivo`): el resto del código es el mismo que corre en la VM.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from construct.core.protocolo import PROTOCOLO, Decodificador, Marco, Tipo, codificar, trocear

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("make") is None or shutil.which("cc") is None,
    reason="el agente usa APIs de Linux (signalfd, forkpty, setfsuid) y se compila con cc",
)


@pytest.fixture(scope="module")
def binario(agente_compilado: Path) -> Path:
    return agente_compilado


class Sesion:
    def __init__(self, conexion: socket.socket) -> None:
        self.conexion = conexion
        self.decodificador = Decodificador()
        self.recibidos: list[Marco] = []

    def enviar(self, tipo: Tipo, id: int = 0, datos: bytes | dict[str, Any] = b"") -> None:
        self.conexion.sendall(codificar(tipo, id, datos))

    def recibir(self, timeout: float = 10) -> Marco:
        limite = time.monotonic() + timeout
        while not self.recibidos:
            restante = limite - time.monotonic()
            if restante <= 0:
                raise TimeoutError("el agente no respondió")
            self.conexion.settimeout(restante)
            datos = self.conexion.recv(65536)
            if not datos:
                raise ConnectionError("el agente cerró la conexión")
            self.recibidos += self.decodificador.alimentar(datos)
        return self.recibidos.pop(0)

    def esperar(self, tipo: Tipo, id: int | None = None, timeout: float = 10) -> Marco:
        while True:
            marco = self.recibir(timeout)
            if marco.tipo is tipo and (id is None or marco.id == id):
                return marco

    def ejecutar(self, id: int, argv: list[str], entrada: bytes | None = None, timeout: float = 10,
                 **opciones: Any) -> tuple[bytes, bytes, dict[str, Any], list[Marco]]:
        self.enviar(Tipo.EJECUTAR, id, {"argv": argv, **opciones})
        if entrada is not None:
            # Como el Host real: se escribe mientras se lee, si no las dos puntas se bloquean.
            flujo = b"".join(trocear(Tipo.ENTRADA, id, entrada)) + codificar(Tipo.ENTRADA, id)
            threading.Thread(target=self.conexion.sendall, args=(flujo,), daemon=True).start()
        salida, errores, otros = b"", b"", []
        while True:
            marco = self.recibir(timeout)
            if marco.id != id:
                continue
            if marco.tipo is Tipo.SALIDA:
                salida += marco.datos
            elif marco.tipo is Tipo.ERRORES:
                errores += marco.datos
            elif marco.tipo is Tipo.TERMINADO:
                return salida, errores, marco.json(), otros
            else:
                otros.append(marco)


@pytest.fixture
def agente(binario: Path, tmp_path: Path) -> Iterator[tuple[Sesion, subprocess.Popen[bytes], Path]]:
    carpeta = Path(tempfile.mkdtemp(prefix="cagt"))  # sun_path admite ~108 bytes
    ruta = carpeta / "ctl.sock"
    token = tmp_path / "token"
    token.write_text("secreto-123\n", encoding="utf-8")
    servidor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    servidor.bind(str(ruta))
    servidor.listen(1)
    servidor.settimeout(10)
    proceso = subprocess.Popen([str(binario), "--conectar", f"unix:{ruta}", "--token-archivo", str(token),
                                "--sin-apagado", "--espera-host", "5"], stderr=subprocess.PIPE)
    conexion, _ = servidor.accept()
    sesion = Sesion(conexion)
    hola = sesion.recibir()
    assert hola.tipo is Tipo.HOLA
    datos = hola.json()
    assert datos["token"] == "secreto-123" and datos["protocolo"] == PROTOCOLO
    sesion.enviar(Tipo.BIENVENIDA, 0, {"protocolo": PROTOCOLO, "latido_s": 2})
    try:
        yield sesion, proceso, tmp_path
    finally:
        try:
            sesion.enviar(Tipo.APAGAR)
            proceso.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            proceso.kill()
            proceso.wait()
        conexion.close()
        servidor.close()
        shutil.rmtree(carpeta, ignore_errors=True)


def test_hola_informa_version_y_arquitectura(binario: Path) -> None:
    salida = subprocess.run([str(binario), "--version"], capture_output=True, text=True, check=True)
    assert f"protocolo {PROTOCOLO}" in salida.stdout


def test_ejecutar_con_salida_errores_y_codigo(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, carpeta = agente
    salida, errores, fin, otros = sesion.ejecutar(
        1, ["sh", "-c", "echo hola; echo problema >&2; pwd; exit 3"], cwd=str(carpeta))
    assert salida == f"hola\n{carpeta}\n".encode()
    assert errores == b"problema\n"
    assert fin == {"codigo": 3, "senal": None}
    assert otros[0].tipo is Tipo.INICIADO and otros[0].json()["pid"] > 0


def test_entrada_estandar_grande(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, _ = agente
    datos = os.urandom(3 * 1024 * 1024)  # más que el buffer de una tubería y que un marco
    salida, _, fin, _ = sesion.ejecutar(2, ["cat"], entrada=datos, timeout=30)
    assert fin["codigo"] == 0
    assert salida == datos


def test_entorno_y_variables_propias(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, _ = agente
    salida, _, _, _ = sesion.ejecutar(3, ["env"], env={"P1_PRUEBA": "sí", "LANG": "es_AR.UTF-8"})
    variables = dict(linea.split("=", 1) for linea in salida.decode().splitlines())
    assert variables["P1_PRUEBA"] == "sí"
    assert variables["LANG"] == "es_AR.UTF-8"  # las del Host reemplazan a las del agente
    assert "TERM" not in variables  # sin pty no hay terminal
    assert variables["PATH"].startswith("/usr/local/bin")


def test_programa_inexistente(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, _ = agente
    _, _, fin, otros = sesion.ejecutar(4, ["no-existe-este-programa"])
    assert fin["codigo"] == 127
    assert any(m.tipo is Tipo.ERROR and "no se encontró el programa" in m.json()["mensaje"] for m in otros)


def test_senal_al_grupo_y_terminado_con_nombre(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, _ = agente
    sesion.enviar(Tipo.EJECUTAR, 5, {"argv": ["sh", "-c", "sleep 30 & wait"]})
    assert sesion.esperar(Tipo.INICIADO, 5)
    sesion.enviar(Tipo.SENAL, 5, {"senal": "TERM"})
    fin = sesion.esperar(Tipo.TERMINADO, 5).json()
    assert fin == {"codigo": 128 + 15, "senal": "TERM"}


def test_crash_informa_segv(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, _ = agente
    _, _, fin, _ = sesion.ejecutar(6, ["sh", "-c", "kill -SEGV $$"])
    assert fin["senal"] == "SEGV"


def test_senal_desconocida(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, _ = agente
    sesion.enviar(Tipo.EJECUTAR, 7, {"argv": ["sleep", "5"]})
    sesion.esperar(Tipo.INICIADO, 7)
    sesion.enviar(Tipo.SENAL, 7, {"senal": "CUALQUIERA"})
    assert "señal desconocida" in sesion.esperar(Tipo.ERROR, 7).json()["mensaje"]
    sesion.enviar(Tipo.SENAL, 7, {"senal": "KILL"})
    assert sesion.esperar(Tipo.TERMINADO, 7).json()["senal"] == "KILL"


def test_terminal_propia_con_tamano(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, _ = agente
    sesion.enviar(Tipo.EJECUTAR, 8, {"argv": ["sh", "-c", "stty size; read linea; echo \"<$linea>\"; stty size"],
                                     "pty": True, "filas": 30, "columnas": 100})
    salida = b""
    while b"30 100" not in salida:
        salida += sesion.esperar(Tipo.SALIDA, 8).datos
    sesion.enviar(Tipo.TAMANO, 8, {"filas": 50, "columnas": 120})
    sesion.enviar(Tipo.ENTRADA, 8, b"hola\n")
    while True:
        marco = sesion.recibir()
        if marco.tipo is Tipo.SALIDA:
            salida += marco.datos
        elif marco.tipo is Tipo.TERMINADO:
            break
    assert b"<hola>" in salida
    assert b"50 120" in salida
    assert marco.json()["codigo"] == 0


def test_escribir_y_leer_archivo(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, carpeta = agente
    destino = carpeta / "sub" / "dir" / "main.c"
    contenido = os.urandom(700_000)
    sesion.enviar(Tipo.ESCRIBIR, 20, {"ruta": str(destino), "modo": 0o600})
    for marco in trocear(Tipo.DATOS, 20, contenido, bloque=100_000):
        sesion.conexion.sendall(marco)
    assert not (carpeta / "sub" / "dir" / "main.c").exists()  # todavía está como .construct-parcial
    sesion.enviar(Tipo.FIN, 20)
    assert sesion.esperar(Tipo.HECHO, 20).json() == {"bytes": len(contenido)}
    assert destino.read_bytes() == contenido
    assert destino.stat().st_mode & 0o777 == 0o600
    assert not list(carpeta.rglob("*.construct-parcial"))

    sesion.enviar(Tipo.LEER, 21, {"ruta": str(destino)})
    leido = b""
    while True:
        marco = sesion.recibir()
        if marco.tipo is Tipo.DATOS:
            leido += marco.datos
        elif marco.tipo is Tipo.FIN:
            break
    assert leido == contenido and marco.json() == {"bytes": len(contenido)}


def test_leer_inexistente(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, carpeta = agente
    sesion.enviar(Tipo.LEER, 22, {"ruta": str(carpeta / "nada.bmp")})
    assert "no se pudo leer" in sesion.esperar(Tipo.ERROR, 22).json()["mensaje"]


def test_latido_y_json_invalido(agente: tuple[Sesion, Any, Path]) -> None:
    sesion, _, _ = agente
    sesion.enviar(Tipo.LATIDO)
    assert sesion.esperar(Tipo.LATIDO_OK).id == 0
    sesion.enviar(Tipo.EJECUTAR, 30, b"{no es json")
    assert "no es un objeto JSON" in sesion.esperar(Tipo.ERROR, 30).json()["mensaje"]


def test_sin_latidos_el_agente_corta(agente: tuple[Sesion, subprocess.Popen[bytes], Path]) -> None:
    sesion, proceso, _ = agente
    # latido_s = 2: a los 6 s sin nada corta la sesión; los reintentos no reciben BIENVENIDA
    # (nadie acepta la conexión) y, pasados los 5 s de --espera-host, termina.
    assert proceso.wait(timeout=40) == 3
    with pytest.raises((ConnectionError, OSError, TimeoutError)):
        sesion.recibir(timeout=1)


def test_procesos_mueren_si_se_corta_la_sesion(agente: tuple[Sesion, subprocess.Popen[bytes], Path]) -> None:
    sesion, proceso, carpeta = agente
    marca = carpeta / "pid"
    sesion.enviar(Tipo.EJECUTAR, 40, {"argv": ["sh", "-c", f"echo $$ > {marca}; exec sleep 60"]})
    sesion.esperar(Tipo.INICIADO, 40)
    while not marca.exists() or not marca.read_text().strip():
        time.sleep(0.05)
    pid = int(marca.read_text())
    sesion.conexion.close()
    proceso.wait(timeout=40)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_token_incorrecto_no_es_problema_del_agente_sino_del_host(binario: Path, tmp_path: Path) -> None:
    """Sin BIENVENIDA (el Host rechazó el token), el agente corta a los 10 s y vuelve a intentar."""
    carpeta = Path(tempfile.mkdtemp(prefix="cagt"))
    ruta = carpeta / "ctl.sock"
    (tmp_path / "token").write_text("t", encoding="utf-8")
    servidor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    servidor.bind(str(ruta))
    servidor.listen(2)
    servidor.settimeout(15)
    proceso = subprocess.Popen([str(binario), "--conectar", f"unix:{ruta}", "--token-archivo",
                                str(tmp_path / "token"), "--sin-apagado", "--espera-host", "30"],
                               stderr=subprocess.DEVNULL)
    try:
        primera, _ = servidor.accept()
        primera.close()  # El Host rechaza: cierra sin responder.
        segunda, _ = servidor.accept()  # El agente reintenta.
        assert Decodificador().alimentar(segunda.recv(4096))[0].tipo is Tipo.HOLA
        segunda.close()
    finally:
        proceso.kill()
        proceso.wait()
        servidor.close()
        shutil.rmtree(carpeta, ignore_errors=True)
