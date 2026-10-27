# construct

IDE pedagógico y orquestador de máquina virtual del perfil `estudiante` de Programación 1
(UNRN Sede Andina). Corre `daedalus`, `ripley`, `nostromo`, `tetsuo`, `hal`, `bishop` y el resto
de las herramientas dentro de una VM Debian acelerada por hardware, con la misma experiencia en
Windows, macOS y Linux.

> **Estado:** Fases 1 a 6 de [`PLAN.md`](PLAN.md) implementadas: la línea de comandos completa, el
> IDE en la terminal (`construct ui`) y `construct jonesy`, verificados en Linux con KVM. Falta
> completar la [matriz manual](docs/matriz_manual.md) en Windows, macOS y las terminales reales.

## Requisitos

| | Windows 10/11 | macOS 13+ | Linux |
| :--- | :--- | :--- | :--- |
| Aceleración | Plataforma del hipervisor de Windows (WHPX) | Hypervisor.framework (HVF) | KVM |
| QEMU | `winget install --id SoftwareFreedomConservancy.QEMU -e` | `brew install qemu` | paquete `qemu-system-x86` (o `-arm`/`-aarch64` en ARM) |
| Git | `winget install --id Git.Git -e` | `xcode-select --install` | paquete `git` |

En las Mac con Apple Silicon la VM usa un Guest `aarch64` (acelerado); en el resto, `x86_64`.

## Instalación

Una sola vez por computadora. Ninguno de los pasos instala compiladores ni herramientas de la
cátedra en tu sistema: todo eso vive en la VM.

### Windows 10/11

1. Activá la plataforma del hipervisor en una PowerShell **como administrador** y reiniciá:

   ```powershell
   Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform
   ```

   Si `construct doctor` dice que la virtualización está apagada, activá VT-x/AMD-V en el BIOS.
2. Instalá QEMU, Git y uv (en una PowerShell común):

   ```powershell
   winget install --id SoftwareFreedomConservancy.QEMU -e
   winget install --id Git.Git -e
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

3. Cerrá y abrí la terminal (mejor **Windows Terminal**) y seguí con
   [los pasos comunes](#en-los-tres-sistemas).

### macOS 13+

```bash
xcode-select --install            # git
brew install qemu                 # https://brew.sh si todavía no tenés Homebrew
curl -LsSf https://astral.sh/uv/install.sh | sh
```

En las Mac con Apple Silicon la VM es `aarch64` y anda acelerada; no hace falta Rosetta.

### Linux

```bash
# Debian / Ubuntu
sudo apt install qemu-system-x86 qemu-utils git          # en ARM: qemu-system-arm
# Fedora
sudo dnf install qemu-system-x86 qemu-img git
# Arch
sudo pacman -S qemu-system-x86 qemu-img git

sudo usermod -aG kvm "$USER"      # y volvé a iniciar sesión: KVM sin sudo
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### En los tres sistemas

```bash
uv tool install git+https://github.com/INGCOM-UNRN-P1/construct
construct setup                   # baja la imagen de la VM y te dice qué falta, con el comando exacto
construct doctor                  # todo en verde: listo
```

Para actualizar: `uv tool upgrade construct` y, si cambió la imagen, `construct setup --forzar`.

## Uso

```bash
construct setup             # carpetas, diagnóstico y descarga verificada (SHA-256) de la imagen
construct setup --forzar    # vuelve a bajar la imagen
construct setup -m RUTA     # usa otro manifiesto de imágenes (URL o archivo local)
construct setup -i CARPETA  # instala una imagen construida en tu máquina, sin bajar nada
construct doctor            # diagnóstico del Host y, con la VM arriba, `mother doctor` adentro
construct doctor --json     # el mismo informe en JSON (sobre común de yutani)
construct doctor --dev      # además, lo que necesita el modo desarrollo (GDB del Host)
construct -v                # versión
```

En la carpeta del trabajo práctico (la VM arranca sola la primera vez y queda prendida un rato):

