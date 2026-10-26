# Especificación Técnica y de Requisitos de Software: `construct`

**Versión:** 5.0.0-unrn-p1-ide-vm

**Estado:** Aprobado para implementación (Alineado con `09_descripcion_del_ecosistema.md` — Octubre 2026)

**Cátedra:** Programación I — Ingeniería en Computación (UNRN Sede Andina)

**Nota de Nomenclatura (Cine de Acción / Sci-Fi de los 90s):** Dado que **`hardboiled`** ya está implementado y asignado en el catálogo oficial (§3.3) como el *emulador bare-metal de microcontrolador RV32I*, esta nueva herramienta de entorno virtualizado `x86_64` + IDE integrado adopta el nombre **`construct`** (*The Matrix*, 1999: el programa de carga y simulación virtual donde se aprovisionan herramientas y se entrena en un entorno controlado). Alternativas válidas de la misma línea: **`nebuchadnezzar`** (*The Matrix*, 1999 — la nave que se conecta a la simulación), **`auriga`** (*Alien: Resurrection*, 1997 — nave laboratorio de pruebas) o **`fiorina`** (*Alien 3*, 1992 — colonia de máxima contención).

**Arquitectura Target:** `x86_64` (Aceleración nativa de hardware obligatoria: KVM en Linux, WHPX en Windows, HVF en macOS)

**Gestor de Proyecto:** `uv` (Python 3.12+, soberanía técnica sin dependencias planas de PyPI)

---

## 1. Visión General y Rol en el Ecosistema

**`construct`** es el **IDE pedagógico y orquestador de máquina virtual `x86_64`** del ecosistema de **Programación I (UNRN)**. Su objetivo es eliminar toda fricción de configuración en los sistemas operativos anfitriones de los alumnos (Windows, macOS, diversas distros Linux) proveyendo una interfaz de desarrollo unificada en el Host que ejecuta, por debajo y de forma transparente, una máquina virtual ultraligera basada en **Alpine Linux (`x86_64`)** acelerada por hardware.

A diferencia de `hardboiled` (enfocado en microcontroladores RV32I bare-metal), **`construct`** provee un entorno GNU/Linux real donde corren preinstaladas todas las herramientas del **perfil `estudiante`** aprovisionadas por `mother`, integrando sus salidas (`hallazgos.json`, trazas de memoria, post-mortems y pruebas unitarias) directamente en paneles visuales del IDE.

---

## 2. Propuestas de Nombre (Línea Sci-Fi / Acción 90s del Ecosistema)

| Nombre Propuesto | Referencia Cinematográfica | Justificación en el Ecosistema P1 |
| :--- | :--- | :--- |
| **`construct`** *(Seleccionado)* | *The Matrix* (1999) | El "Constructo" es el entorno virtual limpio donde Morpheo y Tank cargan herramientas, simulaciones y escenarios de práctica aislados sin riesgo para el exterior. |
| **`auriga`** | *Alien: Resurrection* (1997) | Nave científica y médica donde se cultivan, monitorean e instrumentan especímenes en celdas de contención; ideal por su afinidad con `nostromo`, `sulaco`, `ripley` y `mother`. |
| **`fiorina`** | *Alien 3* (1992) | *Fiorina "Fury" 161*: instalación industrial y correccional aislada de máxima seguridad donde todo recurso está medido al límite. |
| **`demoman`** | *Demolition Man* (1993) | Prisión criogénica asistida por computadora con monitoreo constante de comportamiento y rehabilitación guiada. |

---

## 3. Integración Total del Perfil `estudiante` en el IDE

El IDE de `construct` respeta estrictamente el principio de **dueño único de responsabilidad** (§1.3 de la descripción del ecosistema): **no reimplementa linters, compiladores ni analizadores**, sino que actúa como el *frontend* visual y puente de orquestación sobre los 10 paquetes del perfil `estudiante` y las 3 librerías C de cátedra que residen dentro de la VM:

### 3.1. Mapeo de Herramientas del Perfil `estudiante` a Vistas del IDE

