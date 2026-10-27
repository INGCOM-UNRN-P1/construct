# Plan de Implementación Multiplataforma: `construct`

**Versión:** 2.0.0
**Fecha:** 26/10/2026
**Documento base:** [`SPEC.md`](SPEC.md) (v5.0.0-unrn-p1-ide-vm)
**Reemplaza a:** Plan v1.0.0 (21/10/2026), cuyo contenido de fases se conserva e integra acá.
**Hosts objetivo:** Windows 10/11 (x86_64), macOS 13+ (Intel y Apple Silicon), Linux (Debian/Ubuntu, Fedora, Arch y derivadas).

---

## 0. Resumen Ejecutivo

La SPEC ya resuelve la mitad del problema de portabilidad: todo lo que es "Linux de verdad" (`nostromo` con `bwrap`, `cgroups v2`, `/proc`, `gdbserver`, `daedalus`, ASan) vive **dentro del Guest Alpine**, que es idéntico en las tres plataformas. Lo que hay que diseñar con cuidado es **el Host**: el proceso Python que lanza QEMU, se comunica con la VM, sincroniza archivos, maneja la terminal y abre visores.

La regla de diseño de este plan es:

> **Todo lo que dependa del sistema operativo del Host se concentra en un único paquete `construct.platform`. El resto del código (`core`, `ui`) nunca consulta `sys.platform` ni invoca binarios del Host directamente.**

Además, para minimizar lo que el alumno tiene que instalar, el Host sólo necesita **QEMU** y **Git** (más `uv`). GDB, compiladores y herramientas del perfil `estudiante` **no se instalan nunca en el Host**.

---

## 1. Análisis de Portabilidad de la SPEC

### 1.1. Qué cambia entre plataformas

| Aspecto | Linux | Windows | macOS Intel | macOS Apple Silicon |
| :--- | :--- | :--- | :--- | :--- |
| Acelerador QEMU | KVM (`/dev/kvm`) | WHPX (Plataforma del hipervisor de Windows) | HVF | HVF **sólo para guests `aarch64`** |
| Instalación de QEMU | Gestor de paquetes de la distro | `winget` / instalador oficial | Homebrew | Homebrew |
| Sockets Unix en Python/asyncio | Sí | **No** (`asyncio` no soporta `AF_UNIX` en Windows) | Sí | Sí |
| Carpetas compartidas (9p / virtiofs) | Sí | **No** | Parcial (9p) | Parcial (9p) |
| Terminal raw (`termios`) | Sí | **No** (API de consola Win32) | Sí | Sí |
| GDB nativo usable | Sí | Raro | Requiere firma de código | No soporta `arm64` |
| Sistema de archivos | Sensible a mayúsculas | Insensible, nombres reservados (`aux.c`, `con.h`), `MAX_PATH` | Insensible, normaliza Unicode a NFD | Ídem |
| Fin de línea por defecto | LF | CRLF (Git con `autocrlf`) | LF | LF |
| Reproducción de audio / abrir archivos | `pw-play`/`paplay`/`aplay`, `xdg-open` | `winsound`, `os.startfile` | `afplay`, `open` | `afplay`, `open` |
| Terminación de procesos | Señales POSIX | Sin `SIGTERM`; Job Objects | Señales POSIX | Señales POSIX |

### 1.2. Conflictos de la SPEC con la portabilidad (requieren decisión o enmienda)

| ID | Conflicto | Impacto | Decisión recomendada |
| :--- | :--- | :--- | :--- |
| **D1** | La SPEC exige `x86_64` con HVF en macOS, pero **HVF no acelera guests `x86_64` en Apple Silicon** (la gran mayoría de las Mac actuales). Lo mismo pasa con Windows on ARM. | Sin aceleración, QEMU usa TCG (emulación): arranque y compilación varias veces más lentos, ASan muy pesado. | **Imagen dual `x86_64` + `aarch64`** generada desde el mismo `Dockerfile`. En hosts ARM se usa el guest `aarch64` con HVF. `x86_64` + TCG queda como "modo compatibilidad" (lento, pero exacto). Enmendar SPEC §"Arquitectura Target". |
| **D2** | La SPEC ubica `GDBClient` (`pygdbmi`) en el Host conectando a `gdbserver`. Eso obliga a tener un GDB con soporte `x86_64` en el Host. | No hay GDB razonable en Windows ni en Apple Silicon. | **Perfil estudiante:** GDB corre dentro del Guest; su salida GDB/MI viaja por el canal de control y en el Host sólo se usa el *parser* de `pygdbmi`. **Modo desarrollo:** `gdbserver` en el Guest, tunelizado a un puerto *loopback* del Host, para usar un GDB local (`gdb-multiarch`) vía `pygdbmi` o a mano (ver §3.4). |
| **D3** | La SPEC habla de "Socket" para `construct-tlm` y GDB. En Windows no hay sockets Unix utilizables desde `asyncio`. | Código de transporte divergente por plataforma. | **Un único transporte TCP sobre `127.0.0.1`** para todos los canales, con multiplexado y token de autenticación (ver §3.2). |
| **D4** | Los atajos `Ctrl+.` (autofix) y `F9`–`F11` no son confiables: `Ctrl+.` no tiene código de control y la mayoría de las terminales no lo transmiten; macOS reserva `F11`/`F10` para el sistema. | Funciones normativas inaccesibles en algunas terminales. | **Keymap con alternativas** declarado en `project.toml` y paleta de comandos (`Ctrl+P`) como acceso universal. Los atajos de la SPEC se mantienen como primarios donde funcionen. |
| **D5** | `VirtIO-Blk (workspace.qcow2)` como transporte del código: el Host no puede montar un `qcow2` de forma portable. | — | Se mantiene la SPEC: `workspace.qcow2` es **sólo** el disco persistente del Guest. Se descarta 9p/virtiofs por no existir en Windows. **La sincronización es responsabilidad de un comando dedicado, `construct sync`** (Git por bundles + artefactos por el canal de control, ver §3.3); ningún otro componente mueve archivos entre Host y Guest por su cuenta. |

