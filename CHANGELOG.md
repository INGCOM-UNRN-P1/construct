# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/);
versiones según [SemVer](https://semver.org/lang/es/).

## [No publicado]

### Agregado

- **platform**: capa de plataforma del Host (sistema, arquitectura real, WSL, aceleración KVM/WHPX/HVF/TCG,
  búsqueda de QEMU/Git/GDB, carpetas por sistema y consola UTF-8).
- **doctor**: `construct doctor [--json] [--dev]` con remediación por sistema y distro.
- **config**: lectura y validación de `project.toml` (`[vm]`, `[sync]`, `[dev]`).
- **setup**: `construct setup` prepara las carpetas, diagnostica el Host y baja la imagen del Guest de su
  arquitectura desde un manifiesto publicado, con verificación SHA-256 e inventario local.
- **doctor**: chequeo de la imagen del Guest instalada.
- **agent**: agente del Guest `construct_daemon` y protocolo `construct-ctl` v1 (`docs/protocolo.md`).
- **vm-image**: imagen Debian (systemd) x86_64/aarch64 con el perfil estudiante, las librerías C y jonesy;
  `build_vm.sh`, `generar_manifiesto.py` y workflow que la construye, la arranca y la publica.
- **setup**: el manifiesto por defecto apunta a la versión de imagen compatible (`imagen-v0.1.0`), no a `latest`.
- **platform**: procesos hijos que mueren con construct (Job Objects en Windows, `PR_SET_PDEATHSIG` en
  Linux), apertura de artefactos y reproducción de `.wav` con las herramientas del Host, y rutas cortas
  8.3 en Windows.
- **vm**: sesión de VM persistente con apagado por inactividad, canal asyncio con token y
  `construct vm start|stop|status|exec`.
- **sync**: `construct sync [subir|bajar|artefactos|estado]` con Git por bundles, fusión de 3 vías y
  descarga de artefactos.
- **bridge**: `construct build`, `check`, `fix`, `run` (`--tetsuo`, `--aislado`, post-mortem de hal) y
  `test` sobre las herramientas del Guest.
- **telemetry**: muestras de `/proc` y `libconstruct_preload.so` convertidas en hallazgos (espera
  activa, deadlock, archivos sin `fclose`, memoria sin liberar) en `construct run`.
- **gdb**: cliente GDB/MI con GDB en el Guest o en el Host, y `construct dev gdb` (modo desarrollo).
- **doctor**: encadena `mother doctor --json` del perfil estudiante cuando la VM está corriendo.
- **bridge**: comandos y eventos inmutables (`core/events.py`) y el orquestador que los atiende para la
  interfaz.

### Cambiado

- **vm-image**: el Guest pasa de Alpine a Debian: el gcc de Alpine (musl) no trae AddressSanitizer
  ni UBSan, que necesitan tetsuo y ripley.
