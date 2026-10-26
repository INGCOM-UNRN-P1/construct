/* construct_daemon: agente del Guest de construct (SPEC §4, PLAN Fase 2, docs/protocolo.md).
 *
 * Atiende el puerto VirtIO-Serial `construct-ctl`: ejecuta procesos (con tuberías o con terminal
 * propia), reenvía su entrada y salida, entrega señales, transfiere archivos y apaga la VM cuando
 * el Host desaparece. No conoce las herramientas del ecosistema: el Host arma cada `argv`.
 *
 * Uso: construct_daemon [--conectar virtio|unix:RUTA|tcp:HOST:PUERTO] [--token-archivo RUTA]
 *                       [--usuario NOMBRE] [--espera-host SEGUNDOS] [--sin-apagado]
 */
#define _GNU_SOURCE

#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <netdb.h>
#include <poll.h>
#include <pty.h>
#include <pwd.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/fsuid.h>
#include <sys/ioctl.h>
#include <sys/reboot.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "json.h"

#define VERSION_AGENTE "0.1.0"
#define PROTOCOLO 1
#define LARGO_MAXIMO (1u << 20)
#define CABECERA 9
#define MAX_PROCESOS 64
#define MAX_TRANSFERENCIAS 16
#define MAX_ARGUMENTOS 256
#define BLOQUE (64 * 1024)
#define NOMBRE_PUERTO "construct-ctl"
#define TOKEN_FW_CFG "/sys/firmware/qemu_fw_cfg/by_name/opt/construct/token/raw"
#define PARCIAL ".construct-parcial"

enum tipo {
    HOLA = 0x01, BIENVENIDA = 0x02, ERROR = 0x03, LATIDO = 0x04, LATIDO_OK = 0x05,
    EJECUTAR = 0x10, INICIADO = 0x11, ENTRADA = 0x12, SALIDA = 0x13, ERRORES = 0x14,
    TERMINADO = 0x15, SENAL = 0x16, TAMANO = 0x17,
    ESCRIBIR = 0x20, DATOS = 0x21, FIN = 0x22, HECHO = 0x23, LEER = 0x24,
    APAGAR = 0x30,
};

static struct {
    const char *conectar;
    const char *token_archivo;
    const char *usuario;
    int espera_host_s;
    int apagar;
} op = {"virtio", TOKEN_FW_CFG, "estudiante", 120, 1};

/* Usuario con el que corren los procesos y las transferencias sin `root`. */
static struct {
    int cambiar; /* sólo si el agente corre como root */
    uid_t uid;
    gid_t gid;
    char nombre[64];
    char home[256];
} usr;

typedef struct {
    int usado;
    uint32_t id;
    pid_t pid;
    int pty;
    int fd_entrada;  /* tubería de entrada o maestro de la terminal (con pty, igual a fd_salida) */
    int fd_salida;
    int fd_errores;
    buffer pendiente; /* entrada que el proceso todavía no leyó */
    int cerrar_entrada;
    int terminado;
    int estado;
} proceso;

typedef struct {
    int usado;
    uint32_t id;
    int fd;
    char *ruta;
    char *parcial;
    uint64_t bytes;
} transferencia;

static int ctl = -1;
static int fd_senales = -1;
static proceso procesos[MAX_PROCESOS];
static transferencia transferencias[MAX_TRANSFERENCIAS];
static char token[256];

static void registrar(const char *formato, ...) __attribute__((format(printf, 1, 2)));
static void registrar(const char *formato, ...) {
    va_list args;
    va_start(args, formato);
    fprintf(stderr, "construct_daemon: ");
    vfprintf(stderr, formato, args);
    fputc('\n', stderr);
    va_end(args);
}

static double ahora(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

/* --- Envío de marcos ---------------------------------------------------------------------- */

static int escribir_todo(int fd, const void *datos, size_t largo) {
    const char *p = datos;
    while (largo > 0) {
        ssize_t n = write(fd, p, largo);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            if (errno == EAGAIN) {
                struct pollfd espera = {fd, POLLOUT, 0};
                poll(&espera, 1, 1000);
                continue;
            }
            return -1;
        }
        p += n;
        largo -= (size_t)n;
    }
    return 0;
}

static int enviar(uint8_t tipo, uint32_t id, const void *datos, uint32_t largo) {
    unsigned char cabecera[CABECERA];
    if (ctl < 0) {
        return -1;
    }
    cabecera[0] = tipo;
    uint32_t id_be = htonl(id), largo_be = htonl(largo);
    memcpy(cabecera + 1, &id_be, 4);
    memcpy(cabecera + 5, &largo_be, 4);
    if (escribir_todo(ctl, cabecera, CABECERA) != 0 || (largo > 0 && escribir_todo(ctl, datos, largo) != 0)) {
        close(ctl);
        ctl = -1;
        return -1;
    }
    return 0;
}

static void enviar_buffer(uint8_t tipo, uint32_t id, buffer *b) {
    enviar(tipo, id, b->datos, (uint32_t)b->largo);
    buf_liberar(b);
}

static void enviar_error(uint32_t id, const char *formato, ...) __attribute__((format(printf, 2, 3)));
static void enviar_error(uint32_t id, const char *formato, ...) {
    char mensaje[512];
    va_list args;
    va_start(args, formato);
    vsnprintf(mensaje, sizeof mensaje, formato, args);
    va_end(args);
    buffer b = {0};
    buf_texto(&b, "{\"mensaje\":");
    buf_json_texto(&b, mensaje);
    buf_texto(&b, "}");
    enviar_buffer(ERROR, id, &b);
}

