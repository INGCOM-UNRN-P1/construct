# Matriz manual de verificación (PLAN §5.2, Fase 6)

La CI no puede probar la aceleración real (WHPX y HVF no se exponen en los runners alojados) ni
las terminales de verdad: esta lista se completa a mano en el laboratorio o en los equipos de los
ayudantes, **una copia por equipo**, y se firma. Lo que falle se anota con el comando, la salida y
una captura, y se abre como issue en `INGCOM-UNRN-P1/construct`.

Marcas: `✓` anda, `✗` falla (anotar), `—` no aplica.

## Equipo

| Campo | Valor |
| :--- | :--- |
| Sistema y versión | |
| Arquitectura (y si Python corre bajo Rosetta) | |
| Acelerador que informa `construct doctor` | |
| QEMU (`qemu-system-* --version`) | |
| Terminal y versión | |
| Usuario con tilde o espacio en la ruta del perfil | sí / no |
| Versión de construct (`construct -v`) e imagen (`construct vm status`) | |

## Instalación y diagnóstico (Fase 1)

| # | Paso | Resultado |
| :--- | :--- | :--- |
| 1.1 | Instalación según el bloque del README de este sistema, sin pasos extra | |
| 1.2 | `construct setup` baja la imagen y verifica el SHA-256 | |
| 1.3 | `construct doctor` en verde; con la aceleración apagada, la sugerencia es el comando exacto | |
| 1.4 | `construct doctor --json` es JSON válido con `schema_version` y `ok` | |
| 1.5 | Tildes y «ñ» se ven bien en la salida (conhost incluido) | |

## VM y CLI (Fases 2 y 3)

| # | Paso | Resultado |
| :--- | :--- | :--- |
| 2.1 | `construct vm start`: handshake en menos de 3 s con aceleración (anotar el tiempo) | |
| 2.2 | Con la VM arriba, `construct doctor` muestra «perfil estudiante (VM)» en verde | |
| 2.3 | `construct build` con un error: mensaje en español y `main.c:línea` del Host | |
| 2.4 | `construct check`: hallazgos de gaff, spunkmeyer y kaneda con rutas del Host | |
| 2.5 | `construct fix`: los arreglos de gaff aparecen en tus archivos | |
| 2.6 | `construct run` con un programa que usa `scanf`: la entrada funciona y `Ctrl+C` lo corta sin cerrar construct | |
| 2.7 | `construct run` de un programa que hace `SIGSEGV`: hal explica la causa y la línea | |
| 2.8 | `construct run` con `fopen` sin `fclose`: aviso TLM-FD-01 en la línea del `fopen` | |
| 2.9 | `construct test` con una suite de treadstone que falla: `FAIL` con la pista | |
| 2.10 | Conflicto: editar la misma línea en el Host y en la VM, `construct sync` deja las marcas y la siguiente sync resuelta sube | |
| 2.11 | Un programa que genera `.bmp` y `.wav`: aparecen en `.construct/artefactos/` | |
| 2.12 | `construct vm stop` apaga ordenado; no queda ningún proceso `qemu-system-*` | |
| 2.13 | Cerrar la terminal con la VM arriba: la VM se apaga sola por inactividad | |

## IDE (Fase 4): `construct ui`

Recorrido del checkpoint: editar → `F7` → `F8` → `F6` → `F5` → crash con el modal de hal.

| # | Paso | Resultado |
| :--- | :--- | :--- |
| 4.1 | La barra de estado muestra el acelerador real (`KVM`/`WHPX`/`HVF`, o `TCG — modo compatibilidad`) | |
| 4.2 | `F7` compila y marca el error en el gutter; la alternativa `Ctrl+L` también | |
| 4.3 | `F8` audita; `[!]` en el antipatrón y la pista al poner el cursor en la línea | |
| 4.4 | `F6` corre las pruebas y muestra `[PASS]`/`[FAIL]` (alternativa `Ctrl+T`) | |
| 4.5 | `F5` ejecuta; la entrada desde la consola funciona; `Ctrl+C` interrumpe | |
| 4.6 | Crash: se abre el modal de hal; «Guardar reproductor» deja `hal_reproducer.sh` | |
| 4.7 | `F9` pone un breakpoint, `Ctrl+F5` depura, `F10`/`F11` avanzan (o `Ctrl+N`/`Ctrl+O`) y Registros muestra variables y pila | |
| 4.8 | `F12` muestra la memoria de bishop con las flechas al heap | |
| 4.9 | `Ctrl+.` o `Alt+Enter` aplica el autofix de gaff y el editor recarga | |
| 4.10 | `F1` explica un hallazgo (con `yutani.ia` configurado) | |
| 4.11 | Un `.bmp` se ve en colores (TrueColor o 256); un `.wav` muestra la forma de onda y se reproduce | |
| 4.12 | `Ctrl+P` abre la paleta y todas las acciones están ahí | |
| 4.13 | `Ctrl+S` guarda, sincroniza y audita; con conflicto se abre el modal para elegir versión | |
| 4.14 | Teclas que **no** llegan en esta terminal (anotarlas: son las que necesitan alternativa) | |

## jonesy (Fase 5)

| # | Paso | Resultado |
| :--- | :--- | :--- |
| 5.1 | `construct jonesy main.c` abre jonesy en toda la ventana, con colores | |
| 5.2 | Agrandar y achicar la ventana: jonesy se redibuja con el tamaño nuevo | |
| 5.3 | Editar, guardar (`Ctrl+S` en jonesy) y salir (`Ctrl+Q`): el cambio está en el archivo del Host | |
| 5.4 | Después de salir la terminal queda normal (eco, `Ctrl+C`, flechas) | |
| 5.5 | Cerrar la VM a mitad (`construct vm stop` desde otra terminal): la terminal queda normal igual | |

## Casos especiales de este sistema (PLAN §5.2)

| Host | Caso | Resultado |
| :--- | :--- | :--- |
| Windows 11 Pro | Usuario con tilde; Hyper-V y WSL2 activos a la vez | |
| Windows 10/11 Home | Plataforma del hipervisor apagada → la sugerencia de `doctor` la resuelve | |
| macOS Apple Silicon | Guest `aarch64` con HVF; `accel = "tcg"` + `arch = "x86_64"` en `project.toml` también anda (lento) | |
| macOS Apple Silicon | Python bajo Rosetta: `doctor` lo avisa | |
| Ubuntu / Fedora / Arch | Usuario fuera del grupo `kvm` → la sugerencia de `doctor` lo resuelve | |
| Ubuntu / Fedora / Arch | VirtualBox abierto al mismo tiempo | |
| Windows + WSL2 | `construct` dentro de WSL: `doctor` recomienda usarlo nativo | |

## Firma

| Quién verificó | Fecha | Firma |
| :--- | :--- | :--- |
| | | |
