/* JSON mínimo para el agente: lectura de los mensajes de control y escritura de respuestas.
 *
 * Alcanza para el protocolo construct-ctl (docs/protocolo.md): objetos, listas, textos con
 * escapes (incluidos \uXXXX y pares sustitutos), números, booleanos y null. Sin dependencias,
 * para enlazar estático con musl.
 */
#ifndef CONSTRUCT_JSON_H
#define CONSTRUCT_JSON_H

#include <stddef.h>

typedef enum { JSON_NULO, JSON_BOOL, JSON_NUMERO, JSON_TEXTO, JSON_LISTA, JSON_OBJETO } json_tipo;

typedef struct {
    json_tipo tipo;
    double numero;
    int booleano;
    char *texto;   /* JSON_TEXTO: terminado en '\0' (puede contener '\0' en el medio: ver largo) */
    size_t largo;
    char *clave;   /* clave dentro del objeto padre, o NULL */
    int primero;   /* índice del primer hijo, o -1 */
    int siguiente; /* índice del hermano siguiente, o -1 */
} json_nodo;

typedef struct {
    json_nodo *nodos;
    int cantidad;
    int capacidad;
} json_doc;

/* 0 si `texto` es un único valor JSON válido; -1 si no. Liberar siempre con json_liberar. */
int json_parsear(const char *texto, size_t largo, json_doc *doc);
void json_liberar(json_doc *doc);

const json_nodo *json_raiz(const json_doc *doc);
const json_nodo *json_campo(const json_doc *doc, const json_nodo *objeto, const char *clave);
const json_nodo *json_primero(const json_doc *doc, const json_nodo *contenedor);
const json_nodo *json_siguiente(const json_doc *doc, const json_nodo *nodo);

/* Atajos con valor por defecto si el campo falta o es de otro tipo. */
const char *json_texto(const json_doc *doc, const json_nodo *objeto, const char *clave, const char *defecto);
long json_entero(const json_doc *doc, const json_nodo *objeto, const char *clave, long defecto);
int json_bool(const json_doc *doc, const json_nodo *objeto, const char *clave, int defecto);

/* Buffer dinámico para armar respuestas. */
typedef struct {
    char *datos;
    size_t largo;
    size_t capacidad;
} buffer;

void buf_agregar(buffer *b, const void *datos, size_t largo);
void buf_texto(buffer *b, const char *texto);
void buf_printf(buffer *b, const char *formato, ...) __attribute__((format(printf, 2, 3)));
void buf_json_texto(buffer *b, const char *texto); /* "texto" con los escapes de JSON */
void buf_liberar(buffer *b);

#endif
