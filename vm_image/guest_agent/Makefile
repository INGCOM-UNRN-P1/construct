# Agente del Guest. En la imagen se enlaza estático con musl (Dockerfile.alpine); en el Host de
# desarrollo (Linux) se compila igual para los tests de tests/test_agente.py.
CC ?= gcc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror -pedantic -D_FORTIFY_SOURCE=2
LDFLAGS ?=
SALIDA ?= construct_daemon

$(SALIDA): construct_daemon.c json.c json.h
	$(CC) $(CFLAGS) -o $@ construct_daemon.c json.c $(LDFLAGS)

.PHONY: clean
clean:
	rm -f construct_daemon
