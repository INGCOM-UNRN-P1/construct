# Instrucciones para el Agente (construct)

1. **Commits semánticos en español** ([Conventional Commits](https://www.conventionalcommits.org/),
   LINEAMIENTOS §1): `<tipo>(<alcance>): <descripción en minúsculas y sin punto final>`.
   - Tipos: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`.
   - Alcances de construct: `cli`, `doctor`, `setup`, `platform`, `config`, `vm`, `channel`,
     `sync`, `bridge`, `gdb`, `telemetry`, `ui`, `vm-image`, `agent`, `ci`.
   - Un commit por cambio coherente; el cuerpo explica el motivo y cómo se verificó.
2. **Sin coautoría ni atribución**: ningún commit ni descripción de PR lleva
   `Co-Authored-By:`, «Generated with Claude Code» ni otra mención a asistentes de IA.
3. **Portabilidad** (PLAN §2): sólo `construct.platform` puede consultar el sistema operativo
   (`sys.platform`, `os.name`, `platform.system`) o importar `termios`, `tty`, `pty`, `fcntl`,
   `msvcrt`, `winreg`. `tests/test_portabilidad.py` lo verifica.
4. **Dependencias**: las del ecosistema siempre como `paquete @ git+https://github.com/INGCOM-UNRN-P1/…@<commit>`;
   las de terceros, mínimas y con *wheels* para Windows, macOS y Linux.
5. **Hoja de ruta**: el orden de trabajo es el de las fases de `PLAN.md`; las decisiones D1–D5
   ya están aprobadas y no se vuelven a discutir.
6. Todo cambio lleva tests (`uv run pytest`), que tienen que pasar en las tres plataformas: lo
   específico de un SO se prueba con *fakes*, nunca suponiendo el SO de la máquina que corre los tests.
7. Mensajes, ayudas y documentación en español rioplatense con voseo (LINEAMIENTOS §4.2).
