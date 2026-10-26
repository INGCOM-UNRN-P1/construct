#!/usr/bin/env bash
# Genera la imagen del Guest de construct para una arquitectura (PLAN Fase 2).
#
# Herramienta de mantenedores: corre en Linux (o en el CI) y sólo necesita podman o docker; el
# disco se arma dentro de un contenedor Alpine (mke2fs con soporte de tar, sin root, y qemu-img),
# así no depende de cómo compiló e2fsprogs cada distro. Los alumnos nunca la ejecutan:
# `construct setup` baja la imagen publicada.
#
# Uso: vm_image/build_vm.sh [--arch x86_64|aarch64] [--version X.Y.Z] [--salida DIR] [--tamano 4G]
#
# Deja en DIR/<arch>/: vmlinuz, initramfs y rootfs.qcow2. vm_image/generar_manifiesto.py arma
# después el imagenes.json que consume `construct setup`.
set -euo pipefail

ARCH="$(uname -m)"
VERSION="dev"
SALIDA="$(pwd)/dist/imagenes"
TAMANO="4G"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
	case "$1" in
	--arch) ARCH="$2"; shift 2 ;;
	--version) VERSION="$2"; shift 2 ;;
	--salida) SALIDA="$2"; shift 2 ;;
	--tamano) TAMANO="$2"; shift 2 ;;
	-h | --help) sed -n '2,13p' "$0"; exit 0 ;;
	*) echo "opción desconocida: $1" >&2; exit 2 ;;
	esac
done

case "$ARCH" in
x86_64 | amd64) ARCH=x86_64; PLATAFORMA=linux/amd64 ;;
aarch64 | arm64) ARCH=aarch64; PLATAFORMA=linux/arm64 ;;
*) echo "arquitectura no soportada: $ARCH (x86_64 o aarch64)" >&2; exit 2 ;;
esac

MOTOR="${MOTOR:-$(command -v podman || command -v docker || true)}"
[[ -n "$MOTOR" ]] || { echo "hace falta podman o docker" >&2; exit 1; }
command -v tar >/dev/null || { echo "hace falta tar" >&2; exit 1; }
ALPINE="docker.io/library/alpine:3.22"

ETIQUETA="construct-guest:${VERSION}-${ARCH}"
DESTINO="$SALIDA/$ARCH"
TRABAJO="$(mktemp -d)"
trap 'rm -rf "$TRABAJO"; "$MOTOR" rm -f "construct-export-$$" >/dev/null 2>&1 || true' EXIT
mkdir -p "$DESTINO"

echo "==> Construyendo $ETIQUETA ($PLATAFORMA)"
"$MOTOR" build --platform "$PLATAFORMA" --build-arg "IMAGEN_VERSION=$VERSION" \
	-f "$AQUI/Dockerfile.debian" -t "$ETIQUETA" "$AQUI"

echo "==> Exportando el sistema de archivos"
"$MOTOR" create --platform "$PLATAFORMA" --name "construct-export-$$" "$ETIQUETA" /bin/true >/dev/null
"$MOTOR" export -o "$TRABAJO/rootfs.tar" "construct-export-$$"

# El kernel y el initramfs se pasan a QEMU con -kernel/-initrd: sin cargador de arranque.
tar -xf "$TRABAJO/rootfs.tar" -C "$TRABAJO" boot/construct-vmlinuz boot/construct-initrd
install -m 0644 "$TRABAJO/boot/construct-vmlinuz" "$DESTINO/vmlinuz"
install -m 0644 "$TRABAJO/boot/construct-initrd" "$DESTINO/initramfs"

echo "==> Creando rootfs.qcow2 ($TAMANO, ext4)"
# Dentro del contenedor se es root (del namespace): el tar se extrae con sus dueños reales a una
# carpeta del propio contenedor y mke2fs -d la copia tal cual. (mke2fs -d con el tar directo falla
# con nombres no ASCII: libarchive queda en la locale C.)
"$MOTOR" run --rm -v "$TRABAJO:/trabajo:Z" "$ALPINE" sh -euc "
	apk add -q --no-cache e2fsprogs qemu-img tar
	mkdir /raiz
	tar -xpf /trabajo/rootfs.tar -C /raiz --numeric-owner --xattrs
	# El motor de contenedores pisa estos archivos en el build: se escriben acá.
	echo construct > /raiz/etc/hostname
	printf '127.0.0.1\tlocalhost construct\n::1\tlocalhost\n' > /raiz/etc/hosts
	mke2fs -q -t ext4 -L construct-root -d /raiz /trabajo/rootfs.img $TAMANO
	qemu-img convert -c -O qcow2 /trabajo/rootfs.img /trabajo/rootfs.qcow2"
install -m 0644 "$TRABAJO/rootfs.qcow2" "$DESTINO/rootfs.qcow2"

echo "==> Listo: $DESTINO"
ls -lh "$DESTINO"