/* --- Señales ------------------------------------------------------------------------------ */

static const struct {
    const char *nombre;
    int numero;
} SENALES[] = {
    {"INT", SIGINT},   {"TERM", SIGTERM}, {"KILL", SIGKILL}, {"QUIT", SIGQUIT}, {"HUP", SIGHUP},
    {"USR1", SIGUSR1}, {"USR2", SIGUSR2}, {"STOP", SIGSTOP}, {"CONT", SIGCONT}, {"SEGV", SIGSEGV},
    {"ABRT", SIGABRT}, {"FPE", SIGFPE},   {"BUS", SIGBUS},   {"ILL", SIGILL},   {"PIPE", SIGPIPE},
    {"ALRM", SIGALRM}, {"TRAP", SIGTRAP}, {"XCPU", SIGXCPU}, {"XFSZ", SIGXFSZ}, {"SYS", SIGSYS},
};

static int senal_por_nombre(const char *nombre) {
    for (size_t i = 0; i < sizeof SENALES / sizeof SENALES[0]; i++) {
        if (strcmp(SENALES[i].nombre, nombre) == 0) {
            return SENALES[i].numero;
        }
    }
    return -1;
}

static const char *nombre_de_senal(int numero) {
    for (size_t i = 0; i < sizeof SENALES / sizeof SENALES[0]; i++) {
        if (SENALES[i].numero == numero) {
            return SENALES[i].nombre;
        }
    }
    return NULL;
}

/* --- Usuario sin privilegios -------------------------------------------------------------- */

static void resolver_usuario(void) {
    if (geteuid() != 0) {
        struct passwd *pw = getpwuid(geteuid());
        usr.cambiar = 0;
        usr.uid = geteuid();
        usr.gid = getegid();
        snprintf(usr.nombre, sizeof usr.nombre, "%s", pw ? pw->pw_name : "agente");
        snprintf(usr.home, sizeof usr.home, "%s", pw ? pw->pw_dir : "/tmp");
        return;
    }
    struct passwd *pw = getpwnam(op.usuario);
    if (pw == NULL) {
        registrar("no existe el usuario %s: los procesos van a correr como root", op.usuario);
        usr.cambiar = 0;
        usr.uid = 0;
        usr.gid = 0;
        snprintf(usr.nombre, sizeof usr.nombre, "root");
        snprintf(usr.home, sizeof usr.home, "/root");
        return;
    }
    usr.cambiar = 1;
    usr.uid = pw->pw_uid;
    usr.gid = pw->pw_gid;
    snprintf(usr.nombre, sizeof usr.nombre, "%s", pw->pw_name);
    snprintf(usr.home, sizeof usr.home, "%s", pw->pw_dir);
}

/* Las operaciones de archivo sin `root` usan los permisos del usuario (setfsuid). */
static void como_usuario(int root) {
    if (usr.cambiar && !root) {
        setfsgid(usr.gid);
        setfsuid(usr.uid);
    }
}

static void como_agente(void) {
    if (usr.cambiar) {
        setfsuid(0);
        setfsgid(0);
    }
}

/* --- Procesos ----------------------------------------------------------------------------- */

static proceso *buscar_proceso(uint32_t id) {
    for (int i = 0; i < MAX_PROCESOS; i++) {
        if (procesos[i].usado && procesos[i].id == id) {
            return &procesos[i];
        }
    }
    return NULL;
}

static void cerrar_fd(int *fd) {
    if (*fd >= 0) {
        close(*fd);
        *fd = -1;
    }
}

static void liberar_proceso(proceso *p) {
    if (p->fd_entrada >= 0 && p->fd_entrada != p->fd_salida) {
        close(p->fd_entrada);
    }
    p->fd_entrada = -1;
    cerrar_fd(&p->fd_salida);
    cerrar_fd(&p->fd_errores);
    buf_liberar(&p->pendiente);
    memset(p, 0, sizeof *p);
}

static void poner_no_bloqueante(int fd) {
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
}

/* Lee lo disponible de un flujo; devuelve 0 si sigue abierto, -1 si terminó (EOF o EIO). */
static int leer_flujo(proceso *p, int *fd, uint8_t tipo) {
    char bloque[BLOQUE];
    for (;;) {
        ssize_t n = read(*fd, bloque, sizeof bloque);
        if (n > 0) {
            enviar(tipo, p->id, bloque, (uint32_t)n);
            continue;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n < 0 && errno == EAGAIN) {
            return 0;
        }
        /* EOF, o EIO del maestro de una terminal sin nadie del otro lado. */
        if (fd == &p->fd_salida && p->pty) {
            p->fd_entrada = -1;
        }
        cerrar_fd(fd);
        return -1;
    }
}

static void quizas_terminado(proceso *p) {
    if (!p->terminado || p->fd_salida >= 0 || p->fd_errores >= 0) {
        return;
    }
    buffer b = {0};
    if (WIFSIGNALED(p->estado)) {
        const char *nombre = nombre_de_senal(WTERMSIG(p->estado));
        buf_printf(&b, "{\"codigo\":%d,\"senal\":", 128 + WTERMSIG(p->estado));
        if (nombre != NULL) {
            buf_json_texto(&b, nombre);
        } else {
            buf_printf(&b, "\"%d\"", WTERMSIG(p->estado));
        }
        buf_texto(&b, "}");
    } else {
        buf_printf(&b, "{\"codigo\":%d,\"senal\":null}", WEXITSTATUS(p->estado));
    }
    enviar_buffer(TERMINADO, p->id, &b);
    liberar_proceso(p);
}

