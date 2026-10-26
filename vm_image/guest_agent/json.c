#include "json.h"

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PROFUNDIDAD_MAXIMA 32

typedef struct {
    const char *p;
    const char *fin;
    json_doc *doc;
} lector;

static int nuevo_nodo(json_doc *doc, json_tipo tipo) {
    if (doc->cantidad == doc->capacidad) {
        int capacidad = doc->capacidad ? doc->capacidad * 2 : 16;
        json_nodo *nodos = realloc(doc->nodos, (size_t)capacidad * sizeof *nodos);
        if (nodos == NULL) {
            return -1;
        }
        doc->nodos = nodos;
        doc->capacidad = capacidad;
    }
    json_nodo *n = &doc->nodos[doc->cantidad];
    memset(n, 0, sizeof *n);
    n->tipo = tipo;
    n->primero = -1;
    n->siguiente = -1;
    return doc->cantidad++;
}

static void saltear_espacios(lector *l) {
    while (l->p < l->fin && (*l->p == ' ' || *l->p == '\t' || *l->p == '\n' || *l->p == '\r')) {
        l->p++;
    }
}

static int hex4(const char *p, uint32_t *valor) {
    *valor = 0;
    for (int i = 0; i < 4; i++) {
        char c = p[i];
        *valor <<= 4;
        if (c >= '0' && c <= '9') {
            *valor |= (uint32_t)(c - '0');
        } else if (c >= 'a' && c <= 'f') {
            *valor |= (uint32_t)(c - 'a' + 10);
        } else if (c >= 'A' && c <= 'F') {
            *valor |= (uint32_t)(c - 'A' + 10);
        } else {
            return -1;
        }
    }
    return 0;
}

