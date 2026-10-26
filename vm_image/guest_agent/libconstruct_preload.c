/* libconstruct_preload: interposición ligera de malloc/free/fopen/fclose (SPEC §4, RF-06, RF-07).
 *
 * Se carga con LD_PRELOAD en `construct run` (nunca junto con tetsuo: ASan interpone lo mismo). Anota
 * cada bloque del heap y cada archivo abierto con quién lo pidió (__builtin_return_address(0)) y, al
 * terminar el programa con normalidad, escribe en $CONSTRUCT_INFORME un JSON con lo que quedó sin
 * liberar o sin cerrar. El Host traduce las direcciones a archivo:línea con addr2line y lo muestra
 * como hallazgos de telemetría (core/telemetry.py).
 */
#define _GNU_SOURCE

#include <dlfcn.h>
#include <fcntl.h>
#include <link.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define CAPACIDAD_HEAP (1u << 16)
#define MAX_ARCHIVOS 256
#define MAX_SITIOS 10

typedef struct {
    void *puntero; /* NULL: libre; BORRADO: lugar reutilizable */
    size_t bytes;
    void *llamador;
} bloque;

typedef struct {
    FILE *archivo;
    char ruta[160];
    char modo[8];
    void *llamador;
} archivo_abierto;

#define BORRADO ((void *)1)

static void *(*real_malloc)(size_t);
static void (*real_free)(void *);
static void *(*real_calloc)(size_t, size_t);
static void *(*real_realloc)(void *, size_t);
static FILE *(*real_fopen)(const char *, const char *);
static FILE *(*real_fopen64)(const char *, const char *);
static int (*real_fclose)(FILE *);

/* dlsym puede pedir memoria antes de que existan los punteros reales: sale de acá. */
static char arena[16384] __attribute__((aligned(16)));
static size_t usado_arena;
static int resolviendo;

static bloque heap[CAPACIDAD_HEAP];
static size_t bloques_vivos;
static int desbordado;
static archivo_abierto archivos[MAX_ARCHIVOS];
static pthread_mutex_t candado = PTHREAD_MUTEX_INITIALIZER;
static __thread int dentro;

/* dlsym devuelve void*: ISO C no deja convertirlo a puntero a función, se copia (modismo de POSIX). */
static void resolver_simbolo(void *destino, size_t largo, const char *nombre) {
    void *simbolo = dlsym(RTLD_NEXT, nombre);
    memcpy(destino, &simbolo, largo);
}

static void resolver(void) {
    if (real_malloc != NULL || resolviendo) {
        return;
    }
    resolviendo = 1;
    resolver_simbolo(&real_malloc, sizeof real_malloc, "malloc");
    resolver_simbolo(&real_free, sizeof real_free, "free");
    resolver_simbolo(&real_calloc, sizeof real_calloc, "calloc");
    resolver_simbolo(&real_realloc, sizeof real_realloc, "realloc");
    resolver_simbolo(&real_fopen, sizeof real_fopen, "fopen");
    resolver_simbolo(&real_fopen64, sizeof real_fopen64, "fopen64");
    resolver_simbolo(&real_fclose, sizeof real_fclose, "fclose");
    resolviendo = 0;
}

static void *de_la_arena(size_t bytes) {
    size_t alineado = (bytes + 15) & ~(size_t)15;
    if (usado_arena + alineado > sizeof arena) {
        return NULL;
    }
    void *p = arena + usado_arena;
    usado_arena += alineado;
    return p;
}

static int es_de_la_arena(const void *p) {
    return (const char *)p >= arena && (const char *)p < arena + sizeof arena;
}

static size_t posicion(const void *p) {
    uintptr_t x = (uintptr_t)p;
    x ^= x >> 17;
    x *= 0xed5ad4bbu;
    x ^= x >> 11;
    return (size_t)x & (CAPACIDAD_HEAP - 1);
}

static void anotar(void *p, size_t bytes, void *llamador) {
    if (p == NULL || dentro) {
        return;
    }
    pthread_mutex_lock(&candado);
    if (bloques_vivos * 4 >= CAPACIDAD_HEAP * 3) {
        desbordado = 1;
    } else {
        size_t i = posicion(p);
        while (heap[i].puntero != NULL && heap[i].puntero != BORRADO) {
            i = (i + 1) & (CAPACIDAD_HEAP - 1);
        }
        heap[i] = (bloque){p, bytes, llamador};
        bloques_vivos++;
    }
    pthread_mutex_unlock(&candado);
}