static void cosechar(void) {
    struct signalfd_siginfo info;
    while (read(fd_senales, &info, sizeof info) == (ssize_t)sizeof info) {
    }
    int estado;
    pid_t pid;
    while ((pid = waitpid(-1, &estado, WNOHANG)) > 0) {
        for (int i = 0; i < MAX_PROCESOS; i++) {
            proceso *p = &procesos[i];
            if (p->usado && p->pid == pid) {
                p->terminado = 1;
                p->estado = estado;
                /* Lo que el proceso escribió antes de terminar ya está en la tubería: se drena todo.
                 * Si un nieto sigue con la salida abierta, no se lo espera. */
                if (p->fd_salida >= 0) {
                    leer_flujo(p, &p->fd_salida, SALIDA);
                    if (p->pty) {
                        p->fd_entrada = -1;
                    }
                    cerrar_fd(&p->fd_salida);
                }
                if (p->fd_errores >= 0) {
                    leer_flujo(p, &p->fd_errores, ERRORES);
                    cerrar_fd(&p->fd_errores);
                }
                quizas_terminado(p);
            }
        }
    }
}

static void agregar_entorno(char **entorno, int *n, const char *clave, const char *valor) {
    size_t largo_clave = strlen(clave);
    char *variable = malloc(largo_clave + strlen(valor) + 2);
    if (variable == NULL) {
        return;
    }
    sprintf(variable, "%s=%s", clave, valor);
    for (int i = 0; i < *n; i++) {
        if (strncmp(entorno[i], clave, largo_clave) == 0 && entorno[i][largo_clave] == '=') {
            free(entorno[i]);
            entorno[i] = variable;
            return;
        }
    }
    if (*n < MAX_ARGUMENTOS - 1) {
        entorno[(*n)++] = variable;
        entorno[*n] = NULL;
    } else {
        free(variable);
    }
}

