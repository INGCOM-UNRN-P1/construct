# Sesión del entorno de la cátedra (INGCOM-UNRN-P1/entorno, variante Linux) en la VM.
#
# Hace en cada shell de login de estudiante lo mismo que `source linux/activate.sh`: comandos de
# cátedra en el PATH, toolchain de C y la marca (p1) en el prompt. La bienvenida (banner y `ayuda`)
# la muestra el .bashrc del entorno, como en el lanzador de Windows. activate.sh imprime además su
# propio resumen, que acá se calla: la VM no tiene una sesión "original" a la que volver.
if [ -n "${BASH_VERSION:-}" ] && [ "$(id -un)" = estudiante ] && [ -f /opt/p1/entorno/linux/activate.sh ]; then
    . /opt/p1/entorno/linux/activate.sh >/dev/null
fi