```bash
construct build             # F7: daedalus con -Wall -Wextra -Werror -pedantic -std=c11
construct check             # F8: ripley (gaff, spunkmeyer y kaneda)
construct fix               # arreglos automáticos de gaff, de vuelta en tus archivos
construct run [-- args]     # F5: ejecuta con telemetría; si se cae, hal explica por qué
construct run --tetsuo      # Shift+F5: con AddressSanitizer y UBSan explicados por tetsuo
construct run --aislado     # dentro del sandbox de nostromo
construct test              # F6: suites de treadstone (tests/test_*.c)
construct sync              # sube, baja con fusión de 3 vías y trae artefactos (.bmp, .wav, …)
construct sync estado       # también subir, bajar y artefactos
construct jonesy [archivo]  # jonesy, el IDE de terminal de la cátedra, dentro de la VM
construct vm start|stop|status
construct vm exec -- CMD    # un comando dentro de la VM (diagnóstico)
```

`construct run` avisa, además, de lo que vio la telemetría: espera activa, posibles deadlocks,
archivos sin `fclose` y memoria sin liberar, con su línea. Los artefactos quedan en
`.construct/artefactos/`.

`construct jonesy` conecta tu terminal directo con jonesy en la VM (con colores, mouse y el tamaño
de la ventana); al salir, lo que guardaste allá vuelve a tus archivos. En Windows necesita Windows
Terminal o la consola de Windows 10 1809 en adelante.

### El IDE: `construct ui`

Cuatro paneles: el editor (con los hallazgos marcados al costado de cada línea), la memoria de
`bishop` con los registros de la depuración y los `.bmp`/`.wav` que genera tu programa, los
hallazgos (con «Explicar» por IA y el autofix de `gaff`) y la consola con la entrada del
programa, las pruebas de `treadstone` y el consumo de recursos. Si el programa se cae, `hal`
explica por qué en una ventana.

| Acción | Atajo | Alternativa |
| :--- | :--- | :--- |
| Ejecutar / continuar la depuración | `F5` | `Ctrl+R` |
| Ejecutar con `tetsuo` | `Shift+F5` | paleta |
| Pruebas (`treadstone`) | `F6` | `Ctrl+T` |
| Compilar (`daedalus`) | `F7` | `Ctrl+L` |
| Auditar (`ripley`) | `F8` | `Ctrl+G` |
| Breakpoint / step over / step into | `F9` / `F10` / `F11` | `Ctrl+B` / `Ctrl+N` / `Ctrl+O` |
| Empezar / terminar la depuración | `Ctrl+F5` / `Ctrl+F2` | paleta |
| Memoria con `bishop` en la línea del cursor | `F12` | paleta |
| Guardar, sincronizar y auditar | `Ctrl+S` | — |
| Autofix de `gaff` | `Ctrl+.` | `Alt+Enter` |
| Explicar el hallazgo (`yutani.ia`) | `F1` | paleta |
| Paleta de comandos (todas las acciones) | `Ctrl+P` | — |

Si tu terminal no manda alguna tecla (en macOS, F10 y F11 suelen ser del sistema), usá la
alternativa, la paleta o cambiala en `[teclas]`. «Explicar» usa la configuración común de
`yutani.ia` (`~/.config/p1/ia.toml`); sin configurar, un servidor local como `ollama`.

## Configuración (`project.toml`)

Opcional, en el directorio de trabajo:

```toml
[vm]
accel = "auto"      # auto | kvm | whpx | hvf | tcg
arch = "auto"       # auto | x86_64 | aarch64
memoria_mb = 1024
cpus = 2
qemu = ""           # ruta a qemu-system-* si no está en el PATH

[sync]
automatico = true

[dev]
habilitado = false

[teclas]            # cambiar atajos del IDE: acción = "tecla" o ["tecla", "alternativa"]
paso_dentro = ["f11", "ctrl+o"]
```

Variables de entorno:

- `CONSTRUCT_HOME`: reemplaza las carpetas de datos, estado, caché y logs por una sola.
- `CONSTRUCT_MANIFIESTO`: manifiesto de imágenes por defecto de `construct setup`.
- `http_proxy` / `https_proxy`: se respetan para las descargas (nunca para `localhost`).

## Desarrollo

```bash
uv sync
uv run pytest                     # incluye el agente del Guest compilado en Linux
```

Imagen del Guest (Linux con podman o docker; ver [`docs/protocolo.md`](docs/protocolo.md)):