static void utf8(buffer *b, uint32_t cp) {
    char s[4];
    size_t n;
    if (cp < 0x80) {
        s[0] = (char)cp;
        n = 1;
    } else if (cp < 0x800) {
        s[0] = (char)(0xC0 | (cp >> 6));
        s[1] = (char)(0x80 | (cp & 0x3F));
        n = 2;
    } else if (cp < 0x10000) {
        s[0] = (char)(0xE0 | (cp >> 12));
        s[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        s[2] = (char)(0x80 | (cp & 0x3F));
        n = 3;
    } else {
        s[0] = (char)(0xF0 | (cp >> 18));
        s[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        s[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
        s[3] = (char)(0x80 | (cp & 0x3F));
        n = 4;
    }
    buf_agregar(b, s, n);
}

/* Lee un texto entre comillas; deja el resultado en *salida (malloc) y su largo en *largo. */
static int leer_texto(lector *l, char **salida, size_t *largo) {
    buffer b = {0};
    if (l->p >= l->fin || *l->p != '"') {
        return -1;
    }
    l->p++;
    while (l->p < l->fin && *l->p != '"') {
        unsigned char c = (unsigned char)*l->p++;
        if (c < 0x20) {
            buf_liberar(&b);
            return -1;
        }
        if (c != '\\') {
            buf_agregar(&b, &c, 1);
            continue;
        }
        if (l->p >= l->fin) {
            buf_liberar(&b);
            return -1;
        }
        char e = *l->p++;
        const char *simple = strchr("\"\\/bfnrt", e);
        if (simple != NULL && e != '\0') {
            static const char reemplazos[] = "\"\\/\b\f\n\r\t";
            buf_agregar(&b, &reemplazos[simple - "\"\\/bfnrt"], 1);
        } else if (e == 'u') {
            uint32_t cp;
            if (l->fin - l->p < 4 || hex4(l->p, &cp) != 0) {
                buf_liberar(&b);
                return -1;
            }
            l->p += 4;
            if (cp >= 0xD800 && cp <= 0xDBFF) {
                uint32_t bajo;
                if (l->fin - l->p < 6 || l->p[0] != '\\' || l->p[1] != 'u' || hex4(l->p + 2, &bajo) != 0 ||
                    bajo < 0xDC00 || bajo > 0xDFFF) {
                    buf_liberar(&b);
                    return -1;
                }
                l->p += 6;
                cp = 0x10000 + ((cp - 0xD800) << 10) + (bajo - 0xDC00);
            }
            utf8(&b, cp);
        } else {
            buf_liberar(&b);
            return -1;
        }
    }
    if (l->p >= l->fin) {
        buf_liberar(&b);
        return -1;
    }
    l->p++;
    buf_agregar(&b, "", 1);
    *salida = b.datos;
    *largo = b.largo - 1;
    return 0;
}

static int leer_valor(lector *l, int profundidad);

static int leer_contenedor(lector *l, int indice, int profundidad, char cierre) {
    int anterior = -1;
    l->p++;
    saltear_espacios(l);
    if (l->p < l->fin && *l->p == cierre) {
        l->p++;
        return 0;
    }
    for (;;) {
        char *clave = NULL;
        size_t largo_clave;
        saltear_espacios(l);
        if (cierre == '}') {
            if (leer_texto(l, &clave, &largo_clave) != 0) {
                return -1;
            }
            saltear_espacios(l);
            if (l->p >= l->fin || *l->p != ':') {
                free(clave);
                return -1;
            }
            l->p++;
        }
        int hijo = leer_valor(l, profundidad + 1);
        if (hijo < 0) {
            free(clave);
            return -1;
        }
        l->doc->nodos[hijo].clave = clave;
        if (anterior < 0) {
            l->doc->nodos[indice].primero = hijo;
        } else {
            l->doc->nodos[anterior].siguiente = hijo;
        }
        anterior = hijo;
        saltear_espacios(l);
        if (l->p < l->fin && *l->p == ',') {
            l->p++;
            continue;
        }
        if (l->p < l->fin && *l->p == cierre) {
            l->p++;
            return 0;
        }
        return -1;
    }
}

static int leer_valor(lector *l, int profundidad) {
    if (profundidad > PROFUNDIDAD_MAXIMA) {
        return -1;
    }
    saltear_espacios(l);
    if (l->p >= l->fin) {
        return -1;
    }
    char c = *l->p;
    int i;
    if (c == '{' || c == '[') {
        if ((i = nuevo_nodo(l->doc, c == '{' ? JSON_OBJETO : JSON_LISTA)) < 0) {
            return -1;
        }
        return leer_contenedor(l, i, profundidad, c == '{' ? '}' : ']') == 0 ? i : -1;
    }
    if (c == '"') {
        char *texto;
        size_t largo;
        if (leer_texto(l, &texto, &largo) != 0) {
            return -1;
        }
        if ((i = nuevo_nodo(l->doc, JSON_TEXTO)) < 0) {
            free(texto);
            return -1;
        }
        l->doc->nodos[i].texto = texto;
        l->doc->nodos[i].largo = largo;
        return i;
    }
    static const struct {
        const char *palabra;
        json_tipo tipo;
        int valor;
    } literales[] = {{"true", JSON_BOOL, 1}, {"false", JSON_BOOL, 0}, {"null", JSON_NULO, 0}};
    for (size_t k = 0; k < sizeof literales / sizeof literales[0]; k++) {
        size_t n = strlen(literales[k].palabra);
        if ((size_t)(l->fin - l->p) >= n && memcmp(l->p, literales[k].palabra, n) == 0) {
            l->p += n;
            if ((i = nuevo_nodo(l->doc, literales[k].tipo)) < 0) {
                return -1;
            }
            l->doc->nodos[i].booleano = literales[k].valor;
            return i;
        }
    }
    if (c == '-' || (c >= '0' && c <= '9')) {
        char numero[64];
        size_t n = 0;
        while (l->p < l->fin && n < sizeof numero - 1 && strchr("+-0123456789.eE", *l->p) != NULL && *l->p) {
            numero[n++] = *l->p++;
        }
        numero[n] = '\0';
        char *resto;
        double valor = strtod(numero, &resto);
        if (*resto != '\0' || (i = nuevo_nodo(l->doc, JSON_NUMERO)) < 0) {
            return -1;
        }
        l->doc->nodos[i].numero = valor;
        return i;
    }
    return -1;
}

int json_parsear(const char *texto, size_t largo, json_doc *doc) {
    memset(doc, 0, sizeof *doc);
    lector l = {texto, texto + largo, doc};
    if (leer_valor(&l, 0) != 0) { /* la raíz siempre es el nodo 0 */
        return -1;
    }
    saltear_espacios(&l);
    return l.p == l.fin ? 0 : -1;
}

void json_liberar(json_doc *doc) {
    for (int i = 0; i < doc->cantidad; i++) {
        free(doc->nodos[i].texto);
        free(doc->nodos[i].clave);
    }
    free(doc->nodos);
    memset(doc, 0, sizeof *doc);
}

const json_nodo *json_raiz(const json_doc *doc) {
    return doc->cantidad > 0 ? &doc->nodos[0] : NULL;
}

const json_nodo *json_primero(const json_doc *doc, const json_nodo *contenedor) {
    return contenedor != NULL && contenedor->primero >= 0 ? &doc->nodos[contenedor->primero] : NULL;
}

const json_nodo *json_siguiente(const json_doc *doc, const json_nodo *nodo) {
    return nodo->siguiente >= 0 ? &doc->nodos[nodo->siguiente] : NULL;
}

const json_nodo *json_campo(const json_doc *doc, const json_nodo *objeto, const char *clave) {
    if (objeto == NULL || objeto->tipo != JSON_OBJETO) {
        return NULL;
    }
    for (const json_nodo *n = json_primero(doc, objeto); n != NULL; n = json_siguiente(doc, n)) {
        if (n->clave != NULL && strcmp(n->clave, clave) == 0) {
            return n;
        }
    }
    return NULL;
}

const char *json_texto(const json_doc *doc, const json_nodo *objeto, const char *clave, const char *defecto) {
    const json_nodo *n = json_campo(doc, objeto, clave);
    return n != NULL && n->tipo == JSON_TEXTO ? n->texto : defecto;
}

long json_entero(const json_doc *doc, const json_nodo *objeto, const char *clave, long defecto) {
    const json_nodo *n = json_campo(doc, objeto, clave);
    return n != NULL && n->tipo == JSON_NUMERO ? (long)n->numero : defecto;
}

int json_bool(const json_doc *doc, const json_nodo *objeto, const char *clave, int defecto) {
    const json_nodo *n = json_campo(doc, objeto, clave);
    return n != NULL && n->tipo == JSON_BOOL ? n->booleano : defecto;
}

void buf_agregar(buffer *b, const void *datos, size_t largo) {
    if (b->largo + largo + 1 > b->capacidad) {
        size_t capacidad = b->capacidad ? b->capacidad : 256;
        while (capacidad < b->largo + largo + 1) {
            capacidad *= 2;
        }
        char *nuevo = realloc(b->datos, capacidad);
        if (nuevo == NULL) {
            abort(); /* Sin memoria en el agente: no hay recuperación razonable. */
        }
        b->datos = nuevo;
        b->capacidad = capacidad;
    }
    memcpy(b->datos + b->largo, datos, largo);
    b->largo += largo;
    b->datos[b->largo] = '\0';
}

void buf_texto(buffer *b, const char *texto) {
    buf_agregar(b, texto, strlen(texto));
}

void buf_printf(buffer *b, const char *formato, ...) {
    char temporal[512];
    va_list args;
    va_start(args, formato);
    int n = vsnprintf(temporal, sizeof temporal, formato, args);
    va_end(args);
    if (n > 0) {
        buf_agregar(b, temporal, (size_t)n < sizeof temporal ? (size_t)n : sizeof temporal - 1);
    }
}

void buf_json_texto(buffer *b, const char *texto) {
    buf_texto(b, "\"");
    for (const unsigned char *p = (const unsigned char *)texto; *p; p++) {
        switch (*p) {
        case '"': buf_texto(b, "\\\""); break;
        case '\\': buf_texto(b, "\\\\"); break;
        case '\n': buf_texto(b, "\\n"); break;
        case '\r': buf_texto(b, "\\r"); break;
        case '\t': buf_texto(b, "\\t"); break;
        default:
            if (*p < 0x20) {
                buf_printf(b, "\\u%04x", *p);
            } else {
                buf_agregar(b, p, 1);
            }
        }
    }
    buf_texto(b, "\"");
}

void buf_liberar(buffer *b) {
    free(b->datos);
    memset(b, 0, sizeof *b);
}