| Herramienta | Rol en el Ecosistema | Integración Directa dentro del IDE `construct` |
| :--- | :--- | :--- |
| **`daedalus`** | Compilador pedagógico C11 (`-Wall -Wextra -Werror -pedantic -std=c11`) | Botón/Atajo **Compilar (`F7`)**: ejecuta `daedalus` en la VM; parsea los errores traducidos al español rioplatense y subraya en rojo las líneas en el editor de código. |
| **`ripley`** | Orquestador central de análisis estático | Se ejecuta automáticamente al guardar (`Ctrl+S`) o con **Auditar (`F8`)**. Consolida los reportes en esquema `hallazgos.json` y alimenta el *Gutter de Diagnósticos* y el *Panel de Hallazgos*. |
| **`gaff`** | Linter de estilo y convenciones (indentación, un solo `return`, sufijos `_e`) | Sus advertencias se muestran en amarillo en el editor. Expone la acción rápida **"Aplicar Autofix de `gaff`" (`Ctrl+.`)** que formatea y sanea el código en el acto. |
| **`spunkmeyer`** | Detector de antipatrones didácticos (`while(!feof())`, casteo de `malloc`) | Resalta antipatrones en el editor con ícono pedagógico `[!]` y despliega la explicación didáctica en un *tooltip* o panel lateral. |
| **`kaneda`** | Auditor de seguridad estática (`gets`, `strcpy`, `sprintf`, `system`) | Bloquea visualmente la ejecución con una alerta de seguridad con su identificador **CWE** correspondiente si detecta funciones prohibidas. |
| **`nostromo`** | Sandbox blindado (`bwrap` + cuotas `setrlimit`) | **Envoltorio obligatorio**: todo binario del estudiante lanzado desde el IDE se ejecuta dentro de la VM invocado a través de `nostromo.ejecutar_comando_aislado`. |
| **`tetsuo`** | Ejecución dinámica con ASan y UBSan en español | Modo **"Ejecutar con `tetsuo`" (`Shift+F5`)**: instrumenta el binario y muestra en el IDE el mapa exacto del desborde de buffer o fuga de memoria en español rioplatense. |
| **`hal`** | Diagnóstico post-mortem (`SIGSEGV`, `SIGABRT`, `SIGFPE`) | Si el programa crashea en ejecución normal o depuración, el IDE abre automáticamente el **Modal Post-Mortem de `hal`**, explicando la causa del siniestro y ofreciendo guardar el script reproductor (`hal_reproducer.sh`). |
| **`bishop`** | Trazador visual de memoria (Stack Frames, punteros y bloques del Heap) | Alimenta el **Panel Visualizador de Memoria (`bishop` View)**: renderiza en vivo (en ASCII enriquecido en la TUI o HTML/Mermaid en vista extendida) las cajas del Stack, flechas de punteros y bloques de `malloc`/`free`. |
| **`jonesy`** | IDE de terminal en Rust | Soportado en modo **Passthrough (`construct jonesy`)**: adjunta la terminal del Host directamente a una sesión interactiva de `jonesy` corriendo de forma nativa dentro de la VM Alpine. |

### 3.2. Integración de Librerías C de Soporte y Previsualización de Artefactos

| Librería C | Propósito en Prácticas | Soporte Específico en el IDE `construct` |
| :--- | :--- | :--- |
| **`treadstone`** | Pruebas unitarias (`p1_test.h`, `libtreadstone.a`) | **Panel Test Runner (`F6`)**: ejecuta las suites de `treadstone`, lista cada test con estado `[PASS]` / `[FAIL]`, muestra las pistas didácticas y el *diff* textual de strings. |
| **`bitmaps`** | Imágenes BMP 24-bit sin comprimir (`bitmaps.h`) | **Sincronización y Visor de Imágenes**: al finalizar la ejecución en la VM, descarga automáticamente los `.bmp` generados y los previsualiza en el IDE (en bloques Unicode Half-Block `▀`/`▄` de 24-bit TrueColor o abriendo el visor del Host). |
| **`sonido`** | Audio digital PCM / WAV (`sonido.h`) | **Sincronización y Inspector de Onda**: descarga automáticamente los archivos `.wav` generados en la VM, grafica la forma de onda PCM en el IDE (detección de *clipping* o silencio) y permite reproducirlos en el Host. |

---

## 4. Arquitectura del Sistema y Separación de Capas

El diseño mantiene la interfaz desacoplada mediante un bus de eventos (`cmd_queue` / `evt_queue`), permitiendo operar tanto con la **TUI en Textual** como con una **GUI avanzada futura** sin modificar el orquestador de la VM:

