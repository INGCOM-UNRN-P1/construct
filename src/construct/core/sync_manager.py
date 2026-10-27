"""Sincronización Host ↔ Guest con Git (RF-08, PLAN §3.3, decisión D5).

Un repositorio interno en `<proyecto>/.construct/git` usa la carpeta del alumno como árbol de
trabajo: no toca su propio repositorio (si tiene uno), funciona aunque no use git, respeta su
`.gitignore` y normaliza los fines de línea a LF (`* text=auto eol=lf`). El Guest tiene un clon en
`/workspace/<proyecto>-<hash>`.

- **subir**: foto del árbol del Host → bundle incremental → el Guest lo trae y queda igual.
  Nunca fusiona: si la VM tiene cambios propios, aborta.
- **bajar**: el Guest guarda los cambios de archivos *ya versionados* (lo que tocó `gaff fix`;
  los archivos nuevos de la VM son artefactos, no código) → bundle → fusión de 3 vías en el Host.
  Ante conflictos quedan las marcas `<<<<<<<` en los archivos y la próxima `sync` exige resolverlas.
- **artefactos**: los `.bmp`, `.wav`, `.html`, `.mmd` y `reproducer.sh` nuevos de la VM se copian a
  `<proyecto>/.construct/artefactos/`.

`refs/construct/vm` marca el último commit que tienen los dos lados.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from construct.core.channel import Canal

CARPETA = ".construct"
REF_VM = "refs/construct/vm"
RAMA = "main"
EXTENSIONES_ARTEFACTOS = (".bmp", ".wav", ".html", ".mmd")
NOMBRES_ARTEFACTOS = ("reproducer.sh", "hal_reproducer.sh")
EXCLUSIONES = (f"/{CARPETA}/", "*.o", "*.exe", "a.out", "*.construct-parcial")
RESERVADOS_WINDOWS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MARCA_CONFLICTO = re.compile(rb"^(<{7}|>{7})( |$)", re.MULTILINE)


class ErrorDeSync(ValueError):
    """La sincronización no se pudo hacer (o necesita que el alumno intervenga)."""


@dataclass
class ResultadoSync:
    subidos: bool = False
    bajados: bool = False
    conflictos: list[str] = field(default_factory=list)
    artefactos: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class VersionesEnConflicto:
    ruta: str
    base: str | None
    host: str | None
    vm: str | None


@dataclass(frozen=True)
class EstadoSync:
    cambios_host: list[str]
    cambios_vm: bool
    fusion_pendiente: bool


def es_artefacto(ruta: str) -> bool:
    nombre = PurePosixPath(ruta).name
    return nombre.lower().endswith(EXTENSIONES_ARTEFACTOS) or nombre in NOMBRES_ARTEFACTOS


# --- Nombres de archivo portables -------------------------------------------------------------


def problemas_de_nombres(rutas: Iterable[str]) -> list[str]:
    """Nombres que romperían el proyecto en otro sistema: reservados de Windows, caracteres que
    Windows no admite, distintos sólo en mayúsculas (Windows y macOS no los distinguen) o en la forma
    Unicode (macOS guarda «ó» descompuesta)."""
    problemas = []
    vistos: dict[str, str] = {}
    for ruta in rutas:
        for parte in PurePosixPath(ruta).parts:
            base = parte.split(".")[0].lower()
            if base in RESERVADOS_WINDOWS:
                problemas.append(f"«{ruta}»: «{parte}» es un nombre reservado en Windows")
            if any(c in parte for c in '<>:"\\|?*') or parte.endswith((" ", ".")):
                problemas.append(f"«{ruta}»: «{parte}» tiene caracteres que Windows no admite")
        if unicodedata.normalize("NFC", ruta) != ruta:
            problemas.append(f"«{ruta}»: el nombre no está en forma Unicode NFC")
        clave = unicodedata.normalize("NFC", ruta).casefold()
        if clave in vistos and vistos[clave] != ruta:
            problemas.append(f"«{ruta}» y «{vistos[clave]}» sólo se diferencian en mayúsculas")
        vistos.setdefault(clave, ruta)
    return problemas


def nombre_en_vm(proyecto: Path, raiz: str = "/workspace") -> str:
    """`/workspace/<carpeta>-<hash>`: estable por proyecto y sin choques entre carpetas homónimas."""
    absoluta = str(proyecto.resolve())
    limpio = re.sub(r"[^A-Za-z0-9._-]+", "-", unicodedata.normalize("NFKD", proyecto.resolve().name)
                    .encode("ascii", "ignore").decode()).strip("-.") or "proyecto"
    return f"{raiz}/{limpio}-{hashlib.sha1(absoluta.encode()).hexdigest()[:8]}"


# --- Git del Host -----------------------------------------------------------------------------


class RepoHost:
    def __init__(self, proyecto: Path, git: str) -> None:
        self.proyecto = proyecto.resolve()
        self.git_dir = self.proyecto / CARPETA / "git"
        self.git = git

    def correr(self, *argumentos: str, aceptar: Sequence[int] = (0,)) -> subprocess.CompletedProcess[str]:
        entorno = {**os.environ, "GIT_DIR": str(self.git_dir), "GIT_WORK_TREE": str(self.proyecto),
                   "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        salida = subprocess.run([self.git, *argumentos], cwd=self.proyecto, env=entorno, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", check=False)
        if salida.returncode not in aceptar:
            raise ErrorDeSync(f"git {argumentos[0]} falló: {(salida.stderr or salida.stdout).strip()}")
        return salida

    def inicializar(self) -> None:
        if (self.git_dir / "HEAD").is_file():
            return
        self.git_dir.parent.mkdir(parents=True, exist_ok=True)
        self.correr("init", "-q", "-b", RAMA)
        for clave, valor in (("user.name", "construct"), ("user.email", "construct@localhost"),
                             ("core.autocrlf", "false"), ("core.quotepath", "false"), ("commit.gpgsign", "false"),
                             ("core.precomposeunicode", "true"), ("gc.auto", "0")):
            self.correr("config", clave, valor)
        info = self.git_dir / "info"
        info.mkdir(exist_ok=True)
        (info / "exclude").write_text("\n".join(EXCLUSIONES) + "\n", encoding="utf-8")
        (info / "attributes").write_text("* text=auto eol=lf\n", encoding="utf-8")

    def ref(self, nombre: str) -> str | None:
        salida = self.correr("rev-parse", "-q", "--verify", f"{nombre}^{{commit}}", aceptar=(0, 1))
        return salida.stdout.strip() or None

    def archivos(self) -> list[str]:
        salida = self.correr("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return [r for r in salida.stdout.split("\0") if r]

    def cambios(self) -> list[str]:
        salida = self.correr("status", "--porcelain=v1", "-z", "--untracked-files=all")
        return [linea[3:] for linea in salida.stdout.split("\0") if len(linea) > 3]

    def fusion_pendiente(self) -> bool:
        return (self.git_dir / "MERGE_HEAD").is_file()

    def foto(self) -> str:
        """Commit con el estado actual del árbol (o el mismo HEAD si no cambió nada)."""
        problemas = problemas_de_nombres(self.archivos())
        if problemas:
            raise ErrorDeSync("hay nombres de archivo que no funcionan en todos los sistemas:\n  - "
                              + "\n  - ".join(problemas) + "\nRenombralos y volvé a intentar.")
        self.correr("add", "-A")
        sin_cambios = self.correr("diff", "--cached", "--quiet", aceptar=(0, 1)).returncode == 0
        if not sin_cambios or self.ref("HEAD") is None:
            self.correr("commit", "-q", "--no-verify", "--allow-empty", "-m", "construct: foto del Host")
        head = self.ref("HEAD")
        assert head is not None
        return head

    def en_conflicto(self) -> list[str]:
        """Archivos de la fusión pendiente que todavía tienen marcas de conflicto."""
        if not self.fusion_pendiente():
            return []
        en_conflicto = self.correr("diff", "--name-only", "--diff-filter=U").stdout.split()
        return [r for r in en_conflicto if (self.proyecto / r).is_file()
                and MARCA_CONFLICTO.search((self.proyecto / r).read_bytes())]

    def versiones(self, ruta: str) -> VersionesEnConflicto:
        """Base común, versión del Host y de la VM de un archivo en conflicto (None si no existe en esa)."""
        def etapa(numero: int) -> str | None:
            salida = self.correr("show", f":{numero}:{ruta}", aceptar=(0, 128))
            return salida.stdout if salida.returncode == 0 else None

        return VersionesEnConflicto(ruta, etapa(1), etapa(2), etapa(3))

    def elegir(self, ruta: str, eleccion: str) -> None:
        """Resuelve un conflicto quedándose con la versión del Host (`host`) o la de la VM (`vm`)."""
        versiones = self.versiones(ruta)
        texto = versiones.host if eleccion == "host" else versiones.vm if eleccion == "vm" else None
        if eleccion not in ("host", "vm"):
            raise ValueError(f"elección desconocida: {eleccion} (host o vm)")
        destino = self.proyecto / ruta
        if texto is None:
            destino.unlink(missing_ok=True)  # en esa versión el archivo se había borrado
        else:
            destino.write_text(texto, encoding="utf-8", newline="\n")

    def terminar_fusion(self) -> None:
        """Cierra una fusión con conflictos si ya no quedan marcas en los archivos."""
        pendientes = self.en_conflicto()
        if pendientes:
            raise ErrorDeSync("todavía hay conflictos sin resolver (marcas <<<<<<< y >>>>>>>) en:\n  - "
                              + "\n  - ".join(pendientes)
                              + "\nElegí en cada uno qué versión queda, borrá las marcas y ejecutá `construct sync`.")
        self.correr("add", "-A")
        self.correr("commit", "-q", "--no-verify", "--no-edit")


# --- Sincronización ---------------------------------------------------------------------------


class SyncManager:
    def __init__(self, proyecto: Path, git: str, canal: Canal, *, raiz_vm: str = "/workspace") -> None:
        self.repo = RepoHost(proyecto, git)
        self.canal = canal
        self.vm = nombre_en_vm(proyecto, raiz_vm)
        self.artefactos = self.repo.proyecto / CARPETA / "artefactos"

    async def _git_vm(self, *argumentos: str, aceptar: Sequence[int] = (0,)) -> str:
        resultado = await self.canal.correr(["git", "-C", self.vm, *argumentos])
        if resultado.codigo not in aceptar or resultado.error:
            mensaje = resultado.error or resultado.errores.decode(errors="replace").strip()
            raise ErrorDeSync(f"git {argumentos[0]} en la VM falló: {mensaje}")
        return resultado.texto().strip()

    async def _existe_en_vm(self) -> bool:
        return (await self.canal.correr(["test", "-d", f"{self.vm}/.git"])).codigo == 0

    async def _head_vm(self) -> str:
        return await self._git_vm("rev-parse", "HEAD")

    async def _vm_tiene_cambios(self) -> bool:
        if not await self._existe_en_vm():
            return False
        sucio = await self._git_vm("status", "--porcelain", "--untracked-files=no")
        return bool(sucio) or await self._head_vm() != self.repo.ref(REF_VM)

    async def _nuevos_en_vm(self) -> list[str]:
        """Archivos que se crearon en la VM y vale la pena traer: de texto, fuera de `.gitignore`,
        que no sean artefactos (esos van a `.construct/artefactos/`) y con nombres que anden en
        cualquier sistema. Los binarios (lo compilado) y el resto se quedan en la VM."""
        sueltos = [r for r in (await self._git_vm("ls-files", "-z", "--others", "--exclude-standard")).split("\0")
                   if r and not es_artefacto(r)]
        if not sueltos:
            return []
        # grep -I descarta los binarios; los vacíos se traen igual (p. ej. un caso de prueba sin entrada).
        script = ('cd "$1" && shift && for f; do { [ ! -s "$f" ] || grep -Iq "" -- "$f"; } && printf "%s\\0" "$f"; '
                  'done; true')
        texto = (await self.canal.correr(["sh", "-c", script, "sh", self.vm, *sueltos])).texto()
        candidatos = [r for r in texto.split("\0") if r]
        con_problemas = {r for r in candidatos if problemas_de_nombres([r])}
        return [r for r in candidatos if r not in con_problemas]

    def _bundle(self, base: str | None) -> bytes:
        with tempfile.TemporaryDirectory() as carpeta:
            archivo = Path(carpeta) / "subir.bundle"
            rango = [RAMA, f"^{base}"] if base else [RAMA]
            self.repo.correr("bundle", "create", "-q", str(archivo), *rango)
            return archivo.read_bytes()

    async def subir(self) -> bool:
        """Deja la VM igual al Host; False si ya lo estaba."""
        self.repo.inicializar()
        if self.repo.fusion_pendiente():
            raise ErrorDeSync("hay una fusión con conflictos sin terminar: resolvela y ejecutá `construct sync`.")
        local = self.repo.foto()
        existe = await self._existe_en_vm()
        base = self.repo.ref(REF_VM) if existe else None
        if existe:
            if await self._vm_tiene_cambios():
                raise ErrorDeSync("la VM tiene cambios que el Host todavía no tiene (por ejemplo, de `gaff fix`): "
                                  "ejecutá `construct sync` para traerlos y fusionarlos.")
            if await self._head_vm() == local:
                return False
        bundle = f"/tmp/construct-{PurePosixPath(self.vm).name}.bundle"
        await self.canal.escribir_archivo(bundle, self._bundle(base))
        if existe:
            await self._git_vm("fetch", "-q", bundle, f"{RAMA}:refs/construct/host")
            await self._git_vm("reset", "-q", "--hard", "refs/construct/host")
        else:
            clonado = await self.canal.correr(["git", "clone", "-q", "-b", RAMA, bundle, self.vm])
            if not clonado.ok:
                raise ErrorDeSync(f"no se pudo crear el proyecto en la VM: {clonado.errores.decode(errors='replace')}")
            for clave, valor in (("user.name", "construct"), ("user.email", "construct@localhost"),
                                 ("core.autocrlf", "false")):
                await self._git_vm("config", clave, valor)
        await self.canal.correr(["rm", "-f", bundle])
        self.repo.correr("update-ref", REF_VM, local)
        return True

    async def bajar(self, *, nuevos: bool = False) -> tuple[bool, list[str]]:
        """Trae los cambios de la VM y los fusiona; (si trajo algo, archivos en conflicto).

        Sólo los archivos que ya estaban en el proyecto, salvo con `nuevos`: después de una sesión
        interactiva (`construct entrar`, jonesy) también los que se crearon allá (`_nuevos_en_vm`).
        """
        self.repo.inicializar()
        if not await self._existe_en_vm():
            return False, []
        agregados = await self._nuevos_en_vm() if nuevos else []
        if not agregados and not await self._vm_tiene_cambios():
            return False, []
        if agregados:
            await self._git_vm("add", "--", *agregados)
        await self._git_vm("add", "-u")
        if (await self.canal.correr(["git", "-C", self.vm, "diff", "--cached", "--quiet"])).codigo != 0:
            await self._git_vm("commit", "-q", "--no-verify", "-m", "construct: cambios de la VM")
        cabeza_vm = await self._head_vm()
        base = self.repo.ref(REF_VM)
        bundle = f"/tmp/construct-{PurePosixPath(self.vm).name}-bajar.bundle"
        await self._git_vm("bundle", "create", "-q", bundle, RAMA, *([f"^{base}"] if base else []))
        datos = await self.canal.leer_archivo(bundle)
        await self.canal.correr(["rm", "-f", bundle])
        with tempfile.TemporaryDirectory() as carpeta:
            archivo = Path(carpeta) / "bajar.bundle"
            archivo.write_bytes(datos)
            self.repo.correr("fetch", "-q", str(archivo), f"{RAMA}:refs/construct/desde-vm")
        local = self.repo.foto()
        self.repo.correr("update-ref", REF_VM, cabeza_vm)
        if local == base:  # el Host no cambió: avance directo
            self.repo.correr("merge", "-q", "--ff-only", "refs/construct/desde-vm")
            return True, []
        fusion = self.repo.correr("merge", "-q", "--no-edit", "-m", "construct: fusión con la VM",
                                  "refs/construct/desde-vm", aceptar=(0, 1))
        if fusion.returncode == 1:
            return True, self.repo.correr("diff", "--name-only", "--diff-filter=U").stdout.split()
        return True, []

    async def bajar_artefactos(self) -> list[Path]:
        """Copia al Host los artefactos nuevos de la VM (no los archivos versionados)."""
        if not await self._existe_en_vm():
            return []
        marca, nueva = f"{self.vm}/.git/construct-marca", f"{self.vm}/.git/construct-marca.nueva"
        # La marca nueva se toma antes de buscar: lo que cambie mientras tanto vuelve a salir la próxima vez.
        script = ('touch "$2"; cd "$1" || exit 1; '
                  'if [ -e "$3" ]; then find . -path ./.git -prune -o -type f -newer "$3" -print; '
                  'else find . -path ./.git -prune -o -type f -print; fi; mv "$2" "$3"')
        encontrados = (await self.canal.correr(["sh", "-c", script, "sh", self.vm, nueva, marca])).texto().splitlines()
        versionados = set((await self._git_vm("ls-files")).splitlines())
        copiados = []
        for ruta in (r[2:] for r in encontrados if r.startswith("./")):
            if ruta in versionados or not es_artefacto(ruta):
                continue
            destino = self.artefactos / ruta
            try:
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_bytes(await self.canal.leer_archivo(f"{self.vm}/{ruta}"))
            except OSError:
                continue  # un nombre que el sistema del Host no admite: se deja en la VM
            copiados.append(destino)
        return copiados

    async def sincronizar(self) -> ResultadoSync:
        """`construct sync`: bajar (con fusión), subir y artefactos."""
        self.repo.inicializar()
        if self.repo.fusion_pendiente():
            self.repo.terminar_fusion()
        resultado = ResultadoSync()
        resultado.bajados, resultado.conflictos = await self.bajar()
        if resultado.conflictos:
            return resultado
        resultado.subidos = await self.subir()
        resultado.artefactos = await self.bajar_artefactos()
        return resultado

    # Resolución de conflictos (sync_conflict_modal): todo en el Host, la próxima sync la cierra.

    def conflictos(self) -> list[str]:
        return self.repo.en_conflicto()

    def versiones(self, ruta: str) -> VersionesEnConflicto:
        return self.repo.versiones(ruta)

    def elegir(self, ruta: str, eleccion: str) -> None:
        self.repo.elegir(ruta, eleccion)

    async def estado(self) -> EstadoSync:
        self.repo.inicializar()
        en_vm = self.repo.ref(REF_VM)
        if en_vm is None:
            cambios = self.repo.archivos()
        else:
            # Lo que cambió desde la última foto, más lo que ya estaba en una foto que la VM no tiene.
            fotos = self.repo.correr("diff", "--name-only", en_vm, "HEAD").stdout.split("\n")
            cambios = sorted({*self.repo.cambios(), *(r for r in fotos if r)})
        return EstadoSync(cambios_host=cambios,
                          cambios_vm=await self._vm_tiene_cambios(),
                          fusion_pendiente=self.repo.fusion_pendiente())
