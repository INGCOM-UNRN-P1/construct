# construct

IDE pedagógico y orquestador de máquina virtual del perfil `estudiante` de Programación 1
(UNRN Sede Andina). Corre `daedalus`, `ripley`, `nostromo`, `tetsuo`, `hal`, `bishop` y el resto
de las herramientas dentro de una VM Alpine acelerada por hardware, con la misma experiencia en
Windows, macOS y Linux.

> **Estado:** en desarrollo (Fase 1 de [`PLAN.md`](PLAN.md)). Por ahora está disponible
> `construct doctor`, que verifica que tu computadora esté lista para correr la VM.

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

3. Verificá el entorno; cada problema viene con el comando para resolverlo en tu sistema:

   ```bash
   construct doctor
   ```

## Uso

```bash
construct doctor            # diagnóstico del Host (QEMU, aceleración, git, disco, memoria)
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

`CONSTRUCT_HOME` reemplaza las carpetas de datos, estado, caché y logs por una sola.

## Desarrollo

```bash
uv sync
uv run pytest
```

Ver [`AGENTS.md`](AGENTS.md) para las convenciones de trabajo y [`PLAN.md`](PLAN.md) para la hoja de ruta.

## Licencia

GPL-3.0-or-later.