```
+---------------------------------------------------------------------------------------+
|                       Host del Estudiante (`construct` CLI & IDE)                     |
|                                                                                       |
|  +---------------------------------------------------------------------------------+  |
|  | Capa de Interfaz Desacoplada (Textual TUI / Futura GUI Web o Qt)                |  |
|  | - Editor C11 con Gutter de `hallazgos.json` (`ripley`: `gaff`/`spunkmeyer`/etc) |  |
|  | - Inspector Visual de Punteros y Heap (`bishop` ASCII / Mermaid / HTML)         |  |
|  | - Panel de Pruebas `treadstone` (`p1_test.h`) & Diagnóstico Post-Mortem (`hal`) |  |
|  | - Monitor de Recursos en Vivo (RAM, CPU, FDs, Deadlocks) + Visor `.bmp`/`.wav`  |  |
|  +----------------------------------------+----------------------------------------+  |
|                                           | Eventos / Comandos (`events.py`)          |
|  +----------------------------------------v----------------------------------------+  |
|  | Núcleo de Orquestación (`construct.core` sobre `yutani`)                        |  |
|  | - `VMManager` (QEMU x86_64 + KVM/WHPX/HVF)  - `SyncManager` (Git 3-way + Artef.)|  |
|  | - `ToolBridge` (Invocador JSON de `daedalus`, `ripley`, `tetsuo`, `hal`, etc.)  |  |
|  | - `GDBClient` (`pygdbmi` x86_64)            - `AIClient` (Puente a `yutani.ia`) |  |
|  +----+-----------------------------------+-----------------------------------+----+  |
+-------|-----------------------------------|-----------------------------------|-------+
        | VirtIO-Blk                        | VirtIO-Serial                     | Socket
  (`workspace.qcow2`)                (`construct-ctl`)                 (`construct-tlm` + GDB)
        |                                   |                                   |
+-------v-----------------------------------v-----------------------------------v-------+
|                  Guest VM: Alpine Linux x86_64 (Aceleración por Hardware)             |
|                                                                                       |
|  +-----------------------------------+  +------------------------------------------+  |
|  | Plataforma & Perfil `estudiante`  |  | Ejecución Confinada e Instrumentación    |  |
|  | - `mother` + `yutani`             |  | - `nostromo` (`bwrap` + `setrlimit`)     |  |
|  | - `daedalus`, `ripley`, `gaff`,   |  | - `tetsuo` (ASan/UBSan) / `hal` / `bishop`|  |
|  |   `spunkmeyer`, `kaneda`, `jonesy`|  | - `construct_daemon` (`cgroups v2` +     |  |
|  | - Libs: `treadstone`, `bitmaps`,  |  |   `/proc/<pid>` + `libconstruct_preload`)|  |
|  |   `sonido`                        |  | - `gdbserver` (`x86_64`)                 |  |
|  +-----------------------------------+  +------------------------------------------+  |
+---------------------------------------------------------------------------------------+
```

---

## 5. Distribución Visual del IDE (`construct ui`)

```
+---------------------------------------------------------------------------------------+
| [construct] VM: x86_64 (KVM) | Perfil: estudiante | Sync: OK | RAM: 4.2/32MB | CPU: 3%|
+-------------------------------------------+-------------------------------------------+
| [1] Editor / Depurador C11 (`main.c`)     | [2] Memoria (`bishop`) & Artefactos       |
|                                           |                                           |
|  10   int main(void) {                    |  STACK (`main` frame: 0x7fffffffe1a0)     |
|  11       imagen_t *img = bmp_crear(8,8); |  +------------------+-------------------+ |
| [!] 12    while (!feof(f)) {              |  | img (bmp_t*)     | 0x5555555592a0 ---+-+
| =>13      bmp_set_pixel(img, 0, 0, ROJO); |  +------------------+-------------------+ |
|  14       bmp_guardar(img, "out.bmp");    |  HEAP (`bishop` + `libconstruct` live)    |
|  15       return 0;                       |  +--------------------------------------+ |
|  16   }                                   |  | <0x5555555592a0> 192 bytes (activo)  | |
|                                           |  | Reservado en main.c:11 (Fuga si sale)| |
| [Spunkmeyer L12]: Evitá `while(!feof(f))` |  +--------------------------------------+ |
| [Botón: Aplicar Autofix `gaff` (Ctrl+.)]  |  [Tab: bishop | Registros | BMP | WAV]    |
+-------------------------------------------+-------------------------------------------+
| [3] Hallazgos (`ripley`) & Telemetría     | [4] Consola Rioplatense / `treadstone`    |
|                                           |                                           |
| [WARN] main.c:12 (spunkmeyer: SPK-001)    | $ daedalus: Compilación limpia (0 errores)|
|   Lectura adelantada omitida en feof().   | $ treadstone: Ejecutando 4 pruebas...     |
| [INFO] main.c:15 (telemetría: TLM-FD-01)  |   [PASS] test_creacion_bmp (0.8 ms)       |
|   Descriptor FD #3 ('datos.in') abierto   |   [FAIL] test_liberacion_memoria          |
|   sin `fclose()` antes de salir de main.  |     Pista: Olvidaste llamar a bmp_destruir|
| [Botón: Consultar explicación `yutani.ia`]|   [tetsuo]: 192 bytes perdidos en 1 bloque|
+-------------------------------------------+-------------------------------------------+
| [F5] Run (`nostromo`) | [F6] Tests (`treadstone`) | [F7] Build (`daedalus`) | [F8] Check|
| [F9] Breakpoint | [F10] Step Over | [F11] Step Into | [Ctrl+S] Sync Git & Artefactos  |
+---------------------------------------------------------------------------------------+
```