**Estado de las decisiones (26/10/2026):**

| ID | Estado | Observaciones |
| :--- | :--- | :--- |
| D1 | Aprobada | Imagen dual `x86_64` + `aarch64`; TCG como modo compatibilidad. |
| D2 | Aprobada con matiz | GDB en el Guest para estudiantes; GDB en el Host habilitado en modo desarrollo. |
| D3 | Aprobada | — |
| D4 | Aprobada | — |
| D5 | Aprobada | Con comando dedicado `construct sync`. |

> La **Fase 0** ya no decide estas cuestiones: confirma su viabilidad técnica (tiempos, flags, versión de QEMU) en hardware real.

### 1.3. Riesgos técnicos y mitigaciones

| Riesgo | Plataforma | Mitigación |
| :--- | :--- | :--- |
| WHPX deshabilitado (requiere activar la característica opcional y reiniciar) | Windows | `construct doctor` lo detecta vía `WinHvPlatform.dll` y muestra el comando exacto (`Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform`) más el aviso de activar VT-x/AMD-V en BIOS. |
| Flags de QEMU que cambian entre versiones (`reconnect` → `reconnect-ms`, opciones de `-accel whpx`) | Todas | Versión mínima de QEMU fijada (a definir en Fase 0, p. ej. ≥ 8.2) y `QemuCommandBuilder` que arma los argumentos según versión y acelerador detectados. |
| Usuario sin permiso sobre `/dev/kvm` | Linux | `doctor` prueba abrir `/dev/kvm` y sugiere `usermod -aG kvm $USER` (o regla `udev`) según la distro. |
| Python `x86_64` corriendo bajo Rosetta reporta arquitectura falsa | macOS | Detección de arquitectura real con `sysctl.proc_translated` y `hw.optional.arm64`, no con `platform.machine()`. |
| QEMU con rutas no ASCII (`C:\Users\Martín\...`) o con espacios | Windows | Pruebas explícitas con usuarios con tildes en la matriz manual; si falla, usar la ruta corta 8.3 (`GetShortPathNameW`) para imágenes y discos. |
| `construct` dentro de WSL2 (virtualización anidada) | Windows | `doctor` detecta WSL y recomienda ejecutar `construct` nativo en Windows; si existe `/dev/kvm` en WSL, se permite. |
| Proceso QEMU huérfano si `construct` muere | Todas | Pidfile + `filelock` en el directorio de estado; en Windows, QEMU se asigna a un *Job Object* con `KILL_ON_JOB_CLOSE`; en Linux, `PR_SET_PDEATHSIG`; además el agente Guest se apaga si pierde el canal de control por más de N segundos. |
| Colisión de nombres por mayúsculas o Unicode (`Main.c` vs `main.c`, `función.c` NFD vs NFC) | Windows / macOS | `SyncManager` normaliza a NFC y rechaza, con mensaje en español, nombres que colisionen o sean reservados en Windows. |
| CRLF introducido por Git en Windows altera columnas de `hallazgos.json` y reglas de `gaff` | Windows | El repositorio de trabajo se crea con `.gitattributes` (`* text=auto eol=lf`) y `core.autocrlf=false` a nivel de repo; el editor siempre guarda en LF. |
| Consola Windows en `cp1252`/`cp850` rompe tildes y `ñ` | Windows | El entrypoint fuerza UTF-8 en `stdout`/`stderr` y habilita VT en la consola; se recomienda Windows Terminal. |
| Terminales sin TrueColor (conhost clásico, Terminal.app) | Windows / macOS | `media_preview` detecta capacidades y degrada a 256 colores o abre el visor del Host. |
| Colisión de interposición de memoria (`libconstruct_preload.so` vs `libasan`) | Guest | En modo `tetsuo`, el agente no carga `libconstruct_preload.so` y usa el reporte de ASan/UBSan (sin cambios respecto a v1). |
| Conflictos de concurrencia Git (`index.lock`) | Todas | Sincronización serializada en una cola única del `SyncManager` (sin cambios respecto a v1). |

---

## 2. Arquitectura del Host: Capa de Plataforma

Se agrega el paquete `construct.platform` a la estructura de la SPEC §7 (enmienda menor):

```text
src/construct/
├── platform/
│   ├── __init__.py        # Detecta el Host una sola vez y expone `HostPlatform` (Linux/Windows/macOS)
│   ├── host_info.py       # SO, arquitectura real (anti-Rosetta), WSL, RAM y CPUs disponibles
│   ├── accel.py           # Detección de KVM / WHPX / HVF / fallback TCG y sus flags de QEMU
│   ├── binarios.py        # Busca qemu-system-*, git y gdb: PATH, Program Files, Homebrew, versión mínima
│   ├── paths.py           # Directorios de datos/caché/estado vía `platformdirs`; rutas cortas en Windows
│   ├── process.py         # Lanzamiento y terminación robusta de QEMU (Job Objects / PDEATHSIG / pidfile)
│   ├── terminal.py        # Modo raw + resize: `termios`/`SIGWINCH` en POSIX, API de consola Win32 en Windows
│   ├── opener.py          # Abrir archivos y reproducir .wav con la herramienta nativa del Host
│   └── installer_hints.py # Instrucciones de instalación por SO/distro para `doctor` y `setup`
```

### 2.1. Detección de aceleración (`accel.py`)

