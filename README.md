# construct

IDE pedagógico y orquestador de máquina virtual del perfil `estudiante` de Programación 1
(UNRN Sede Andina). Corre `daedalus`, `ripley`, `nostromo`, `tetsuo`, `hal`, `bishop` y el resto
de las herramientas dentro de una VM Debian acelerada por hardware, con la misma experiencia en
Windows, macOS y Linux.

> **Estado:** en desarrollo (Fases 1 a 4 de [`PLAN.md`](PLAN.md)): la línea de comandos completa
> y el IDE en la terminal (`construct ui`). Falta el modo `construct jonesy` (Fase 5).

## Requisitos

| | Windows 10/11 | macOS 13+ | Linux |
| :--- | :--- | :--- | :--- |
| Aceleración | Plataforma del hipervisor de Windows (WHPX) | Hypervisor.framework (HVF) | KVM |
| QEMU | `winget install --id SoftwareFreedomConservancy.QEMU -e` | `brew install qemu` | paquete `qemu-system-x86` (o `-arm`/`-aarch64` en ARM) |
| Git | `winget install --id Git.Git -e` | `xcode-select --install` | paquete `git` |

En las Mac con Apple Silicon la VM usa un Guest `aarch64` (acelerado); en el resto, `x86_64`.

## Instalación

1. Instalá [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Instalá construct:

   ```bash
   uv tool install git+https://github.com/INGCOM-UNRN-P1/construct
   ```

3. Prepará la computadora: crea las carpetas, baja la imagen de la VM y te dice qué falta
   instalar, con el comando exacto para tu sistema:

   ```bash
   construct setup
   ```

4. Verificá que quedó todo listo:

   ```bash
   construct doctor
   ```

## Uso

```bash
construct setup             # carpetas, diagnóstico y descarga verificada (SHA-256) de la imagen
construct setup --forzar    # vuelve a bajar la imagen
construct setup -m RUTA     # usa otro manifiesto de imágenes (URL o archivo local)
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
construct vm start|stop|status
construct vm exec -- CMD    # un comando dentro de la VM (diagnóstico)
```

`construct run` avisa, además, de lo que vio la telemetría: espera activa, posibles deadlocks,
archivos sin `fclose` y memoria sin liberar, con su línea. Los artefactos quedan en
`.construct/artefactos/`.

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
vm_image/generar_manifiesto.py dist/imagenes --version 0.1.0 --salida dist/release
construct setup -m dist/release/imagenes.json                          # la instala como un alumno
```

El workflow `imagenes.yml` construye y prueba las dos arquitecturas y, con un tag `imagen-vX.Y.Z`,
publica el release que `construct setup` baja por defecto.

Modo desarrollo (`[dev] habilitado = true` o `CONSTRUCT_DEV=1`): `construct dev gdb [binario]`
abre el GDB del Host contra un `gdbserver` del Guest, con el sysroot de la VM y las rutas de tus
fuentes; `construct dev gdbserver` es el puente que usa (`target remote | …`).

Ver [`AGENTS.md`](AGENTS.md) para las convenciones de trabajo y [`PLAN.md`](PLAN.md) para la hoja de ruta.

## Licencia

GPL-3.0-or-later.