---

## 6. Requisitos Funcionales (RF)

### 6.1. Cumplimiento Estricto de `LINEAMIENTOS.md` (§5)
* **RF-01 (Instalación sin PyPI):** Todas las dependencias internas del ecosistema (`yutani`, `mother`, etc.) en `pyproject.toml` se declaran como referencias Git directas: `paquete @ git+https://github.com/INGCOM-UNRN-P1/...`.
* **RF-02 (Contrato CLI Unificado §3.2):**
  * Construido sobre `TyperConErrores` de `yutani` (cero *stack traces* de Python ante errores del usuario o fallos de compilación; mensajes en español rioplatense).
  * Flags `-h`/`--help` en español y `-v`/`--version` devolviendo `construct X.Y.Z`.
  * Subcomando `construct doctor` y `construct doctor --json` que verifica el hipervisor del Host (QEMU `x86_64`, aceleración KVM/WHPX/HVF, imagen de disco) y consulta `mother doctor --json` dentro de la VM. La salida JSON usa el sobre común de `yutani.doctor` (LINEAMIENTOS §3.3), igual que el resto del ecosistema:
    ```json
    {
      "schema_version": "1.0.0",
      "herramienta": "construct",
      "version": "X.Y.Z",
      "ok": true,
      "chequeos": [
        {"nombre": "aceleración", "requerido": true, "ok": true,
         "detalle": "KVM para un Guest x86_64 (/dev/kvm accesible (API 12))",
         "proposito": "acelerar la VM por hardware", "sugerencia": ""}
      ]
    }
    ```
    `ok` es `true` cuando pasan todos los chequeos con `requerido: true`; el código de salida es `0` en ese caso y `1` si falla alguno. Cada chequeo fallido trae en `sugerencia` la instrucción concreta para el sistema operativo y la distribución del Host.

### 6.2. Orquestación del Perfil `estudiante` en el IDE
* **RF-03 (Pipeline de Compilación y Análisis Estático):**
  * Toda compilación se delega a **`daedalus`** dentro de la VM con los flags normativos `-Wall -Wextra -Werror -pedantic -std=c11`.
  * El comando de auditoría invoca a **`ripley --json`** (que orquesta a **`gaff`**, **`spunkmeyer`** y **`kaneda`**), deserializa la clave `hallazgos` y posiciona los marcadores directamente sobre las líneas y columnas del código fuente en el IDE.
  * Soporte para invocar `gaff --autofix` desde la interfaz y recargar el buffer del archivo sincronizado.
* **RF-04 (Ejecución Aislada y Diagnóstico Dinámico):**
  * Toda ejecución de binarios estudiantiles o suites de `treadstone` se realiza obligatoriamente a través de **`nostromo.ejecutar_comando_aislado`** combinado con cuotas de `cgroups v2`.
  * El IDE permite alternar en un clic entre ejecución estándar, ejecución instrumentada con **`tetsuo`** (ASan/UBSan) y generación de diagramas de memoria con **`bishop`**.
  * Ante una señal fatal (`SIGSEGV`, `SIGABRT`, `SIGFPE`), el demonio de la VM captura el *coredump*, ejecuta **`hal`** y **`bishop`**, y presenta en el IDE el diagnóstico post-mortem junto con el estado exacto de punteros y *stack frames* previo al fallo.
* **RF-05 (Modo Delegado a `jonesy`):** El subcomando `construct jonesy` redirige la terminal interactiva del estudiante directamente al binario en Rust de `jonesy` preinstalado dentro de la VM, garantizando compatibilidad total con las guías de la cátedra.

### 6.3. Subsistema de Instrumentación de Recursos y Emisión de `hallazgos.json`
* **RF-06 (Monitoreo de Proceso en Tiempo Real):** Durante la ejecución o depuración paso a paso con `gdbserver`, `construct_daemon` muestrea `/proc/<pid>` y `cgroups v2` cada $50\text{--}100\text{ ms}$ y mediante `libconstruct_preload.so` (`__builtin_return_address(0)`) reporta:
  * Memoria física (RSS), bloques activos en el Heap (complementando a `bishop`), consumo de Stack, tiempos de CPU (`utime`/`stime`) y descriptores de archivo (`fd`) abiertos.