static void ejecutar(uint32_t id, const json_doc *doc) {
    const json_nodo *raiz = json_raiz(doc);
    const json_nodo *lista = json_campo(doc, raiz, "argv");
    char *argv[MAX_ARGUMENTOS + 1];
    char *entorno[MAX_ARGUMENTOS + 1] = {NULL};
    int argc = 0, n_entorno = 0;

    if (id == 0 || buscar_proceso(id) != NULL) {
        enviar_error(id, "el id %u ya está en uso", id);
        return;
    }
    proceso *p = NULL;
    for (int i = 0; i < MAX_PROCESOS && p == NULL; i++) {
        if (!procesos[i].usado) {
            p = &procesos[i];
        }
    }
    if (p == NULL) {
        enviar_error(id, "hay demasiados procesos corriendo (máximo %d)", MAX_PROCESOS);
        return;
    }
    if (lista == NULL || lista->tipo != JSON_LISTA) {
        enviar_error(id, "falta argv");
        return;
    }
    for (const json_nodo *a = json_primero(doc, lista); a != NULL && argc < MAX_ARGUMENTOS; a = json_siguiente(doc, a)) {
        if (a->tipo != JSON_TEXTO) {
            enviar_error(id, "argv tiene que ser una lista de textos");
            return;
        }
        argv[argc++] = a->texto;
    }
    argv[argc] = NULL;
    if (argc == 0) {
        enviar_error(id, "argv está vacío");
        return;
    }

    int root = json_bool(doc, raiz, "root", 0);
    int con_pty = json_bool(doc, raiz, "pty", 0);
    const char *cwd = json_texto(doc, raiz, "cwd", NULL);
    struct stat st;
    if (cwd == NULL) {
        cwd = stat("/workspace", &st) == 0 ? "/workspace" : usr.home;
    }
    int cambiar = usr.cambiar && !root;

    agregar_entorno(entorno, &n_entorno, "PATH", "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin");
    agregar_entorno(entorno, &n_entorno, "LANG", "C.UTF-8");
    agregar_entorno(entorno, &n_entorno, "HOME", cambiar || !usr.cambiar ? usr.home : "/root");
    agregar_entorno(entorno, &n_entorno, "USER", cambiar || !usr.cambiar ? usr.nombre : "root");
    agregar_entorno(entorno, &n_entorno, "LOGNAME", cambiar || !usr.cambiar ? usr.nombre : "root");
    if (con_pty) {
        agregar_entorno(entorno, &n_entorno, "TERM", "xterm-256color");
    }
    const json_nodo *variables = json_campo(doc, raiz, "env");
    for (const json_nodo *v = json_primero(doc, variables); v != NULL; v = json_siguiente(doc, v)) {
        if (v->tipo == JSON_TEXTO && v->clave != NULL && v->clave[0] != '\0' && strchr(v->clave, '=') == NULL) {
            agregar_entorno(entorno, &n_entorno, v->clave, v->texto);
        }
    }

    int aviso[2];
    int entrada[2] = {-1, -1}, salida[2] = {-1, -1}, errores[2] = {-1, -1};
    int maestro = -1;
    pid_t pid;
    if (pipe2(aviso, O_CLOEXEC) != 0) {
        enviar_error(id, "no se pudo crear la tubería de control: %s", strerror(errno));
        goto liberar;
    }
    if (con_pty) {
        struct winsize ws = {(unsigned short)json_entero(doc, raiz, "filas", 24),
                             (unsigned short)json_entero(doc, raiz, "columnas", 80), 0, 0};
        pid = forkpty(&maestro, NULL, NULL, &ws);
    } else {
        if (pipe2(entrada, O_CLOEXEC) != 0 || pipe2(salida, O_CLOEXEC) != 0 || pipe2(errores, O_CLOEXEC) != 0) {
            enviar_error(id, "no se pudieron crear las tuberías: %s", strerror(errno));
            close(aviso[0]);
            close(aviso[1]);
            for (int i = 0; i < 2; i++) {
                cerrar_fd(&entrada[i]);
                cerrar_fd(&salida[i]);
                cerrar_fd(&errores[i]);
            }
            goto liberar;
        }
        pid = fork();
    }
    if (pid < 0) {
        enviar_error(id, "no se pudo crear el proceso: %s", strerror(errno));
        close(aviso[0]);
        close(aviso[1]);
        goto liberar;
    }
    if (pid == 0) {
        sigset_t vacio;
        sigemptyset(&vacio);
        sigprocmask(SIG_SETMASK, &vacio, NULL);
        signal(SIGPIPE, SIG_DFL);
        if (!con_pty) {
            setsid();
            dup2(entrada[0], 0);
            dup2(salida[1], 1);
            dup2(errores[1], 2);
        }
        int error = 0;
        if (cambiar && (initgroups(usr.nombre, usr.gid) != 0 || setgid(usr.gid) != 0 || setuid(usr.uid) != 0)) {
            error = errno;
        }
        if (error == 0 && chdir(cwd) != 0 && chdir("/") != 0) {
            error = errno;
        }
        if (error == 0) {
            execvpe(argv[0], argv, entorno);
            error = errno;
        }
        if (write(aviso[1], &error, sizeof error) < 0) {
            /* Nada más que hacer: el padre verá el código 127. */
        }
        _exit(127);
    }

    close(aviso[1]);
    int error_hijo = 0;
    ssize_t leidos;
    do {
        leidos = read(aviso[0], &error_hijo, sizeof error_hijo);
    } while (leidos < 0 && errno == EINTR);
    close(aviso[0]);

    memset(p, 0, sizeof *p);
    p->usado = 1;
    p->id = id;
    p->pid = pid;
    p->pty = con_pty;
    if (con_pty) {
        p->fd_entrada = p->fd_salida = maestro;
        p->fd_errores = -1;
        fcntl(maestro, F_SETFD, FD_CLOEXEC);
    } else {
        close(entrada[0]);
        close(salida[1]);
        close(errores[1]);
        p->fd_entrada = entrada[1];
        p->fd_salida = salida[0];
        p->fd_errores = errores[0];
    }
    poner_no_bloqueante(p->fd_salida);
    if (p->fd_errores >= 0) {
        poner_no_bloqueante(p->fd_errores);
    }
    if (!con_pty) {
        poner_no_bloqueante(p->fd_entrada);
    }

    if (leidos == (ssize_t)sizeof error_hijo) {
        if (error_hijo == ENOENT) {
            enviar_error(id, "no se encontró el programa «%s»", argv[0]);
        } else {
            enviar_error(id, "no se pudo ejecutar «%s»: %s", argv[0], strerror(error_hijo));
        }
    } else {
        buffer b = {0};
        buf_printf(&b, "{\"pid\":%d}", (int)pid);
        enviar_buffer(INICIADO, id, &b);
    }

liberar:
    for (int i = 0; i < n_entorno; i++) {
        free(entorno[i]);
    }
}

static void vaciar_entrada(proceso *p) {
    while (p->pendiente.largo > 0 && p->fd_entrada >= 0) {
        ssize_t n = write(p->fd_entrada, p->pendiente.datos, p->pendiente.largo);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            if (errno == EAGAIN) {
                return;
            }
            /* El proceso cerró su entrada (EPIPE): se descarta lo pendiente. */
            p->pendiente.largo = 0;
            break;
        }
        memmove(p->pendiente.datos, p->pendiente.datos + n, p->pendiente.largo - (size_t)n);
        p->pendiente.largo -= (size_t)n;
    }
    if (p->pendiente.largo == 0 && p->cerrar_entrada && p->fd_entrada >= 0) {
        if (p->pty) {
            char eot = 4; /* Ctrl+D: fin de la entrada para la disciplina de línea */
            if (write(p->fd_entrada, &eot, 1) < 0) {
                /* La terminal ya no existe. */
            }
        } else {
            close(p->fd_entrada);
            p->fd_entrada = -1;
        }
        p->cerrar_entrada = 0;
    }
}

static void recibir_entrada(uint32_t id, const char *datos, uint32_t largo) {
    proceso *p = buscar_proceso(id);
    if (p == NULL || p->terminado) {
        return; /* El proceso ya terminó: la entrada tardía se descarta. */
    }
    if (largo == 0) {
        p->cerrar_entrada = 1;
    } else {
        buf_agregar(&p->pendiente, datos, largo);
    }
    vaciar_entrada(p);
}

