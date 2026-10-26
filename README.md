# construct

IDE pedagógico y orquestador de máquina virtual del perfil `estudiante` de Programación 1
(UNRN Sede Andina). Corre `daedalus`, `ripley`, `nostromo`, `tetsuo`, `hal`, `bishop` y el resto
de las herramientas dentro de una VM Alpine acelerada por hardware, con la misma experiencia en
Windows, macOS y Linux.

> **Estado:** en desarrollo (Fase 1 de [`PLAN.md`](PLAN.md) completa). Por ahora están
> `construct setup`, que prepara la computadora y baja la imagen de la VM, y `construct doctor`,
> que verifica que esté todo listo. Las imágenes se publican a partir de la Fase 2.

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
construct doctor            # diagnóstico del Host (QEMU, aceleración, imagen, git, disco, memoria)
construct doctor --json     # el mismo informe en JSON (sobre común de yutani)
construct doctor --dev      # además, lo que necesita el modo desarrollo (GDB del Host)
construct -v                # versión
```

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
```

Variables de entorno:

- `CONSTRUCT_HOME`: reemplaza las carpetas de datos, estado, caché y logs por una sola.
- `CONSTRUCT_MANIFIESTO`: manifiesto de imágenes por defecto de `construct setup`.
- `http_proxy` / `https_proxy`: se respetan para las descargas (nunca para `localhost`).

## Desarrollo

```bash
uv sync
uv run pytest
```

Ver [`AGENTS.md`](AGENTS.md) para las convenciones de trabajo y [`PLAN.md`](PLAN.md) para la hoja de ruta.

## Licencia

GPL-3.0-or-later.