* **RF-07 (Anomalías de Recursos como `hallazgos` de `yutani`):** Si el monitor detecta *espera activa* ($100\%$ CPU sin I/O), *fugas de descriptores de archivo* (archivos abiertos sin `fclose`) o *deadlocks* (`/proc/<pid>/task/<tid>/wchan`), emite un objeto compatible con el esquema `hallazgos.json` de `yutani` para que el IDE lo muestre en el mismo panel que los reportes de `ripley`.

### 6.4. Sincronización Híbrida y Asistencia IA Gobernada
* **RF-08 (Sincronización Git 3-Way + Artefactos `.bmp`/`.wav`):**
  * Sincroniza el código entre el Host y `/workspace` en la VM mediante Git, presentando un modal interactivo de fusión de 3 vías ante conflictos.
  * Transfiere fuera del repositorio los lotes de entrada y descarga automáticamente los artefactos producidos por **`bitmaps`** (`.bmp`), **`sonido`** (`.wav`), **`bishop`** (`.html`/`.mmd`) y **`hal`** (`reproducer.sh`).
* **RF-09 (Asistencia Pedagógica con `yutani.ia` §3.4):** El IDE incluye la acción **"Explicar este hallazgo"** sobre cualquier reporte de `daedalus`, `ripley`, `tetsuo` o `hal`, que consulta exclusivamente a `yutani.ia` (soportando `ollama`/`llama.cpp` local o proveedores configurados) enviando únicamente la línea afectada y el objeto del hallazgo.

---

## 7. Estructura del Repositorio (`construct`)

```text
construct/
├── pyproject.toml                  # Gestionado con uv; deps vía git+https://github.com/INGCOM-UNRN-P1/...
├── README.md
├── project.toml                    # Configuración declarativa de la VM, límites y herramientas
├── vm_image/
│   ├── Dockerfile.alpine           # Alpine x86_64 + `mother instalar --perfil estudiante` + libs C
│   ├── build_vm.sh                 # Genera bzImage + rootfs/qcow2 con el perfil preaprovisionado
│   └── guest_agent/
│       ├── libconstruct_preload.c  # Interposición ligera de malloc/free/fopen/fclose
│       └── construct_daemon.c      # Agente guest: puente hacia daedalus/ripley/nostromo/tetsuo/hal/bishop
├── src/
│   └── construct/
│       ├── __init__.py
│       ├── cli.py                  # TyperConErrores (yutani): ui, jonesy, doctor, sync, build, check, test, run
│       ├── core/
│       │   ├── doctor.py           # Valida QEMU + KVM/WHPX/HVF y encadena `mother doctor --json`
│       │   ├── vm_manager.py       # Orquestador QEMU x86_64 con aceleración por hardware
│       │   ├── sync_manager.py     # Git 3-way merge + transferencia automática de .bmp, .wav y reportes
│       │   ├── tool_bridge.py      # Adaptador JSON para daedalus, ripley, gaff, tetsuo, hal, bishop, treadstone
│       │   ├── gdb_client.py       # Cliente GDB/MI (pygdbmi) sincronizado con vistas de bishop
│       │   ├── telemetry.py        # Lector de cgroups/proc y emisor de anomalías en esquema hallazgos.json
│       │   └── events.py           # Eventos y comandos inmutables entre el core y la interfaz
│       └── ui/
│           ├── app.py              # IDE Textual desacoplado (reemplazable por GUI futura)
│           └── widgets/
│               ├── code_editor.py          # Editor/Visor C11 con gutter de hallazgos y botón autofix de gaff
│               ├── bishop_view.py          # Renderizador interactivo de Stack Frames y Heap de bishop
│               ├── treadstone_view.py      # Runner visual de pruebas unitarias p1_test.h
│               ├── hal_postmortem_modal.py # Ventana emergente ante SIGSEGV/SIGABRT con explicación de hal
│               ├── media_preview.py        # Previsualizador de bitmaps (.bmp) y formas de onda de sonido (.wav)
│               ├── resource_monitor.py     # Telemetría de RAM, CPU, FDs y alertas de espera activa
│               └── sync_conflict_modal.py  # Resolución interactiva de conflictos Git de 3 vías
└── tests/
    ├── test_cli_contract.py        # Verifica -h, -v, `doctor --json` y cero stack traces (LINEAMIENTOS §3.2)
    ├── test_student_tools_ide.py   # Verifica integración de daedalus, ripley, gaff, tetsuo, hal, bishop
    └── test_media_artifacts.py     # Verifica descarga e inspección de artefactos de `bitmaps` y `sonido`
```