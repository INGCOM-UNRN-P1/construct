"""Procesos hijos que no sobreviven a construct: QEMU nunca queda huérfano (PLAN §1.3).

- Linux: `PR_SET_PDEATHSIG`, el kernel le manda SIGTERM al hijo cuando muere construct.
- Windows: el hijo entra en un *Job Object* con `KILL_ON_JOB_CLOSE`; al cerrarse el último handle
  del job (cuando termina construct, aunque sea por la fuerza), Windows mata al hijo.
- macOS: no hay mecanismo del kernel; lo cubren el pidfile de la sesión y el watchdog del agente
  Guest, que apaga la VM al perder el canal de control.

El hijo arranca en su propio grupo de procesos, así un Ctrl+C en la terminal no le llega directo:
construct decide cómo apagarlo (por QMP, de forma ordenada).
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

_ES_WINDOWS = sys.platform == "win32"
_ES_LINUX = sys.platform.startswith("linux")

MUERE_CON_EL_PADRE = _ES_WINDOWS or _ES_LINUX
"""Si el sistema garantiza que el hijo muere cuando muere construct."""

PR_SET_PDEATHSIG = 1

# Windows: constantes de la API de Job Objects y de procesos.
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASE = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
CREATE_NEW_PROCESS_GROUP = 0x00000200

_jobs: dict[int, Any] = {}
"""Handles de los Job Objects por PID: viven mientras viva construct."""


def _pdeathsig() -> None:  # pragma: no cover - corre en el hijo, entre fork y exec
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)


def _kernel32() -> Any:
    from ctypes import wintypes

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009 - sólo existe en Windows
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _asignar_a_job(proceso: subprocess.Popen[Any]) -> None:
    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(nombre, ctypes.c_uint64) for nombre in ("ReadOperationCount", "WriteOperationCount",
                                                              "OtherOperationCount", "ReadTransferCount",
                                                              "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASE,
                                            ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    if not kernel32.AssignProcessToJobObject(job, int(proceso._handle)):  # type: ignore[attr-defined]
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    _jobs[proceso.pid] = job


def lanzar_vinculado(
    argumentos: Sequence[str | os.PathLike[str]],
    *,
    stdin: int | IO[Any] | None = subprocess.DEVNULL,
    stdout: int | IO[Any] | None = None,
    stderr: int | IO[Any] | None = None,
    cwd: Path | None = None,
) -> subprocess.Popen[bytes]:
    """Lanza un proceso que muere con construct (donde el sistema lo permite) y no recibe su Ctrl+C."""
    if _ES_WINDOWS:
        proceso = subprocess.Popen(list(argumentos), stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd,
                                   creationflags=CREATE_NEW_PROCESS_GROUP)
        try:
            _asignar_a_job(proceso)
        except OSError:
            proceso.kill()
            raise
        return proceso
    # preexec_fn sólo llama a prctl; QEMU se lanza desde el hilo principal, sin otros hilos en fork.
    return subprocess.Popen(list(argumentos), stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd,
                            start_new_session=True,
                            preexec_fn=_pdeathsig if _ES_LINUX else None)  # noqa: PLW1509


def proceso_vivo(pid: int) -> bool:
    """Si existe un proceso con ese PID (para validar un pidfile de una sesión anterior)."""
    if pid <= 0:
        return False
    if _ES_WINDOWS:
        from ctypes import wintypes

        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            codigo = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(codigo))) and codigo.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Existe, pero es de otro usuario.
    return True


def terminar(pid: int) -> None:
    """Termina el proceso por la fuerza (último recurso, después de pedir el apagado por QMP)."""
    if _ES_WINDOWS:
        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