| Host | Prueba | Resultado | Flags QEMU (punto de partida, se validan en Fase 0) |
| :--- | :--- | :--- | :--- |
| Linux | Abrir `/dev/kvm` en lectura/escritura + `ioctl(KVM_GET_API_VERSION)` | `kvm` | `-accel kvm -cpu host` |
| Windows | `ctypes` → `WinHvPlatform.dll` → `WHvGetCapability(HypervisorPresent)` | `whpx` | `-accel whpx,kernel-irqchip=off -cpu max` |
| macOS Intel | `sysctl kern.hv_support == 1` | `hvf` | `-accel hvf -cpu host` |
| macOS ARM | `hw.optional.arm64 == 1` | `hvf` con guest `aarch64` | `qemu-system-aarch64 -M virt -accel hvf -cpu host` |
| Cualquier otro caso | — | `tcg` | `-accel tcg` + aviso de "modo compatibilidad" en la barra de estado |

`project.toml` permite forzar el acelerador y la arquitectura del guest (`[vm] accel = "auto" | "kvm" | ...`, `arch = "auto" | "x86_64" | "aarch64"`), útil para CI y para depurar.

### 2.2. Directorios del Host (`paths.py`)

Se usa `platformdirs` para no hardcodear rutas:

| Uso | Linux | Windows | macOS |
| :--- | :--- | :--- | :--- |
| Imágenes base (solo lectura) | `~/.local/share/construct/images` | `%LOCALAPPDATA%\construct\images` | `~/Library/Application Support/construct/images` |
| Discos de trabajo (`workspace.qcow2`, overlays) | `~/.local/share/construct/vms` | `%LOCALAPPDATA%\construct\vms` | `~/Library/Application Support/construct/vms` |
| Estado de sesión (pidfile, puerto, token, locks) | `$XDG_STATE_HOME/construct` | `%LOCALAPPDATA%\construct\state` | `~/Library/Application Support/construct/state` |
| Logs | `$XDG_STATE_HOME/construct/log` | `%LOCALAPPDATA%\construct\log` | `~/Library/Logs/construct` |