static void recibir_senal(uint32_t id, const json_doc *doc) {
    proceso *p = buscar_proceso(id);
    const char *nombre = json_texto(doc, json_raiz(doc), "senal", "");
    int numero = senal_por_nombre(nombre);
    if (p == NULL) {
        enviar_error(id, "no hay un proceso con id %u", id);
    } else if (numero < 0) {
        enviar_error(id, "señal desconocida «%s»", nombre);
    } else if (!p->terminado) {
        kill(-p->pid, numero); /* a todo el grupo: también a lo que lanzó (nostromo, tetsuo…) */
    }
}

static void recibir_tamano(uint32_t id, const json_doc *doc) {
    proceso *p = buscar_proceso(id);
    if (p != NULL && p->pty && p->fd_salida >= 0) {
        struct winsize ws = {(unsigned short)json_entero(doc, json_raiz(doc), "filas", 24),
                             (unsigned short)json_entero(doc, json_raiz(doc), "columnas", 80), 0, 0};
        ioctl(p->fd_salida, TIOCSWINSZ, &ws);
    }
}

static void matar_todos(void) {
    for (int i = 0; i < MAX_PROCESOS; i++) {
        if (procesos[i].usado) {
            if (!procesos[i].terminado) {
                kill(-procesos[i].pid, SIGKILL);
                waitpid(procesos[i].pid, NULL, 0);
            }
            liberar_proceso(&procesos[i]);
        }
    }
}

/* --- Transferencias ----------------------------------------------------------------------- */

static char *ruta_absoluta(const char *ruta) {
    char *resultado;
    if (ruta[0] == '/') {
        resultado = strdup(ruta);
    } else if (asprintf(&resultado, "/workspace/%s", ruta) < 0) {
        resultado = NULL;
    }
    return resultado;
}

static int crear_padres(const char *ruta) {
    char *copia = strdup(ruta);
    if (copia == NULL) {
        return -1;
    }
    for (char *p = copia + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            if (mkdir(copia, 0755) != 0 && errno != EEXIST) {
                free(copia);
                return -1;
            }
            *p = '/';
        }
    }
    free(copia);
    return 0;
}

static transferencia *buscar_transferencia(uint32_t id) {
    for (int i = 0; i < MAX_TRANSFERENCIAS; i++) {
        if (transferencias[i].usado && transferencias[i].id == id) {
            return &transferencias[i];
        }
    }
    return NULL;
}

static void liberar_transferencia(transferencia *t, int borrar) {
    if (t->fd >= 0) {
        close(t->fd);
    }
    if (borrar && t->parcial != NULL) {
        unlink(t->parcial);
    }
    free(t->ruta);
    free(t->parcial);
    memset(t, 0, sizeof *t);
    t->fd = -1;
}

static void escribir_archivo(uint32_t id, const json_doc *doc) {
    const json_nodo *raiz = json_raiz(doc);
    const char *ruta = json_texto(doc, raiz, "ruta", NULL);
    transferencia *t = NULL;
    if (ruta == NULL || ruta[0] == '\0') {
        enviar_error(id, "falta la ruta");
        return;
    }
    if (buscar_transferencia(id) != NULL) {
        enviar_error(id, "el id %u ya está en uso", id);
        return;
    }
    for (int i = 0; i < MAX_TRANSFERENCIAS && t == NULL; i++) {
        if (!transferencias[i].usado) {
            t = &transferencias[i];
        }
    }
    if (t == NULL) {
        enviar_error(id, "hay demasiadas transferencias abiertas");
        return;
    }
    memset(t, 0, sizeof *t);
    t->usado = 1;
    t->id = id;
    t->fd = -1;
    t->ruta = ruta_absoluta(ruta);
    if (t->ruta == NULL || asprintf(&t->parcial, "%s%s", t->ruta, PARCIAL) < 0) {
        t->parcial = NULL;
        enviar_error(id, "sin memoria");
        liberar_transferencia(t, 0);
        return;
    }
    como_usuario(json_bool(doc, raiz, "root", 0));
    if (crear_padres(t->ruta) == 0) {
        t->fd = open(t->parcial, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC,
                     (mode_t)json_entero(doc, raiz, "modo", 0644) & 07777);
    }
    int error = errno;
    como_agente();
    if (t->fd < 0) {
        enviar_error(id, "no se pudo escribir %s: %s", t->ruta, strerror(error));
        liberar_transferencia(t, 0);
    }
}

static void recibir_datos(uint32_t id, const char *datos, uint32_t largo) {
    transferencia *t = buscar_transferencia(id);
    if (t == NULL) {
        return;
    }
    if (escribir_todo(t->fd, datos, largo) != 0) {
        enviar_error(id, "no se pudo escribir %s: %s", t->ruta, strerror(errno));
        liberar_transferencia(t, 1);
        return;
    }
    t->bytes += largo;
}

static void recibir_fin(uint32_t id) {
    transferencia *t = buscar_transferencia(id);
    if (t == NULL) {
        enviar_error(id, "no hay una transferencia con id %u", id);
        return;
    }
    if (fsync(t->fd) != 0 || close(t->fd) != 0) {
        t->fd = -1;
        enviar_error(id, "no se pudo terminar de escribir %s: %s", t->ruta, strerror(errno));
        liberar_transferencia(t, 1);
        return;
    }
    t->fd = -1;
    if (rename(t->parcial, t->ruta) != 0) {
        enviar_error(id, "no se pudo reemplazar %s: %s", t->ruta, strerror(errno));
        liberar_transferencia(t, 1);
        return;
    }
    buffer b = {0};
    buf_printf(&b, "{\"bytes\":%llu}", (unsigned long long)t->bytes);
    enviar_buffer(HECHO, id, &b);
    liberar_transferencia(t, 0);
}

