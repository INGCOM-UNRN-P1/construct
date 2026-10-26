# Protocolo `construct-ctl` (versión 1)

Canal único entre el Host (`construct`) y el agente del Guest (`construct_daemon`), sobre el puerto
VirtIO-Serial `construct-ctl` (PLAN §3). El Host escucha en `127.0.0.1:<puerto efímero>` y QEMU se
conecta como cliente; del lado del Guest el puerto aparece como `/dev/vportNpM`, que el agente
encuentra por nombre en `/sys/class/virtio-ports/*/name`.

## Marco

```
+--------+----------------+----------------+------------------+
| tipo   | id             | largo          | datos            |
| u8     | u32 big-endian | u32 big-endian | `largo` bytes    |
+--------+----------------+----------------+------------------+
```

* `largo` ≤ 1 MiB (`LARGO_MAXIMO`); un marco más largo cierra la conexión.
* `id` identifica el proceso o la transferencia; `0` es el control general. Los `id` los elige el Host.
* Los datos de los mensajes de control son un objeto JSON en UTF-8; los de flujo (`ENTRADA`,
  `SALIDA`, `ERRORES`, `DATOS`), bytes crudos.

## Sesión

1. El agente abre el puerto (o reintenta hasta que el Host se conecte) y manda **`HOLA`**:
   `{"protocolo": 1, "token": "<token>", "agente": "0.1.0", "arquitectura": "x86_64"}`.
   El token lo inyecta el Host con `-fw_cfg name=opt/construct/token,string=<token>` y el agente lo
   lee de `/sys/firmware/qemu_fw_cfg/by_name/opt/construct/token/raw`.
2. El Host verifica el token y la versión, y contesta **`BIENVENIDA`** `{"protocolo": 1, "latido_s": 5}`;
   si algo no coincide, cierra la conexión sin responder.
3. Desde ahí el Host manda `LATIDO` cada `latido_s` segundos y el agente responde `LATIDO_OK`. Si el
   agente pasa `3 × latido_s` sin recibir nada, o el Host se desconecta y no vuelve en el tiempo de
   espera, apaga la VM (salvo con `--sin-apagado`, para pruebas).

## Mensajes

| Código | Nombre | Sentido | Datos |
| :--- | :--- | :--- | :--- |
| `0x01` | `HOLA` | Guest → Host | `{protocolo, token, agente, arquitectura}` |
| `0x02` | `BIENVENIDA` | Host → Guest | `{protocolo, latido_s}` |
| `0x03` | `ERROR` | ambos | `{mensaje}`; con el `id` del pedido que falló |
| `0x04` | `LATIDO` | Host → Guest | vacío |
| `0x05` | `LATIDO_OK` | Guest → Host | vacío |
| `0x10` | `EJECUTAR` | Host → Guest | `{argv, cwd?, env?, pty?, filas?, columnas?, root?}` |
| `0x11` | `INICIADO` | Guest → Host | `{pid}` |
| `0x12` | `ENTRADA` | Host → Guest | bytes para la entrada estándar; vacío = fin de la entrada |
| `0x13` | `SALIDA` | Guest → Host | bytes de la salida estándar (o de la terminal, con `pty`) |
| `0x14` | `ERRORES` | Guest → Host | bytes de la salida de errores (sin `pty`) |
| `0x15` | `TERMINADO` | Guest → Host | `{codigo, senal}`: `senal` es el nombre (`"SEGV"`) o `null` |
| `0x16` | `SENAL` | Host → Guest | `{senal}`: `INT`, `TERM`, `KILL`, `QUIT`, `HUP`, `USR1`, `USR2`, `STOP`, `CONT` |
| `0x17` | `TAMANO` | Host → Guest | `{filas, columnas}` de la terminal (sólo con `pty`) |
| `0x20` | `ESCRIBIR` | Host → Guest | `{ruta, modo?, root?}`: abre una transferencia al Guest |
| `0x21` | `DATOS` | ambos | bytes de una transferencia |
| `0x22` | `FIN` | ambos | cierra una transferencia: Host → Guest vacío; Guest → Host `{bytes}` |
| `0x23` | `HECHO` | Guest → Host | `{bytes}`: la transferencia al Guest quedó escrita |
| `0x24` | `LEER` | Host → Guest | `{ruta, root?}`: el Guest responde `DATOS`… y `FIN`, o `ERROR` |
| `0x30` | `APAGAR` | Host → Guest | vacío: sincroniza los discos y apaga |

### Ejecución

* `argv` se ejecuta tal cual (sin shell) en `cwd` (por defecto `/workspace`). El Host arma el
  `argv` completo, incluido el envoltorio `nostromo` cuando corresponde (RF-04): el agente no conoce
  las herramientas.
* Sin `root: true`, el proceso corre como el usuario `estudiante`.
* Con `pty: true` el proceso tiene una terminal propia (`filas` × `columnas`, por defecto 24 × 80);
  `SALIDA` trae lo que muestra la terminal y no hay `ERRORES`.
* `TERMINADO` llega después de la última `SALIDA`/`ERRORES` del proceso. Si el programa no existe,
  llega un `ERROR` y `TERMINADO {codigo: 127}`.

### Transferencias

* Al Guest: `ESCRIBIR` → `DATOS`… → `FIN`. El agente escribe en `<ruta>.construct-parcial` y recién
  con `FIN` lo renombra a `ruta`, así nunca queda un archivo a medias; responde `HECHO`.
* Desde el Guest: `LEER` → el agente manda `DATOS`… y `FIN {bytes}`.
* Sin `root`, los archivos se leen y escriben con los permisos del usuario `estudiante`.