```bash
vm_image/build_vm.sh --arch x86_64 --version 0.1.0 --salida dist/imagenes
CONSTRUCT_IMAGEN_PRUEBA=dist/imagenes/x86_64 uv run pytest tests/e2e -s   # la arranca de verdad
CONSTRUCT_IMAGEN_PRUEBA=dist/imagenes/x86_64 uv run pytest \
    tests/test_student_tools_ide.py tests/test_media_artifacts.py      # el perfil estudiante entero
construct setup --imagen dist/imagenes                                 # la instala para usarla
```

El workflow `imagenes.yml` construye y prueba las dos arquitecturas y, con un tag `imagen-vX.Y.Z`,
publica el release que `construct setup` baja por defecto (`vm_image/generar_manifiesto.py` arma esa
carpeta, que también se instala con `construct setup -m dist/release/imagenes.json`).

`construct setup --imagen` acepta la carpeta que deja `build_vm.sh`, la de una arquitectura
(`dist/imagenes/x86_64`) o la de un release con `imagenes.json`. La versión queda como
`local-<hash del rootfs>`: si reconstruís la imagen, el mismo comando instala la nueva.

Modo desarrollo (`[dev] habilitado = true` o `CONSTRUCT_DEV=1`): `construct dev gdb [binario]`
abre el GDB del Host contra un `gdbserver` del Guest, con el sysroot de la VM y las rutas de tus
fuentes; `construct dev gdbserver` es el puente que usa (`target remote | …`).

Ver [`AGENTS.md`](AGENTS.md) para las convenciones de trabajo y [`PLAN.md`](PLAN.md) para la hoja de ruta.

<!-- p1:referencia:inicio — generado por p1-tools/scripts/readme_generado.py: no editar a mano -->

## Referencia rápida

### Requisitos

- Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Programas del sistema: `git`, `qemu-system-x86_64`, `qemu-img`.

| Sistema | `git` |
|:--|:--|
| Debian / Ubuntu | `sudo apt install git` |
| Fedora | `sudo dnf install git` |
| Windows | incluido en el entorno de la cátedra (MSYS2 UCRT64) |
| macOS | `xcode-select --install` |

### Comandos

| Comando | Descripción |
|:--|:--|
| `construct ui` | Abre el IDE en la terminal: editor, hallazgos, memoria, consola y pruebas sobre la VM. |
| `construct doctor` | Verifica el Host y, si la máquina virtual está corriendo, las herramientas de adentro. |
| `construct build` | Compila con daedalus en la VM (F7): -Wall -Wextra -Werror -pedantic -std=c11, errores en español. |
| `construct check` | Audita con ripley (F8): estilo (gaff), antipatrones (spunkmeyer) y seguridad (kaneda). |
| `construct fix` | Aplica los arreglos automáticos de gaff en la VM y los trae a tus archivos. |
| `construct run` | Ejecuta el programa en la VM conectado a esta terminal (F5); si falla con una señal, lo explica hal. |
| `construct test` | Compila y corre las pruebas de treadstone (tests/test_*.c) con tu código (F6). |
| `construct jonesy` | Abre jonesy, el IDE de terminal de la cátedra, dentro de la VM y en esta terminal (RF-05). |
| `construct sync` | Sincroniza tu carpeta con la VM (único responsable de mover archivos entre los dos). |
| `construct setup` | Prepara el Host: carpetas, diagnóstico con instrucciones y descarga de la imagen del Guest. |
| `construct vm` | Máquina virtual: arrancarla, apagarla, ver su estado o ejecutar algo adentro. |

Ayuda de cada comando: `construct <comando> -h`.

### Salida JSON

Con `--json`, estos comandos emiten el resultado como JSON por la salida estándar, para usarlo desde scripts, ripley o dredd: `construct doctor`, `construct build`, `construct check`, `construct test`, `construct sync`, `construct setup`. El de `doctor --json` lleva `schema_version` y `ok`.

### Códigos de salida

| Código | Significado |
|:--|:--|
| `0` | Terminó bien (en `doctor`: está todo lo requerido). |
| `1` | El comando encontró problemas (hallazgos, pruebas que fallan, un umbral que no se alcanza) o un dato no se pudo usar (un archivo ilegible, un formato inválido). |
| `2` | Error de uso: comando, opción o argumento inválido. |

<!-- p1:referencia:fin -->

## Licencia

GPL-3.0-or-later.