static void leer_archivo(uint32_t id, const json_doc *doc) {
    const json_nodo *raiz = json_raiz(doc);
    const char *ruta = json_texto(doc, raiz, "ruta", NULL);
    if (ruta == NULL || ruta[0] == '\0') {
        enviar_error(id, "falta la ruta");
        return;
    }
    char *absoluta = ruta_absoluta(ruta);
    if (absoluta == NULL) {
        enviar_error(id, "sin memoria");
        return;
    }
    como_usuario(json_bool(doc, raiz, "root", 0));
    int fd = open(absoluta, O_RDONLY | O_CLOEXEC);
    int error = errno;
    como_agente();
    if (fd < 0) {
        enviar_error(id, "no se pudo leer %s: %s", absoluta, strerror(error));
        free(absoluta);
        return;
    }
    char bloque[BLOQUE * 4];
    unsigned long long total = 0;
    ssize_t n;
    while ((n = read(fd, bloque, sizeof bloque)) != 0) {
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            enviar_error(id, "no se pudo leer %s: %s", absoluta, strerror(errno));
            close(fd);
            free(absoluta);
            return;
        }
        if (enviar(DATOS, id, bloque, (uint32_t)n) != 0) {
            break;
        }
        total += (unsigned long long)n;
    }
    close(fd);
    free(absoluta);
    buffer b = {0};
    buf_printf(&b, "{\"bytes\":%llu}", total);
    enviar_buffer(FIN, id, &b);
}

static void cerrar_transferencias(void) {
    for (int i = 0; i < MAX_TRANSFERENCIAS; i++) {
        if (transferencias[i].usado) {
            liberar_transferencia(&transferencias[i], 1);
        }
    }
}

/* --- Apagado ------------------------------------------------------------------------------ */

static void apagar(void) {
    matar_todos();
    cerrar_transferencias();
    sync();
    if (!op.apagar) {
        registrar("apagado pedido (sin --apagar: el agente termina)");
        exit(0);
    }
    registrar("apagando la VM");
    pid_t pid = fork();
    if (pid == 0) {
        execl("/sbin/poweroff", "poweroff", (char *)NULL);
        _exit(1);
    }
    int estado = 1;
    if (pid > 0) {
        waitpid(pid, &estado, 0);
    }
    if (pid < 0 || !WIFEXITED(estado) || WEXITSTATUS(estado) != 0) {
        reboot(RB_POWER_OFF);
    }
    pause();
    exit(0);
}

/* --- Conexión ----------------------------------------------------------------------------- */

static int buscar_puerto_virtio(char *dispositivo, size_t largo) {
    DIR *dir = opendir("/sys/class/virtio-ports");
    struct dirent *e;
    int encontrado = 0;
    if (dir == NULL) {
        return -1;
    }
    while (!encontrado && (e = readdir(dir)) != NULL) {
        char ruta[512], nombre[64] = "";
        if (e->d_name[0] == '.') {
            continue;
        }
        snprintf(ruta, sizeof ruta, "/sys/class/virtio-ports/%s/name", e->d_name);
        FILE *f = fopen(ruta, "r");
        if (f != NULL) {
            if (fgets(nombre, sizeof nombre, f) != NULL) {
                nombre[strcspn(nombre, "\n")] = '\0';
            }
            fclose(f);
        }
        if (strcmp(nombre, NOMBRE_PUERTO) == 0) {
            snprintf(dispositivo, largo, "/dev/%s", e->d_name);
            encontrado = 1;
        }
    }
    closedir(dir);
    return encontrado ? 0 : -1;
}

/* Intenta conectar una vez; devuelve el descriptor o -1. */
static int conectar(void) {
    int fd = -1;
    if (strncmp(op.conectar, "unix:", 5) == 0) {
        struct sockaddr_un dir = {.sun_family = AF_UNIX};
        snprintf(dir.sun_path, sizeof dir.sun_path, "%s", op.conectar + 5);
        fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (fd >= 0 && connect(fd, (struct sockaddr *)&dir, sizeof dir) != 0) {
            close(fd);
            fd = -1;
        }
    } else if (strncmp(op.conectar, "tcp:", 4) == 0) {
        char host[256];
        snprintf(host, sizeof host, "%s", op.conectar + 4);
        char *puerto = strrchr(host, ':');
        struct addrinfo pista = {.ai_socktype = SOCK_STREAM}, *res = NULL;
        if (puerto == NULL) {
            return -1;
        }
        *puerto++ = '\0';
        if (getaddrinfo(host, puerto, &pista, &res) == 0) {
            fd = socket(res->ai_family, SOCK_STREAM | SOCK_CLOEXEC, 0);
            if (fd >= 0 && connect(fd, res->ai_addr, res->ai_addrlen) != 0) {
                close(fd);
                fd = -1;
            }
            freeaddrinfo(res);
        }
    } else {
        char dispositivo[300];
        if (buscar_puerto_virtio(dispositivo, sizeof dispositivo) != 0) {
            return -1;
        }
        fd = open(dispositivo, O_RDWR | O_CLOEXEC);
        if (fd >= 0) {
            /* Con el Host desconectado, el puerto da POLLHUP: se espera a que alguien escuche. */
            struct pollfd espera = {fd, POLLOUT, 0};
            if (poll(&espera, 1, 1000) <= 0 || (espera.revents & POLLHUP)) {
                close(fd);
                fd = -1;
            }
        }
    }
    return fd;
}

