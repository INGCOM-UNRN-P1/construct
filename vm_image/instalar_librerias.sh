#!/bin/sh
# Compila e instala las librerías C de la cátedra en el Guest (SPEC §3.2).
#
# Ninguna de las tres trae `make install`, así que se compilan acá con los flags normativos:
#   treadstone → /usr/local/lib/libp1_test.a, cabeceras en /usr/local/include (p1_test.h, tda/…)
#   bitmaps    → /usr/local/lib/libbitmaps.a, cabeceras en /usr/local/include/bitmaps/
#   sonido     → /usr/local/lib/libsonido.a, cabeceras en /usr/local/include/sonido/
# bitmaps y sonido van en subcarpetas porque las dos llaman libreria.h a su cabecera.
#
# Uso: instalar_librerias.sh TREADSTONE_REF BITMAPS_REF SONIDO_REF
set -eu

ORG="https://github.com/INGCOM-UNRN-P1"
CFLAGS="-std=c11 -O2 -Wall -Wextra -pedantic -fno-omit-frame-pointer"
TRABAJO="$(mktemp -d)"
trap 'rm -rf "$TRABAJO"' EXIT

traer() { # nombre ref
	git -c advice.detachedHead=false clone -q "$ORG/$1" "$TRABAJO/$1"
	git -C "$TRABAJO/$1" checkout -q "$2"
}

traer treadstone "$1"
cc $CFLAGS -I"$TRABAJO/treadstone/include" -c "$TRABAJO"/treadstone/src/*.c -o "$TRABAJO/p1_test.o"
ar rcs /usr/local/lib/libp1_test.a "$TRABAJO/p1_test.o"
cp -R "$TRABAJO"/treadstone/include/. /usr/local/include/

traer bitmaps "$2"
mkdir -p /usr/local/include/bitmaps
cc $CFLAGS -c "$TRABAJO/bitmaps/bitmap/libreria.c" -o "$TRABAJO/bitmaps.o"
ar rcs /usr/local/lib/libbitmaps.a "$TRABAJO/bitmaps.o"
cp "$TRABAJO"/bitmaps/bitmap/*.h /usr/local/include/bitmaps/

traer sonido "$3"
mkdir -p /usr/local/include/sonido
cc $CFLAGS -c "$TRABAJO/sonido/libreria/libreria.c" -o "$TRABAJO/sonido.o"
ar rcs /usr/local/lib/libsonido.a "$TRABAJO/sonido.o"  # se enlaza con -lsonido -lm
cp "$TRABAJO"/sonido/libreria/*.h /usr/local/include/sonido/

echo "librerías instaladas: p1_test, bitmaps, sonido"