static void olvidar(void *p) {
    if (p == NULL || dentro) {
        return;
    }
    pthread_mutex_lock(&candado);
    for (size_t i = posicion(p), vueltas = 0; heap[i].puntero != NULL && vueltas < CAPACIDAD_HEAP;
         i = (i + 1) & (CAPACIDAD_HEAP - 1), vueltas++) {
        if (heap[i].puntero == p) {
            heap[i].puntero = BORRADO;
            bloques_vivos--;
            break;
        }
    }
    pthread_mutex_unlock(&candado);
}

void *malloc(size_t bytes) {
    resolver();
    if (real_malloc == NULL) {
        return de_la_arena(bytes);
    }
    void *p = real_malloc(bytes);
    anotar(p, bytes, __builtin_return_address(0));
    return p;
}

void *calloc(size_t cantidad, size_t tamano) {
    resolver();
    if (real_calloc == NULL) {
        return de_la_arena(cantidad * tamano); /* la arena es estática: ya está en cero */
    }
    void *p = real_calloc(cantidad, tamano);
    anotar(p, cantidad * tamano, __builtin_return_address(0));
    return p;
}

void *realloc(void *anterior, size_t bytes) {
    resolver();
    if (anterior != NULL && es_de_la_arena(anterior)) {
        void *p = malloc(bytes);
        if (p != NULL) {
            memcpy(p, anterior, bytes);
        }
        return p;
    }
    if (real_realloc == NULL) {
        return de_la_arena(bytes);
    }
    void *p = real_realloc(anterior, bytes);
    if (p != NULL || bytes == 0) {
        olvidar(anterior);
        anotar(p, bytes, __builtin_return_address(0));
    }
    return p;
}

void free(void *p) {
    if (p == NULL || es_de_la_arena(p)) {
        return;
    }
    resolver();
    olvidar(p);
    real_free(p);
}

static void anotar_archivo(FILE *f, const char *ruta, const char *modo, void *llamador) {
    if (f == NULL || dentro) {
        return;
    }
    pthread_mutex_lock(&candado);
    for (int i = 0; i < MAX_ARCHIVOS; i++) {
        if (archivos[i].archivo == NULL) {
            archivos[i].archivo = f;
            snprintf(archivos[i].ruta, sizeof archivos[i].ruta, "%s", ruta);
            snprintf(archivos[i].modo, sizeof archivos[i].modo, "%s", modo);
            archivos[i].llamador = llamador;
            break;
        }
    }
    pthread_mutex_unlock(&candado);
}

FILE *fopen(const char *ruta, const char *modo) {
    resolver();
    FILE *f = real_fopen(ruta, modo);
    anotar_archivo(f, ruta, modo, __builtin_return_address(0));
    return f;
}

FILE *fopen64(const char *ruta, const char *modo) {
    resolver();
    FILE *f = (real_fopen64 != NULL ? real_fopen64 : real_fopen)(ruta, modo);
    anotar_archivo(f, ruta, modo, __builtin_return_address(0));
    return f;
}

int fclose(FILE *f) {
    resolver();
    pthread_mutex_lock(&candado);
    for (int i = 0; i < MAX_ARCHIVOS; i++) {
        if (archivos[i].archivo == f) {
            archivos[i].archivo = NULL;
            break;
        }
    }
    pthread_mutex_unlock(&candado);
    return real_fclose(f);
}

/* --- Informe al terminar (sin pedir memoria: todo con buffers propios y write) ------------------ */

typedef struct {
    char texto[65536];
    size_t largo;
} salida;

static void poner(salida *s, const char *texto) {
    size_t n = strlen(texto);
    if (s->largo + n < sizeof s->texto) {
        memcpy(s->texto + s->largo, texto, n);
        s->largo += n;
    }
}

static void poner_texto_json(salida *s, const char *texto) {
    char c[8];
    poner(s, "\"");
    for (const unsigned char *p = (const unsigned char *)texto; *p; p++) {
        if (*p == '"' || *p == '\\') {
            c[0] = '\\';
            c[1] = (char)*p;
            c[2] = '\0';
        } else if (*p < 0x20) {
            snprintf(c, sizeof c, "\\u%04x", *p);
        } else {
            c[0] = (char)*p;
            c[1] = '\0';
        }
        poner(s, c);
    }
    poner(s, "\"");
}