static int leer_token(void) {
    FILE *f = fopen(op.token_archivo, "r");
    if (f == NULL) {
        registrar("no se pudo leer el token de %s: %s", op.token_archivo, strerror(errno));
        return -1;
    }
    size_t n = fread(token, 1, sizeof token - 1, f);
    fclose(f);
    token[n] = '\0';
    token[strcspn(token, "\r\n \t")] = '\0';
    return token[0] != '\0' ? 0 : -1;
}

static const char *arquitectura(void) {
    static struct utsname u;
    if (uname(&u) != 0) {
        return "desconocida";
    }
    if (strcmp(u.machine, "arm64") == 0) {
        return "aarch64";
    }
    return u.machine;
}

/* Un marco recibido completo. */
static int atender(uint8_t tipo, uint32_t id, const char *datos, uint32_t largo) {
    json_doc doc = {0};
    int es_json = tipo == EJECUTAR || tipo == SENAL || tipo == TAMANO || tipo == ESCRIBIR || tipo == LEER;
    if (es_json && (json_parsear(datos, largo, &doc) != 0 || json_raiz(&doc)->tipo != JSON_OBJETO)) {
        json_liberar(&doc);
        enviar_error(id, "el mensaje 0x%02x no es un objeto JSON válido", tipo);
        return 0;
    }
    switch (tipo) {
    case LATIDO: enviar(LATIDO_OK, 0, NULL, 0); break;
    case EJECUTAR: ejecutar(id, &doc); break;
    case ENTRADA: recibir_entrada(id, datos, largo); break;
    case SENAL: recibir_senal(id, &doc); break;
    case TAMANO: recibir_tamano(id, &doc); break;
    case ESCRIBIR: escribir_archivo(id, &doc); break;
    case DATOS: recibir_datos(id, datos, largo); break;
    case FIN: recibir_fin(id); break;
    case LEER: leer_archivo(id, &doc); break;
    case APAGAR: json_liberar(&doc); apagar(); break;
    case ERROR: break;
    default: enviar_error(id, "mensaje desconocido 0x%02x", tipo); break;
    }
    json_liberar(&doc);
    return 0;
}

/* Saca los marcos completos de `entrada`; -1 si hay uno inválido. */
static int procesar_marcos(buffer *entrada, int *bienvenido, int *latido_s) {
    size_t inicio = 0;
    while (entrada->largo - inicio >= CABECERA) {
        unsigned char *c = (unsigned char *)entrada->datos + inicio;
        uint32_t id, largo;
        memcpy(&id, c + 1, 4);
        memcpy(&largo, c + 5, 4);
        id = ntohl(id);
        largo = ntohl(largo);
        if (largo > LARGO_MAXIMO) {
            registrar("marco de %u bytes: se corta la conexión", largo);
            return -1;
        }
        if (entrada->largo - inicio < CABECERA + (size_t)largo) {
            break;
        }
        const char *datos = (const char *)c + CABECERA;
        if (!*bienvenido) {
            json_doc doc = {0};
            if (c[0] != BIENVENIDA || json_parsear(datos, largo, &doc) != 0 ||
                json_entero(&doc, json_raiz(&doc), "protocolo", -1) != PROTOCOLO) {
                json_liberar(&doc);
                registrar("el Host no respondió BIENVENIDA con el protocolo %d", PROTOCOLO);
                return -1;
            }
            *latido_s = (int)json_entero(&doc, json_raiz(&doc), "latido_s", 5);
            json_liberar(&doc);
            *bienvenido = 1;
            registrar("sesión iniciada (latido cada %d s)", *latido_s);
        } else {
            atender(c[0], id, datos, largo);
        }
        inicio += CABECERA + largo;
    }
    memmove(entrada->datos, entrada->datos + inicio, entrada->largo - inicio);
    entrada->largo -= inicio;
    return 0;
}

