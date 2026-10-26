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