static void poner_llamador(salida *s, void *llamador) {
    Dl_info info;
    struct link_map *mapa = NULL;
    char numero[64];
    /* l_addr es el desplazamiento de carga (PIE): dirección - l_addr es la que entiende addr2line. */
    if (llamador != NULL && dladdr1(llamador, &info, (void **)&mapa, RTLD_DL_LINKMAP) && info.dli_fname != NULL) {
        poner(s, "{\"modulo\":");
        poner_texto_json(s, info.dli_fname[0] != '\0' ? info.dli_fname : "/proc/self/exe");
        uintptr_t base = mapa != NULL ? (uintptr_t)mapa->l_addr : (uintptr_t)info.dli_fbase;
        /* -1: la dirección de retorno es la instrucción siguiente a la llamada */
        snprintf(numero, sizeof numero, ",\"offset\":\"0x%lx\"}", (unsigned long)((uintptr_t)llamador - base - 1));
        poner(s, numero);
    } else {
        poner(s, "null");
    }
}

typedef struct {
    void *llamador;
    size_t bloques;
    size_t bytes;
} sitio;

__attribute__((destructor)) static void informar(void) {
    const char *destino = getenv("CONSTRUCT_INFORME");
    static salida s;
    static sitio sitios[512];
    size_t n_sitios = 0, total_bloques = 0, total_bytes = 0;
    char numero[128];
    int primero = 1;

    if (destino == NULL || destino[0] == '\0') {
        return;
    }
    dentro = 1;
    pthread_mutex_lock(&candado);
    poner(&s, "{\"archivos_abiertos\":[");
    for (int i = 0; i < MAX_ARCHIVOS; i++) {
        if (archivos[i].archivo == NULL) {
            continue;
        }
        poner(&s, primero ? "{\"ruta\":" : ",{\"ruta\":");
        primero = 0;
        poner_texto_json(&s, archivos[i].ruta);
        poner(&s, ",\"modo\":");
        poner_texto_json(&s, archivos[i].modo);
        poner(&s, ",\"llamador\":");
        poner_llamador(&s, archivos[i].llamador);
        poner(&s, "}");
    }
    for (size_t i = 0; i < CAPACIDAD_HEAP; i++) {
        if (heap[i].puntero == NULL || heap[i].puntero == BORRADO) {
            continue;
        }
        total_bloques++;
        total_bytes += heap[i].bytes;
        size_t j = 0;
        while (j < n_sitios && sitios[j].llamador != heap[i].llamador) {
            j++;
        }
        if (j == n_sitios && n_sitios < sizeof sitios / sizeof sitios[0]) {
            sitios[n_sitios++] = (sitio){heap[i].llamador, 0, 0};
        }
        if (j < n_sitios) {
            sitios[j].bloques++;
            sitios[j].bytes += heap[i].bytes;
        }
    }
    snprintf(numero, sizeof numero, "],\"heap\":{\"bloques\":%zu,\"bytes\":%zu,\"desbordado\":%s,\"sitios\":[",
             total_bloques, total_bytes, desbordado ? "true" : "false");
    poner(&s, numero);
    for (int k = 0; k < MAX_SITIOS && n_sitios > 0; k++) { /* los que más bytes dejaron, de mayor a menor */
        size_t mayor = 0;
        for (size_t j = 1; j < n_sitios; j++) {
            if (sitios[j].bytes > sitios[mayor].bytes) {
                mayor = j;
            }
        }
        if (sitios[mayor].bloques == 0) {
            break;
        }
        snprintf(numero, sizeof numero, "%s{\"bloques\":%zu,\"bytes\":%zu,\"llamador\":", k ? "," : "",
                 sitios[mayor].bloques, sitios[mayor].bytes);
        poner(&s, numero);
        poner_llamador(&s, sitios[mayor].llamador);
        poner(&s, "}");
        sitios[mayor].bloques = 0;
        sitios[mayor].bytes = 0;
    }
    poner(&s, "]}}\n");
    pthread_mutex_unlock(&candado);

    int fd = open(destino, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd >= 0) {
        for (size_t hecho = 0; hecho < s.largo;) {
            ssize_t w = write(fd, s.texto + hecho, s.largo - hecho);
            if (w <= 0) {
                break;
            }
            hecho += (size_t)w;
        }
        close(fd);
    }
}