/* Devuelve 1 si el Host aceptó la sesión (BIENVENIDA), 0 si no. */
static int sesion(void) {
    buffer hola = {0}, entrada = {0};
    buf_printf(&hola, "{\"protocolo\":%d,\"token\":", PROTOCOLO);
    buf_json_texto(&hola, token);
    buf_texto(&hola, ",\"agente\":\"" VERSION_AGENTE "\",\"arquitectura\":");
    buf_json_texto(&hola, arquitectura());
    buf_texto(&hola, "}");
    enviar_buffer(HOLA, 0, &hola);

    int bienvenido = 0, latido_s = 5;
    double ultimo = ahora();
    while (ctl >= 0) {
        struct pollfd fds[2 + MAX_PROCESOS * 2];
        proceso *duenos[2 + MAX_PROCESOS * 2];
        int n = 0;
        fds[n++] = (struct pollfd){ctl, POLLIN, 0};
        fds[n++] = (struct pollfd){fd_senales, POLLIN, 0};
        for (int i = 0; i < MAX_PROCESOS; i++) {
            proceso *p = &procesos[i];
            if (!p->usado) {
                continue;
            }
            if (p->fd_salida >= 0) {
                short eventos = POLLIN;
                if (p->pty && p->pendiente.largo > 0) {
                    eventos |= POLLOUT;
                }
                duenos[n] = p;
                fds[n++] = (struct pollfd){p->fd_salida, eventos, 0};
            }
            if (p->fd_errores >= 0) {
                duenos[n] = p;
                fds[n++] = (struct pollfd){p->fd_errores, POLLIN, 0};
            }
            if (!p->pty && p->fd_entrada >= 0 && p->pendiente.largo > 0) {
                duenos[n] = p;
                fds[n++] = (struct pollfd){p->fd_entrada, POLLOUT, 0};
            }
        }
        int listos = poll(fds, (nfds_t)n, 1000);
        if (listos < 0 && errno != EINTR) {
            registrar("poll: %s", strerror(errno));
            break;
        }
        if (bienvenido && ahora() - ultimo > 3.0 * latido_s) {
            registrar("el Host dejó de mandar latidos");
            break;
        }
        if (!bienvenido && ahora() - ultimo > 10.0) {
            registrar("el Host no respondió al HOLA");
            break;
        }
        if (listos <= 0) {
            continue;
        }
        /* Primero los procesos y después los mensajes del Host: un EJECUTAR nuevo podría reusar el
         * lugar y los descriptores de un proceso que terminó en esta misma vuelta. */
        for (int i = 2; i < n; i++) {
            proceso *p = duenos[i];
            if (!fds[i].revents || !p->usado) {
                continue;
            }
            if (fds[i].fd == p->fd_entrada && (fds[i].revents & POLLOUT)) {
                vaciar_entrada(p);
            }
            if (fds[i].revents & (POLLIN | POLLHUP | POLLERR)) {
                if (fds[i].fd == p->fd_salida) {
                    leer_flujo(p, &p->fd_salida, SALIDA);
                } else if (fds[i].fd == p->fd_errores) {
                    leer_flujo(p, &p->fd_errores, ERRORES);
                }
            }
            quizas_terminado(p);
        }
        if (fds[1].revents & POLLIN) {
            cosechar();
        }
        if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            char bloque[BLOQUE];
            ssize_t leidos = read(ctl, bloque, sizeof bloque);
            if (leidos <= 0 && !(leidos < 0 && (errno == EINTR || errno == EAGAIN))) {
                registrar("el Host cerró la conexión");
                break;
            }
            if (leidos > 0) {
                ultimo = ahora();
                buf_agregar(&entrada, bloque, (size_t)leidos);
                if (procesar_marcos(&entrada, &bienvenido, &latido_s) != 0) {
                    break;
                }
            }
        }
        if (fds[1].revents & POLLIN) {
            cosechar();
        }
    }
    buf_liberar(&entrada);
    if (ctl >= 0) {
        close(ctl);
        ctl = -1;
    }
    matar_todos();
    cerrar_transferencias();
    return bienvenido;
}

static void uso(void) {
    fprintf(stderr, "uso: construct_daemon [--conectar virtio|unix:RUTA|tcp:HOST:PUERTO] [--token-archivo RUTA]\n"
                    "                        [--usuario NOMBRE] [--espera-host SEGUNDOS] [--sin-apagado]\n");
    exit(2);
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--conectar") == 0 && i + 1 < argc) {
            op.conectar = argv[++i];
        } else if (strcmp(argv[i], "--token-archivo") == 0 && i + 1 < argc) {
            op.token_archivo = argv[++i];
        } else if (strcmp(argv[i], "--usuario") == 0 && i + 1 < argc) {
            op.usuario = argv[++i];
        } else if (strcmp(argv[i], "--espera-host") == 0 && i + 1 < argc) {
            op.espera_host_s = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--sin-apagado") == 0) {
            op.apagar = 0;
        } else if (strcmp(argv[i], "--version") == 0) {
            printf("construct_daemon %s (protocolo %d)\n", VERSION_AGENTE, PROTOCOLO);
            return 0;
        } else {
            uso();
        }
    }
    signal(SIGPIPE, SIG_IGN);
    sigset_t hijos;
    sigemptyset(&hijos);
    sigaddset(&hijos, SIGCHLD);
    sigprocmask(SIG_BLOCK, &hijos, NULL);
    fd_senales = signalfd(-1, &hijos, SFD_NONBLOCK | SFD_CLOEXEC);
    if (fd_senales < 0) {
        registrar("signalfd: %s", strerror(errno));
        return 1;
    }
    for (int i = 0; i < MAX_TRANSFERENCIAS; i++) {
        transferencias[i].fd = -1;
    }
    resolver_usuario();

    double sin_host_desde = ahora();
    for (;;) {
        if (token[0] == '\0' && leer_token() != 0) {
            /* El módulo qemu_fw_cfg puede tardar en aparecer al arrancar. */
        } else if ((ctl = conectar()) >= 0) {
            registrar("conectado a %s", op.conectar);
            /* Sólo una sesión aceptada cuenta como Host presente: uno que conecta pero nunca
             * responde (o rechaza el token) no mantiene viva a la VM. */
            if (sesion()) {
                sin_host_desde = ahora();
            }
        }
        if (op.espera_host_s > 0 && ahora() - sin_host_desde > op.espera_host_s) {
            registrar("sin Host durante %d s", op.espera_host_s);
            if (!op.apagar) {
                return 3;
            }
            apagar();
        }
        sleep(1);
    }
}