El disco del sistema del Guest es un **overlay `qcow2` con `backing_file` relativo** sobre la imagen base, así una actualización de la imagen no destruye `workspace.qcow2` y no quedan rutas absolutas del Host (con `\` de Windows) embebidas en los discos.

### 2.3. Dependencias del Host

* **Obligatorias:** `uv` (Python ≥ 3.12), QEMU (versión mínima de la Fase 0), Git ≥ 2.30.
* **Python (`pyproject.toml`):** ecosistema vía `git+https://github.com/INGCOM-UNRN-P1/...` (RF-01: `yutani`); terceros mínimos y portables: `textual`, `typer` (vía `yutani`), `pygdbmi` (sólo el parser), `platformdirs`, `filelock`, `psutil`. Todas tienen *wheels* para las tres plataformas; no se admite ninguna dependencia que requiera compilar en el Host.
* **Prohibidas en el Host del estudiante:** GDB, gcc/clang, Docker, WSL, sockets Unix, `pty`/`termios` fuera de `platform/terminal.py`.
* **Opcionales en la máquina de desarrollo:** `gdb-multiarch` (o GDB con soporte de la arquitectura del Guest), usado sólo en modo desarrollo (§3.4). `construct doctor --dev` lo verifica; sin `--dev`, su ausencia no es un error.

---

## 3. Arquitectura de Comunicación Host ↔ Guest

### 3.1. Topología

```
 Host (construct, Python asyncio)                         Guest (Alpine)
 ┌───────────────────────────────┐                        ┌──────────────────────────────┐
 │ ChannelServer                 │  TCP 127.0.0.1:<efím.> │ construct_daemon             │
 │  escucha en puerto 0 ◄────────┼──── QEMU chardev ──────┼─ /dev/virtio-ports/         │
 │  (acepta UNA conexión)        │   socket (cliente)     │    construct-ctl             │
 │                               │                        │                              │
 │ Multiplexor de canales:       │                        │ Despachador:                 │
 │  ctl  │ pty │ tlm │ mi │ file │                        │  herramientas, pty (jonesy), │
 └───────────────────────────────┘                        │  telemetría, gdb --interp=mi │
 QMP (control de QEMU): TCP 127.0.0.1:<efím.>             └──────────────────────────────┘
```

* **El Host escucha y QEMU se conecta como cliente** (`-chardev socket,host=127.0.0.1,port=N`): el Host pide el puerto `0` al sistema operativo, por lo que no hay carrera por puertos libres ni diálogos del firewall de Windows (sólo *loopback*).
* **QMP** también por TCP en *loopback*, para apagar la VM de forma ordenada en Windows (donde no existen señales POSIX) y en el resto.
* No se usa red de usuario (`slirp`) salvo que la cátedra lo pida para `yutani.ia` con proveedores remotos; en ese caso, el Guest no necesita red porque **la consulta a `yutani.ia` se hace desde el Host** (ver Fase 4).

### 3.2. Protocolo `construct-ctl`

* **Marco:** `[tipo:u8][id:u32 BE][largo:u32 BE][datos]`; datos JSON UTF-8 para los mensajes de control y binarios para los flujos. Especificación completa en [`docs/protocolo.md`](docs/protocolo.md).
* **Handshake:** el Host genera un token aleatorio y lo inyecta con `-fw_cfg name=opt/construct/token,string=<token>`; el agente lo lee de `/sys/firmware/qemu_fw_cfg` y lo presenta junto con su versión de protocolo. Una conexión sin token válido se descarta. Esto evita que otro proceso local se haga pasar por la VM.
* **Versionado:** `protocol_version` en el handshake; el Host rechaza imágenes incompatibles y sugiere `construct setup --actualizar-imagen`.
* **Multiplexado por `id`** (en lugar de canales fijos): cada proceso (`EJECUTAR`, con tuberías o con `pty`) y cada transferencia (`ESCRIBIR`/`LEER`) lleva su propio `id`. Las herramientas, el programa del alumno, `jonesy` y `gdb --interpreter=mi` son procesos; los bundles Git y los artefactos, transferencias. La telemetría (RF-06/07) se suma en la Fase 3 como un tipo de mensaje más.
* **Interrupción:** `Ctrl+C` en el Host nunca se traduce a una señal local: se envía un mensaje `signal{SIGINT}` por `ctl` y el agente lo entrega al proceso confinado. Mismo comportamiento en las tres plataformas.

### 3.3. Sincronización de código (RF-08): comando dedicado `construct sync`

Toda transferencia entre Host y Guest pasa por `SyncManager`, y su única puerta de entrada pública es `construct sync`:

| Comando | Acción |
| :--- | :--- |
| `construct sync` | Sincronización completa en ambos sentidos: sube, baja, fusiona (3 vías) y descarga artefactos. |
| `construct sync subir` | Sólo Host → Guest. Nunca fusiona: si el Guest tiene cambios propios, aborta y pide `construct sync`. |
| `construct sync bajar` | Sólo Guest → Host, con fusión de 3 vías; ante conflictos marca los archivos y explica cómo resolverlos (en la TUI, abre `sync_conflict_modal`). |
| `construct sync artefactos` | Descarga `.bmp`, `.wav`, `.html`/`.mmd` y `reproducer.sh` pendientes. |
| `construct sync estado` | Muestra divergencia Host/Guest y artefactos pendientes (`--json` disponible). |

* `build`, `check`, `test` y `run` invocan internamente **sólo** el equivalente a `sync subir` (para no compilar código viejo) y, al terminar, `sync artefactos`. Nunca fusionan ni resuelven conflictos: eso queda reservado a `construct sync`. Se puede desactivar con `[sync] automatico = false` en `project.toml`.
* En la TUI, `Ctrl+S` ejecuta exactamente `construct sync` a través del mismo `SyncManager`.

Mecanismo interno:

1. El Host mantiene el repositorio del alumno (Git del Host). El Guest mantiene un clon en `/workspace` sobre `workspace.qcow2`.
2. **Host → Guest:** `git bundle create` de los commits nuevos (incluido un commit automático de trabajo en curso en una rama interna `construct/wip`) → canal `file` → `git fetch` desde el bundle en el Guest.
3. **Guest → Host:** sólo si hubo cambios del lado Guest (p. ej. `gaff --autofix`): bundle inverso y `git merge` de 3 vías en el Host; si hay conflictos, se abre `sync_conflict_modal`.
4. **Artefactos fuera del repo:** el agente lista archivos nuevos por extensión permitida tras cada ejecución y los envía por `file` a `<proyecto>/.construct/artefactos/`.
5. **Normalización en el Host antes de cada sync:** NFC, rechazo de nombres reservados de Windows y de colisiones por mayúsculas, LF.

Este esquema es idéntico en las tres plataformas porque sólo depende de `git` y del canal TCP.

### 3.4. Modo desarrollo: GDB en el Host

Pensado para quienes desarrollan `construct` y los satélites, no para estudiantes. Se activa con `construct vm start --dev` o `[dev] habilitado = true` en `project.toml`.

* El agente lanza `gdbserver` en el Guest y el Host abre un puerto `127.0.0.1:<efímero>` que reenvía el protocolo remoto de GDB (RSP) por un canal `rsp` adicional del multiplexor. No hace falta red de usuario (`slirp`) en la VM.
* `construct dev gdb <binario>` descarga del Guest el binario y un *sysroot* mínimo (musl y las bibliotecas enlazadas) a la caché, y lanza el GDB local con `set sysroot` y `target remote` ya configurados. También imprime el puerto para conectar a mano desde un IDE o editor externo.
* `core/gdb_client.py` define una interfaz con dos *backends*: `GuestGdbBackend` (por defecto; MI por el canal `mi`) y `HostGdbBackend` (`pygdbmi` lanzando GDB local contra el puerto tunelizado). Ambos emiten los mismos eventos hacia la UI y `bishop_view`, así que el modo desarrollo también sirve para probar el camino de `pygdbmi` completo.
* Opcional para depurar el kernel o el agente desde el arranque: `--dev-qemu-gdb` agrega `-gdb tcp:127.0.0.1:<puerto>` a QEMU.

---

## 4. Fases de Implementación

### Fase 0: Prueba de Concepto de Portabilidad (bloqueante)

* **Objetivo:** Confirmar en hardware real la viabilidad técnica de las decisiones aprobadas D1–D5 antes de construir el producto.
* **Tareas:**
  1. Imagen Alpine mínima (`x86_64` y `aarch64`) con un agente "eco" que responda por `virtio-serial`.
  2. Script de spike en Python que lance QEMU, se conecte por el esquema TCP de §3.1 y mida tiempos.
  3. Ejecutarlo en: Linux con KVM, Windows 11 con WHPX, Windows 10 Home con WHPX, Mac Intel con HVF, Mac Apple Silicon con HVF (`aarch64`) y con TCG (`x86_64`).
  4. Medir: tiempo de arranque hasta handshake, `gcc -fsanitize=address` de un programa de práctica, consumo de RAM del Host.
  5. Fijar versión mínima de QEMU y la tabla final de flags por acelerador.
  6. Probar rutas con espacios y tildes en Windows.
  7. Probar el túnel RSP del modo desarrollo con `gdb-multiarch` en Linux y macOS.
* **Entregable:** `docs/fase0_resultados.md` con la matriz de resultados, y la enmienda a la SPEC (§6) aplicada.
* **Criterio de salida:** handshake < 3 s con aceleración en las cinco configuraciones aceleradas; documentado el tiempo en TCG.

### Fase 1: Infraestructura Base, Capa de Plataforma, Contrato CLI y Diagnóstico

* **Tareas:**
  1. `pyproject.toml` con `uv` (`hatchling`), `yutani @ git+https://...` fijado por *tag*/commit (RF-01).
  2. Paquete `construct.platform` completo (§2), con cada módulo detrás de una interfaz y una implementación por SO; las implementaciones se seleccionan en `platform/__init__.py`.
  3. `cli.py` sobre `TyperConErrores` con `ui`, `jonesy`, `doctor`, `setup`, `vm` (`start`/`stop`/`status`), `sync` (con los subcomandos de §3.3), `build`, `check`, `test`, `run` y el grupo `dev` (oculto en `--help` salvo con modo desarrollo activo). `-h`/`--help` en español y `-v`/`--version` (RF-02). Forzar UTF-8 en `stdout`/`stderr` al inicio.
  4. `core/doctor.py` con checks del Host: SO y arquitectura real, QEMU encontrado y versión, acelerador disponible y con permisos, Git, imagen descargada y con `sha256` válido, espacio en disco, RAM libre, WSL/virtualización anidada. Cuando la VM está arriba, encadena `mother doctor --json` en el Guest. Salida `{"schema_version": "1.0", "estado": ..., "checks": [...]}`; cada check fallido trae la **instrucción de remediación específica del SO/distro** (`installer_hints.py`).
  5. `construct setup`: descarga la imagen correspondiente a la arquitectura desde un manifiesto publicado (`imagenes.json` en el release de GitHub, con `sha256` por archivo y versión de protocolo; ver `core/imagenes.py`) y, si falta QEMU, imprime el comando exacto (`winget install`, `brew install qemu`, `apt`/`dnf`/`pacman`). No instala nada con privilegios por su cuenta. Los discos de trabajo se crean en la Fase 3, junto con `vm_manager`.
  6. `tests/test_cli_contract.py` y `tests/platform/` con pruebas unitarias de cada implementación usando *fakes* (p. ej. simular `/dev/kvm` ausente, `WinHvPlatform.dll` inexistente, Rosetta).
* **Checkpoint:** `uv run pytest` en verde en Linux, Windows y macOS (CI, §5); `construct doctor --json` válido en las tres.
* **Estado (26/10/2026):** implementada. Verificada en Linux (Fedora 44, KVM); el CI pasa en Linux, Windows y macOS desde el push del 27/10/2026. El encadenado con `mother doctor --json` del Guest se hizo en la Fase 3.

### Fase 2: Imagen de Máquina Virtual Multiarquitectura y Agente Guest

* **Tareas:**
  1. `vm_image/Dockerfile.alpine` multiarquitectura (`--platform linux/amd64,linux/arm64`): paquetes base, `gdb`, `git`, `bubblewrap`, `mother instalar --perfil estudiante`, `treadstone`, `bitmaps`, `sonido`. Verificar con cada dueño de satélite que sus paquetes compilen en `aarch64` (especialmente `jonesy` en Rust y `bishop`). `jonesy` no publica binario `aarch64`: en esa arquitectura se compila desde el tag.
  2. OpenRC con `rc_cgroup_mode="unified"` para `cgroups v2` (RF-04/RF-06).
  3. `vm_image/build_vm.sh`: genera kernel + `rootfs.qcow2` por arquitectura. **Herramienta exclusiva de mantenedores, sólo corre en Linux/CI**; los alumnos nunca construyen la imagen.
  4. `guest_agent/construct_daemon.c` (enlazado estático con musl): protocolo de §3.2, ejecución genérica de procesos (el Host arma el `argv`, incluido el envoltorio `nostromo`, así el agente no conoce las herramientas), PTY con `forkpty` + mensajes de resize, transferencia de archivos y watchdog de canal. GDB/MI no necesita nada propio: es un proceso más.
  5. `guest_agent/libconstruct_preload.c`: pasa a la Fase 3, junto con `telemetry.py`, que es quien consume sus datos.
  6. Pipeline de CI que publica las imágenes como *release assets* junto con el manifiesto `imagenes.json` que consume `construct setup` (esquema en `core/imagenes.py`).
* **Checkpoint:** ambas imágenes arrancan con el agente completo y pasan `mother doctor --json` dentro del Guest.
* **Cambio (26/10/2026):** el Guest pasa de Alpine a **Debian trixie slim con systemd** (`vm_image/Dockerfile.debian`): el gcc de Alpine (musl) no trae libasan ni libubsan, sin los que no funcionan `tetsuo` ni el paso ASan de `ripley` (RF-04). Las tareas 1, 2 y 4 se leen con ese reemplazo: `cgroups v2` sin configuración (systemd), agente compilado con glibc y unidades de systemd en lugar de servicios de OpenRC. Con Debian el handshake tarda 3,96 s en Fedora 44 con KVM.
* **Cambio (27/10/2026):** la VM se comporta como el [entorno de la cátedra](../entorno) en Linux: la imagen trae `INGCOM-UNRN-P1/entorno` fijado a un commit (`ENTORNO_REF`) en `/opt/p1/entorno`, con su HOME portable apuntando al de `estudiante`. Su propio `doctor --fix` arma la configuración inicial: `.bashrc` con la bienvenida y `ayuda`, `.bash_profile` y el `.gdbinit` pedagógico. `/etc/profile.d/p1-entorno.sh` hace en cada shell de login lo mismo que `source linux/activate.sh`. Se entra con `construct entrar` (Fase 5), y al salir vuelven también los archivos de texto creados allá. Se quita su lanzador de `ripley`, que se encuentra a sí mismo en el PATH y nunca llega al de `mother` (bug del entorno, a reportar).
* **Estado (26/10/2026):** implementada. La imagen `x86_64` se construyó y arrancó en Fedora 44 con KVM: handshake a los 3,15 s (con Alpine), perfil estudiante completo (`mother doctor` en verde), librerías C enlazando y cgroups v2 (`tests/e2e/test_arranque.py`, 21 chequeos). La `aarch64` la construye y prueba el workflow `imagenes.yml` en un runner ARM nativo; todavía no corrió. Pendientes para la cátedra: las herramientas se instalan desde la rama principal de cada repo hasta que `ecosistema.toml` fije la matriz del cuatrimestre, y `bitmaps` y `sonido` llaman igual (`libreria.h`) a su cabecera, así que van en `/usr/local/include/bitmaps/` y `/usr/local/include/sonido/`.

### Fase 3: Orquestación QEMU, Puentes de Herramientas y Sincronización

* **Tareas:**
  1. `core/vm_manager.py`: usa `platform.accel`, `platform.binarios` y `platform.process`; arma la línea de comandos con `QemuCommandBuilder`; ciclo de vida por QMP. **Sesión persistente**: la VM queda viva entre comandos CLI (`construct build`, `check`, ...) y se apaga tras un tiempo de inactividad configurable, para no pagar el arranque en cada invocación. Recursos por defecto conservadores (p. ej. 2 vCPU, 1 GB) y ajustables en `project.toml`.
  2. `core/channel.py` (nuevo): servidor TCP, handshake con token, multiplexor de canales sobre `asyncio` (funciona igual con el *event loop* Proactor de Windows).
  3. `core/tool_bridge.py`: adaptadores de `daedalus`, `ripley --json`, `gaff --autofix`, `tetsuo`, `hal`, `bishop`, `treadstone`. Traducción de rutas del Guest (`/workspace/...`) a rutas del Host (`pathlib`) en cada hallazgo.
  4. `core/sync_manager.py`: esquema de bundles de §3.3 con cola serializada y normalización de nombres; expuesto por `construct sync` y usado por el resto de los comandos sólo en modo `subir`/`artefactos`.
  5. `core/gdb_client.py`: interfaz común con `GuestGdbBackend` (MI por el canal `mi`, parseado con `pygdbmi.gdbmiparser`) y `HostGdbBackend` para modo desarrollo (§3.4); breakpoints, step over/into y lectura de frames para `bishop`. Comando `construct dev gdb`.
  6. `core/telemetry.py`: consumo del canal `tlm` y emisión de anomalías como `hallazgos` (RF-06/RF-07).
  7. `core/events.py`: comandos y eventos inmutables entre core y UI.
* **Checkpoint:** desde la CLI, en las tres plataformas, `construct build` y `construct check` devuelven hallazgos con rutas del Host correctas; `construct run` ejecuta un programa interactivo con `Ctrl+C` funcional; `construct sync` resuelve un conflicto de prueba; `construct dev gdb` funciona en la máquina de desarrollo.
* **Estado (26/10/2026):** implementada. Verificada en Linux (Fedora 44, KVM) contra la imagen Debian: `build`, `check`, `run` (con telemetría, `--aislado`, `--tetsuo` y post-mortem de `hal`) y `construct dev gdb` con el GDB del Host (`fix`, `test` y `sync` se habían probado contra la imagen Alpine); `construct doctor` encadena `mother doctor --json` del Guest. El orquestador de la interfaz (`core/orquestador.py`) atiende los comandos de `core/events.py`. Falta el checkpoint en Windows y macOS. Pendiente de los satélites: bajo el sandbox de `nostromo` se pierde el informe de ASan, así que `tetsuo` no trae el detalle de los sanitizers (construct lo informa como falla sin detalle).

### Fase 4: Frontend TUI en Textual

* **Tareas:**
  1. `ui/app.py`: layout de 4 cuadrantes (SPEC §5) y barra de estado que muestra el acelerador real (`KVM`/`WHPX`/`HVF`/`TCG — modo compatibilidad`).
  2. **Keymap portable** (`ui/keymap.py`, nuevo): atajos de la SPEC como primarios y alternativas para terminales que no los transmiten; p. ej. autofix `Ctrl+.` con alternativa `Alt+Enter`; `F9`–`F11` con alternativas `Ctrl+B`/`Ctrl+N`/`Ctrl+O` (`Ctrl+I` se descartó: la terminal lo manda igual que Tab). Paleta de comandos `Ctrl+P` con todas las acciones. Sobrescribible en `project.toml`.
  3. Widgets de la SPEC §7: `code_editor` (gutter de `hallazgos.json`, guardado siempre en LF y UTF-8), `bishop_view`, `treadstone_view`, `hal_postmortem_modal`, `resource_monitor`, `sync_conflict_modal`.
  4. `media_preview`: BMP en half-block TrueColor con degradación a 256 colores según capacidades de la terminal; WAV con forma de onda y reproducción vía `platform.opener` (`winsound` / `afplay` / `pw-play`→`paplay`→`aplay`).
  5. "Explicar este hallazgo" (RF-09) llamando a `yutani.ia` **desde el Host** (así funciona con `ollama`/`llama.cpp` local del alumno sin dar red al Guest), enviando sólo la línea afectada y el objeto del hallazgo.
* **Checkpoint:** recorrido completo (editar → `F7` → `F8` → `F6` → `F5` → crash con modal de `hal`) en Windows Terminal, conhost, Terminal.app, iTerm2, GNOME Terminal y Konsole, con todas las acciones alcanzables por atajo o paleta.
* **Estado (26/10/2026):** implementada. Además de lo pedido: depuración con el GDB del Guest desde el orquestador (breakpoints, pasos, variables y pila), foto de `bishop` en la línea del cursor (`F12`), «Guardar reproductor» de `hal`, resolución de conflictos por archivo en el `SyncManager` y bloqueo de la ejecución ante funciones prohibidas de `kaneda`. Verificada con tests *headless* de Textual y manejando la app contra la VM Debian con KVM (auditoría, ejecución interactiva con telemetría, BMP, depuración y `bishop`). **Falta el checkpoint en terminales reales**: no se probó todavía en ninguna de las seis terminales de la lista, así que las alternativas del keymap siguen sin confirmar.

### Fase 5: Modo Passthrough `construct jonesy` (RF-05)

* **Tareas:**
  1. Subcomando independiente de Textual: abre un canal `pty` y conecta la terminal del Host en modo raw usando `platform.terminal`.
  2. POSIX: `termios`/`tty.setraw`, `SIGWINCH` → mensaje de resize.
  3. Windows: `SetConsoleMode` con `ENABLE_VIRTUAL_TERMINAL_INPUT`/`ENABLE_VIRTUAL_TERMINAL_PROCESSING` vía `ctypes`, lectura de entrada en hilo dedicado y detección de resize por sondeo de `shutil.get_terminal_size`.
  4. Restauración garantizada del modo de la terminal ante cualquier salida (incluida la caída de la VM).
  5. `construct entrar [comando]`: la terminal del entorno de la cátedra en la VM, como `~/p1/entrar` (agregado el 27/10/2026, ver Fase 2).
* **Checkpoint:** `jonesy` usable y redimensionable en las terminales de la Fase 4.
* **Estado (26/10/2026):** implementada. Además de lo pedido, al salir de `jonesy` se traen los cambios hechos en la VM (`sync bajar`, con fusión de 3 vías). Verificada con tests (fakes de `termios` y de la consola de Windows, y una pty contra el agente real) y contra la VM Debian dentro de una pty de Linux: tamaño inicial, redimensionado, edición guardada que vuelve al Host y terminal restaurada al salir. **Falta el checkpoint en las terminales reales** (las seis de la Fase 4), en particular la rama de Windows, que sólo se probó con la consola falsa.

### Fase 6: Verificación Integral, Distribución y Alta en la Plataforma

* **Tareas:**
  1. Suites `tests/test_student_tools_ide.py` y `tests/test_media_artifacts.py` (integración real con VM).
  2. Matriz manual en laboratorio (§5.2) con checklist firmada por plataforma.
  3. Instalación del alumno documentada en el README en tres bloques (Windows/macOS/Linux): instalar `uv`, `uv tool install git+https://github.com/INGCOM-UNRN-P1/construct`, `construct setup`, `construct doctor`.
  4. Registrar `construct` en `dev/tools/p1-tools/ecosistema.toml` (perfil `estudiante`).
  5. Ejecutar `python3 revision/evidencia/generar_docs.py` para emitir `revision/docs/construct.md` y actualizar `revision/docs/README.md`.
* **Checkpoint:** CI en verde en las tres plataformas, matriz manual completa y documentación indexada.
* **Estado (27/10/2026):** las tareas 1, 3, 4 y 5 están hechas; la 2 quedó preparada.
  1. `tests/test_student_tools_ide.py` y `tests/test_media_artifacts.py`, sobre una sesión real (`tests/vm_real.py`): 11 pasan contra la VM Debian con KVM. Dos *xfail* estrictos documentan problemas de satélites: `tetsuo` sin el detalle de ASan bajo `nostromo`, y `bitmaps`, que guarda los píxeles en RGB cuando el formato BMP es BGR. Encontraron un bug propio, ya corregido: la entrada escrita mientras el programa compila se perdía. Corren en `imagenes.yml` (e2e-kvm), junto con el job nuevo `e2e-tcg` en Windows y macOS, que todavía no corrió.
  2. Checklist para firmar en [`docs/matriz_manual.md`](docs/matriz_manual.md); falta completarla en cada equipo.
  3. README con la instalación en tres bloques y la referencia generada por `p1-tools/scripts/readme_generado.py`.
  4. `construct` registrado en `p1-tools/ecosistema.toml` (perfil `estudiante`). Como `mother` no tiene cómo excluir herramientas, la imagen instala el perfil con el manifiesto publicado sin construct (`vm_image/manifiesto_guest.py`) y el doctor encadenado ignora su chequeo.
  5. `revision/docs/construct.md` generado con `revision/evidencia/generar_docs.py`.
* **Después del push (27/10/2026):** `construct` y `p1-tools` están publicados y el workflow `CI` está en verde en Linux, Windows y macOS (Python 3.12 y 3.13, lint y tipos). `imagenes.yml` sólo corre con un tag `imagen-v*`, un PR que toque `vm_image/` o a mano, así que todavía no corrió nunca. Siguientes pasos, en orden:
  1. **Primera corrida de `imagenes.yml`** (`gh workflow run imagenes.yml`): construye la imagen `aarch64` por primera vez en el runner ARM y corre `e2e-kvm`, con las suites de integración, y `e2e-tcg` en Windows y macOS. Lo que falle se corrige antes de publicar.
  2. **Publicar la imagen Debian:** subir `IMAGEN_VERSION` a `0.2.0` en `core/imagenes.py` y empujar el tag `imagen-v0.2.0`, que construye y publica el release con su `imagenes.json`. Hasta entonces `construct setup` baja la `v0.1.0`, que es Alpine y no tiene ASan. Después, comprobar `construct setup` y `construct doctor` desde cero en una máquina limpia.
  3. **Manifiesto de `mother`:** actualizar la copia de `ecosistema.toml` que trae `mother`, que no coincide con la de `p1-tools` y además no tiene `construct`, hasta que `ecosistema.py verificar` dé verde.
  4. **Matriz manual** ([`docs/matriz_manual.md`](docs/matriz_manual.md)) en Windows, macOS y las seis terminales. Con eso se cierran los checkpoints que siguen abiertos en las Fases 1 y 3 a 5. Las teclas que no lleguen (punto 4.14) se resuelven con alternativas en `ui/keymap.py`.
  5. **Issues en los satélites:**
     * `bitmaps`: los píxeles se guardan en RGB y el formato BMP es BGR.
     * `nostromo`/`tetsuo`: bajo el sandbox se pierde el informe de ASan.
     * `mother`: no hay forma de excluir una herramienta de un perfil.

     Cuando se arreglen, los *xfail* estrictos van a fallar y ahí se sacan. Si `mother` permite excluir `construct`, se quitan también `vm_image/manifiesto_guest.py` y el filtro de `chequeos_del_guest`.
  6. **Fijar las versiones del Guest:** cuando `ecosistema.toml` fije la matriz del cuatrimestre, la imagen deja de instalar las herramientas desde la rama principal de cada repo y se publica una imagen nueva.
  7. **`revision`:** commitear `docs/construct.md` y la fila del índice, que quedaron sin seguimiento junto con el generador.

---

## 5. Estrategia de Verificación Multiplataforma

### 5.1. Integración continua (GitHub Actions)

| Job | Runner | Alcance |
| :--- | :--- | :--- |
| `unit` | `ubuntu-latest`, `windows-latest`, `macos-latest` × Python 3.12/3.13 | Toda la lógica del Host con *fakes* de plataforma y un agente Guest simulado en Python que habla el protocolo `construct-ctl`. |
| `contract` | Los tres | `test_cli_contract.py`: `-h`, `-v`, `doctor --json`, cero *stack traces*, salida UTF-8. |
| `e2e-kvm` | `ubuntu-latest` (con `/dev/kvm` habilitado por regla `udev`) | VM real `x86_64` acelerada: build, check, test, run, crash, sync, artefactos. |
| `e2e-tcg` | `windows-latest`, `macos-latest` | VM real en TCG (los runners alojados no exponen WHPX/HVF anidado): smoke test de arranque, handshake, sync y una compilación. Tiempo de espera amplio. |
| `image` | `ubuntu-latest` + `buildx`/QEMU binfmt | Construcción de imágenes `x86_64` y `aarch64` y publicación con `sha256`. |

> Que la aceleración real (WHPX/HVF) no se pueda probar en CI hace obligatoria la matriz manual.

### 5.2. Matriz manual (laboratorio de la cátedra / equipos de ayudantes)

| Host | Acelerador | Terminales | Casos especiales |
| :--- | :--- | :--- | :--- |
| Windows 11 Pro | WHPX | Windows Terminal, conhost | Usuario con tilde en el nombre; Hyper-V/WSL2 activos a la vez |
| Windows 10/11 Home | WHPX | Windows Terminal | Característica deshabilitada → remediación de `doctor` |
| macOS Apple Silicon | HVF (`aarch64`) y TCG (`x86_64`) | Terminal.app, iTerm2 | Python bajo Rosetta; teclas de función reservadas |
| macOS Intel | HVF | Terminal.app | — |
| Ubuntu LTS / Fedora / Arch | KVM | GNOME Terminal, Konsole, kitty | Usuario fuera del grupo `kvm`; VirtualBox abierto |
| Windows + WSL2 | — | — | `doctor` recomienda uso nativo |

### 5.3. Criterios de aceptación transversales

* Ningún módulo fuera de `construct.platform` importa `termios`, `msvcrt`, `winreg`, `fcntl` ni compara `sys.platform` (verificado con un test que inspecciona el AST del paquete).
* Mismo `hallazgos.json` (salvo rutas absolutas del Host) para el mismo código en las tres plataformas.
* Arranque en frío hasta IDE operativo < 10 s con aceleración; documentado el tiempo en TCG.
* Todo error del Host termina en un mensaje en español con la acción concreta a tomar, nunca en un *traceback*.
* Ningún módulo fuera de `SyncManager` transfiere archivos de trabajo entre Host y Guest (verificado por test).
* El perfil estudiante funciona sin GDB en el Host; el modo desarrollo no es requisito para ningún test de `unit` o `contract`.

---

## 6. Enmiendas Propuestas a `SPEC.md`

1. **Arquitectura Target:** `x86_64` (KVM/WHPX/HVF) **y `aarch64` (KVM/HVF) en hosts ARM**; `x86_64` sobre TCG como modo compatibilidad (D1).
2. **§4 Arquitectura:** para estudiantes, GDB corre en el Guest y su salida MI se tunelea; el Host no requiere GDB. En modo desarrollo, `gdbserver` se expone en un puerto *loopback* del Host para GDB local (D2). Un único transporte TCP *loopback* multiplexado y autenticado en lugar de sockets separados (D3).
3. **§5/§6:** keymap con alternativas y paleta de comandos para atajos no transmitibles (D4).
4. **§6.1 RF-02:** se agregan los subcomandos `setup`, `vm`, `dev` y `entrar` al contrato CLI.
5. **§6.4 RF-08:** la sincronización es responsabilidad exclusiva del comando `construct sync` (`subir`, `bajar`, `artefactos`, `estado`); sin carpetas compartidas (D5).
6. **§1, §4 y §7 Guest:** Debian en lugar de Alpine (`vm_image/Dockerfile.debian`), porque el gcc de Alpine no trae AddressSanitizer ni UBSan (ver Fase 2).
7. **§7 Estructura:** se agregan `src/construct/platform/`, `src/construct/core/channel.py`, `src/construct/core/orquestador.py`, `src/construct/core/explicar.py`, `src/construct/ui/nucleo.py`, `src/construct/ui/medios.py`, los widgets `hallazgos_view.py` y `aviso_modal.py`, `src/construct/ui/keymap.py`, `tests/platform/` y `docs/fase0_resultados.md`.
