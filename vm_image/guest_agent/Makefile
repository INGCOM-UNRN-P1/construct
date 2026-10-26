# Agente del Guest y biblioteca de telemetría. En la imagen se compilan en Debian (Dockerfile.debian);
# en el Host de desarrollo (Linux) igual, para los tests.
CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -pedantic -D_FORTIFY_SOURCE=2
LDFLAGS ?=
SALIDA ?= construct_daemon
PRELOAD ?= libconstruct_preload.so

all: $(SALIDA) $(PRELOAD)

$(SALIDA): construct_daemon.c json.c json.h
	$(CC) $(CFLAGS) -o $@ construct_daemon.c json.c $(LDFLAGS)

# Biblioteca para LD_PRELOAD (telemetría de heap y archivos, RF-06/07).
$(PRELOAD): libconstruct_preload.c
	$(CC) $(CFLAGS) -fPIC -shared -o $@ libconstruct_preload.c -ldl -lpthread

.PHONY: all clean
clean:
	rm -f construct_daemon libconstruct_preload.so
