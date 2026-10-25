#!/usr/bin/env python3
"""
IEE — Índice de Entropía Estructural
Mide la fragmentación y dispersión de un árbol de directorios: profundidad
promedio, dispersión entre carpetas de primer nivel, archivos huérfanos en
la raíz, carpetas casi vacías y carpetas nuevas sin catalogar.

Uso:
  python3 iee.py                                # mide el directorio actual
  python3 iee.py --path /ruta                   # mide otra ruta
  python3 iee.py --config iee.config.yml        # usa config explícita
  python3 iee.py --fix                          # dry-run: plan de orden
  python3 iee.py --fix --apply                  # ejecuta el plan
  python3 iee.py --json                         # salida JSON
  python3 iee.py --list-conocidos                # lista proyectos_conocidos existentes
  python3 iee.py --no-history                    # no registrar en el historial
  python3 iee.py --clasificar                    # qué es cada carpeta sin catalogar y qué hacer
  python3 iee.py --adoptar NOMBRE [--motivo ".."]  # agrega la carpeta a proyectos_conocidos (respaldo)
  python3 iee.py --ignorar NOMBRE [--motivo ".."]  # la deja como esperada pero SIN respaldo
  (--adoptar/--ignorar aceptan --simular; --adoptar exige --confirmar-datos si hay datos o .env)

Configuración: ver iee.config.example.yml. Sin config, corre con defaults
genéricos y sin proyectos_conocidos propios — el primer run va a mostrar
todas las carpetas de primer nivel como "sin catalogar"; copia el ejemplo,
ajústalo y pásalo con --config (o déjalo en ./iee.config.yml o
~/.config/iee/config.yml, que se detectan automáticamente).
"""

import argparse
import json
import os
import re
import subprocess
import tempfile
import shutil
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

# Ruido técnico genérico que no aporta al análisis de estructura, válido
# para casi cualquier proyecto de código. Lo específico de tu entorno
# (apps de terceros, venvs sueltos, etc.) va en tu config, se suma a esto.
EXCLUIR_DIRS_BASE = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
    ".cache", "site-packages", "eggs", ".eggs",
}

# Archivos/carpetas de raíz esperados en casi cualquier home/root de Linux.
# Lo específico de tu entorno (scripts propios, docs propias) va en tu
# config, se suma a esto.
RAIZ_ESPERADOS_BASE = {
    ".bashrc", ".profile", ".bash_history", ".ssh", ".gnupg",
    ".config", ".local", ".cache",
}

# Extensiones que --fix nunca mueve (credenciales, scripts, config).
EXTENSIONES_PROTEGIDAS = {
    ".pass", ".key", ".pem", ".pfx", ".p12", ".crt", ".cer",
    ".sh", ".py", ".env", ".json", ".toml", ".cfg", ".conf", ".ini",
    ".md", ".txt",  # docs y texto plano pueden tener info sensible
}

# Extensiones/sufijos que --fix considera seguros de mover a _inbox/.
EXTENSIONES_MOVIBLES = {".bak", ".bak-", ".xlsx", ".xls", ".csv", ".tmp", ".log"}
SUFIJOS_BACKUP = ("-bak", ".bak", ".old", ".orig", ".backup", ".disabled")

# Directorios transitorios (inbox de la propia herramienta) — se excluyen
# del cálculo de dispersión porque su volumen es esperado, no desorden.
DIRS_TRANSITORIOS = {"_inbox"}

HISTORIAL_MAX = 104  # ~2 años de reportes semanales


@dataclass
class Config:
    proyectos_conocidos: set = field(default_factory=set)
    raiz_esperados: set = field(default_factory=lambda: set(RAIZ_ESPERADOS_BASE))
    excluir_dirs: set = field(default_factory=lambda: set(EXCLUIR_DIRS_BASE))
    historial_path: Path | None = None


def _localizar_config(explicito: str | None) -> Path | None:
    """Busca el archivo de config: --config explícito > $IEE_CONFIG >
    ./iee.config.yml > ~/.config/iee/config.yml. None si no hay ninguno."""
    if explicito:
        p = Path(explicito)
        return p if p.exists() else None
    env = os.environ.get("IEE_CONFIG")
    if env and Path(env).exists():
        return Path(env)
    for candidato in (Path.cwd() / "iee.config.yml", Path.home() / ".config" / "iee" / "config.yml"):
        if candidato.exists():
            return candidato
    return None


def cargar_config(ruta_config: Path | None) -> Config:
    cfg = Config()
    if ruta_config is None:
        return cfg
    if yaml is None:
        print(f"AVISO: pyyaml no está instalado, ignorando {ruta_config} (pip install pyyaml)")
        return cfg
    datos = yaml.safe_load(ruta_config.read_text(encoding="utf-8")) or {}
    cfg.proyectos_conocidos |= set(datos.get("proyectos_conocidos", []))
    cfg.raiz_esperados |= set(datos.get("raiz_esperados", []))
    cfg.excluir_dirs |= set(datos.get("excluir_dirs", []))
    if datos.get("historial_path"):
        cfg.historial_path = Path(datos["historial_path"]).expanduser()
    return cfg


# --------------------------------------------------------------------------
# Catastro asistido: clasificar carpetas sin catalogar y adoptarlas/ignorarlas
# editando la config sin perder comentarios (no usa un dumper de YAML).
# --------------------------------------------------------------------------
EXT_DATOS = {".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".csv"}
PATRON_COPIA_O_PRUEBA = re.compile(
    r"(\.bak|-bak|\.old|\.orig|backup|backups|test|prueba|pruebas|evidencia|residuo|scratch|tmp|archivo)", re.I
)
BACKUPS_DE_CONFIG_MAX = 3


def _nombre_valido(nombre: str) -> bool:
    return bool(nombre) and nombre not in (".", "..") and "/" not in nombre and "\0" not in nombre


def inspeccionar_carpeta(carpeta: Path, excluir: set | None = None, max_archivos: int = 50000) -> dict:
    """Mira el contenido sin modificar nada: git, tamaño (sin venvs/caches), archivos de datos y .env."""
    excluir = excluir or EXCLUIR_DIRS_BASE
    info = {"git": (carpeta / ".git").exists(), "bytes": 0, "archivos": 0, "datos": set(), "env": False}
    for dirpath, dirs, files in os.walk(carpeta):
        dirs[:] = [d for d in dirs if d not in excluir]
        for fn in files:
            info["archivos"] += 1
            if info["archivos"] > max_archivos:
                info["truncado"] = True
                return info
            p = Path(dirpath) / fn
            try:
                info["bytes"] += p.lstat().st_size
            except OSError:
                pass
            ext = p.suffix.lower()
            if ext in EXT_DATOS:
                info["datos"].add(ext)
            if fn == ".env" or fn.startswith(".env."):
                if not fn.endswith((".example", ".sample", ".template")):
                    info["env"] = True
    return info


def referencias_activas(raiz: Path, nombre: str, textos: dict) -> list:
    """Fuentes (cron, unidades systemd...) cuyo texto menciona la carpeta por su ruta completa."""
    ruta = str(raiz / nombre)
    return sorted(k for k, tx in textos.items() if ruta + "/" in tx or tx.rstrip().endswith(ruta) or ruta + " " in tx)


def textos_de_ejecucion() -> dict:
    """Texto del crontab del usuario y de las unidades systemd del sistema (best-effort, solo lectura)."""
    textos = {}
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            textos["crontab"] = r.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    unidades = Path("/etc/systemd/system")
    if unidades.is_dir():
        for u in unidades.glob("*.service"):
            try:
                textos[f"unidad {u.name}"] = u.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    return textos


def clasificar_carpeta(raiz: Path, nombre: str, excluir: set, textos: dict) -> dict:
    info = inspeccionar_carpeta(raiz / nombre, excluir)
    refs = referencias_activas(raiz, nombre, textos)
    if refs:
        clase = "activa"
    elif PATRON_COPIA_O_PRUEBA.search(nombre):
        clase = "copia_o_prueba"
    else:
        clase = "revisar"
    return {"nombre": nombre, "clase": clase, "referencias": refs, **info}


def _tam(b: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB":
            return f"{b:.0f} {u}" if u == "B" else f"{b:.1f} {u}"
        b /= 1024
    return f"{b} B"


def imprimir_clasificacion(raiz: Path, nombres: list, cfg: "Config") -> None:
    if not nombres:
        print("\n  No hay carpetas sin catalogar.\n")
        return
    textos = textos_de_ejecucion()
    print(f"\n  Carpetas sin catalogar en {raiz} ({len(nombres)}):\n")
    for n in sorted(nombres):
        c = clasificar_carpeta(raiz, n, cfg.excluir_dirs, textos)
        marcas = []
        if c["git"]:
            marcas.append("git")
        if c["datos"]:
            marcas.append("datos " + "/".join(sorted(c["datos"])))
        if c["env"]:
            marcas.append(".env")
        titulo = {"activa": "ACTIVA (la usa un cron o un servicio)", "copia_o_prueba": "copia o prueba (por el nombre)",
                  "revisar": "a revisar (no la usa ningún cron ni servicio)"}[c["clase"]]
        print(f"  • {n}  —  {titulo}")
        print(f"      {_tam(c['bytes'])}, {c['archivos']} archivos" + (f", {', '.join(marcas)}" if marcas else "")
              + (" (conteo truncado)" if c.get("truncado") else ""))
        if c["referencias"]:
            print(f"      referenciada por: {', '.join(c['referencias'][:4])}")
        flag = " --confirmar-datos" if (c["datos"] or c["env"]) else ""
        if c["clase"] == "activa":
            print(f"      sugerido: iee.py --adoptar {n}{flag}   (entra al respaldo)")
        elif c["clase"] == "copia_o_prueba":
            print(f"      sugerido: borrarla si ya no sirve (con tu visto bueno) o iee.py --ignorar {n} (sin respaldo)")
        else:
            print(f"      sugerido: decidir; iee.py --adoptar {n}{flag} (con respaldo) o iee.py --ignorar {n} (sin respaldo)")
        print()


def _linea_item(nombre: str, motivo: str, sangria: str) -> str:
    if re.search(r"""[:#\[\]{},&*!|>'"%@`]|^\s|\s$|^-""", nombre):
        nombre = json.dumps(nombre, ensure_ascii=False)
    motivo = " ".join((motivo or "").split())
    nota = f"adoptado/clasificado {datetime.now():%Y-%m-%d}" + (f": {motivo}" if motivo else "")
    return f"{sangria}- {nombre}  # {nota}"


def _insertar_en_lista(texto: str, clave: str, nombre: str, motivo: str) -> str:
    lineas = texto.split("\n")
    idx = next((i for i, l in enumerate(lineas) if re.match(rf"^{re.escape(clave)}\s*:", l)), None)
    if idx is None:
        base = texto if (texto.endswith("\n") or not texto) else texto + "\n"
        return base + f"{clave}:\n" + _linea_item(nombre, motivo, "  ") + "\n"
    resto = lineas[idx].split(":", 1)[1].strip()
    if resto and not resto.startswith("#"):
        raise ValueError(f"'{clave}' está escrita en línea ({resto[:30]}...); conviértela a lista con guiones para poder editarla")
    fin, sangria = idx, "  "
    for j in range(idx + 1, len(lineas)):
        l = lineas[j]
        m = re.match(r"^(\s+)-\s", l)
        if m:
            fin, sangria = j, m.group(1)
        elif l.strip() == "" or l.lstrip().startswith("#"):
            continue
        elif re.match(r"^\S", l):
            break
        else:
            fin = j
    lineas.insert(fin + 1, _linea_item(nombre, motivo, sangria))
    return "\n".join(lineas)


def _escribir_atomico(ruta: Path, contenido: str) -> None:
    modo = ruta.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=str(ruta.parent), prefix=ruta.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contenido)
        os.chmod(tmp, modo)
        os.replace(tmp, ruta)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _respaldar_config(ruta: Path) -> Path:
    destino = ruta.with_name(f"{ruta.name}.bak-iee-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(ruta, destino)
    previos = sorted(ruta.parent.glob(f"{ruta.name}.bak-iee-*"))
    for viejo in previos[:-BACKUPS_DE_CONFIG_MAX]:
        viejo.unlink()
    return destino


def catalogar(accion: str, nombres: list, raiz: Path, ruta_config: Path | None, motivo: str,
              simular: bool, confirmar_datos: bool) -> int:
    """accion: 'adoptar' (proyectos_conocidos: se respalda) o 'ignorar' (raiz_esperados: esperada, sin respaldo)."""
    if ruta_config is None:
        print("ERROR: no encontré la config. Pásala con --config ruta/iee.config.yml.")
        return 1
    if yaml is None:
        print("ERROR: falta pyyaml (pip install pyyaml) para validar la config.")
        return 1
    clave = "proyectos_conocidos" if accion == "adoptar" else "raiz_esperados"
    cfg = cargar_config(ruta_config)
    actuales = cfg.proyectos_conocidos if accion == "adoptar" else cfg.raiz_esperados
    texto = ruta_config.read_text(encoding="utf-8")
    textos = textos_de_ejecucion() if accion == "adoptar" else {}
    cambios = []
    for n in nombres:
        if not _nombre_valido(n):
            print(f"ERROR: nombre no válido: {n!r} (es el nombre de una carpeta directamente dentro de {raiz}, sin rutas).")
            return 1
        if not (raiz / n).is_dir():
            print(f"ERROR: {raiz / n} no existe o no es una carpeta. No se cambió nada.")
            return 1
        if n in actuales:
            print(f"  {n}: ya está en {clave}. Nada que hacer.")
            continue
        if accion == "adoptar":
            c = clasificar_carpeta(raiz, n, cfg.excluir_dirs, textos)
            sensibles = sorted(c["datos"]) + ([".env"] if c["env"] else [])
            print(f"  {n}: {_tam(c['bytes'])}, {c['archivos']} archivos" + (", git" if c["git"] else "")
                  + (f", contiene {', '.join(sensibles)}" if sensibles else "") + f" [{c['clase']}]")
            if sensibles and not confirmar_datos:
                print(f"  → NO adoptada: contiene {', '.join(sensibles)} (pueden ser datos personales o secretos) y adoptarla "
                      f"copia todo eso al respaldo en la nube. Si quieres respaldarla igual: "
                      f"iee.py --adoptar {n} --confirmar-datos --motivo \"...\"")
                return 2
        texto = _insertar_en_lista(texto, clave, n, motivo)
        cambios.append(n)
    if not cambios:
        return 0
    try:
        nuevo = yaml.safe_load(texto) or {}
        faltan = [n for n in cambios if n not in set(nuevo.get(clave, []))]
        if faltan:
            raise ValueError(f"tras editar, {faltan} no aparecen en {clave}")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: la edición dejaría la config inválida ({e}). No se cambió nada.")
        return 1
    efecto = ("entra(n) al respaldo diario" if accion == "adoptar"
              else "queda(n) como esperada(s) en la raíz y NO se respalda(n) ni se avisa más")
    if simular:
        print(f"\n  SIMULACIÓN: se agregaría(n) a {clave}: {', '.join(cambios)} — {efecto}. No se escribió nada.")
        return 0
    bak = _respaldar_config(ruta_config)
    _escribir_atomico(ruta_config, texto)
    print(f"\n  Listo: {', '.join(cambios)} agregada(s) a {clave} en {ruta_config} — {efecto}.")
    print(f"  Respaldo de la config anterior: {bak.name} (se conservan los últimos {BACKUPS_DE_CONFIG_MAX}).")
    return 0


def _historial_path_permitido(historial_path_resuelto: Path, raiz: Path) -> bool:
    """Contiene la escritura del historial a ubicaciones esperadas: dentro de
    la raíz escaneada, o en los directorios de config/datos del propio iee.
    Evita que un iee.config.yml (auto-detectado, sin opt-in explícito) pueda
    apuntar historial_path a una ruta arbitraria del sistema."""
    bases = (
        raiz.resolve(),
        (Path.home() / ".config" / "iee").resolve(),
        (Path.home() / ".local" / "share" / "iee").resolve(),
    )
    return any(
        historial_path_resuelto == base or base in historial_path_resuelto.parents
        for base in bases
    )


def registrar_historial(resultado: "ResultadoIEE", historial_path: Path, raiz: Path) -> None:
    """Agrega el score de esta corrida al historial (best-effort, no bloquea)."""
    try:
        # Resolver una sola vez y operar siempre sobre esa misma ruta ya
        # resuelta: valida y usa el mismo Path en todas las operaciones de
        # filesystem para no reabrir una ventana TOCTOU entre el chequeo del
        # allow-list y las llamadas que siguen (cada una re-resolvería
        # symlinks por su cuenta si partiéramos de la ruta sin resolver).
        historial_path = historial_path.resolve()
        if not _historial_path_permitido(historial_path, raiz):
            print(
                f"AVISO: historial_path ({historial_path}) está fuera de la raíz escaneada "
                f"o de ~/.config/iee y ~/.local/share/iee — no se escribe el historial."
            )
            return
        historial = []
        if historial_path.exists():
            historial = json.loads(historial_path.read_text(encoding="utf-8"))
        historial.append({
            "timestamp": resultado.timestamp,
            "iee": resultado.iee,
            "nivel": resultado.nivel,
            "sin_catalogar": len(resultado.proyectos_sin_catalogar),
            "huerfanos": len(resultado.archivos_huerfanos),
            "fantasmas": len(resultado.carpetas_fantasma),
        })
        historial = historial[-HISTORIAL_MAX:]
        historial_path.parent.mkdir(parents=True, exist_ok=True)
        historial_path.write_text(
            json.dumps(historial, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass  # el historial es un extra, nunca debe romper una corrida normal


@dataclass
class ResultadoIEE:
    ruta: str
    timestamp: str
    total_archivos: int = 0
    total_dirs: int = 0
    profundidad_promedio: float = 0.0
    profundidad_max: int = 0
    archivos_huerfanos: list = field(default_factory=list)
    carpetas_fantasma: list = field(default_factory=list)
    proyectos_sin_catalogar: list = field(default_factory=list)
    carpetas_por_tamano: dict = field(default_factory=dict)
    dominantes: dict = field(default_factory=dict)
    dispersion: float = 0.0
    score_profundidad: int = 0
    score_dispersion: int = 0
    score_huerfanos: int = 0
    score_fantasmas: int = 0
    iee: int = 0
    nivel: str = ""
    recomendaciones: list = field(default_factory=list)


def escanear(raiz: Path, cfg: Config) -> ResultadoIEE:
    resultado = ResultadoIEE(
        ruta=str(raiz),
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    profundidades = []
    archivos_por_dir: dict[str, int] = {}

    # Drift: carpetas de primer nivel que no son ruido conocido, ni
    # esperadas, ni proyectos catalogados. Señal temprana de "carpeta nueva
    # sin agregar a proyectos_conocidos", antes de que dependa de que
    # alguien se acuerde (por ejemplo, antes de que un script de backup la
    # siga saltando en silencio).
    for entrada in sorted(os.listdir(raiz)):
        ruta_entrada = raiz / entrada
        if not ruta_entrada.is_dir():
            continue
        if entrada.startswith("."):
            continue
        if entrada in cfg.excluir_dirs or entrada in cfg.raiz_esperados or entrada in cfg.proyectos_conocidos:
            continue
        resultado.proyectos_sin_catalogar.append(entrada)

    for dirpath, dirnames, filenames in os.walk(raiz):
        # Filtrar directorios excluidos (in-place para que os.walk no los recorra)
        dirnames[:] = [
            d for d in dirnames
            if d not in cfg.excluir_dirs and not d.startswith(".")
        ]

        ruta_rel = Path(dirpath).relative_to(raiz)
        profundidad = len(ruta_rel.parts)

        if profundidad == 0:
            # Raíz: identificar archivos huérfanos (archivos sueltos no esperados)
            for f in filenames:
                if not f.startswith(".") and f not in cfg.raiz_esperados:
                    resultado.archivos_huerfanos.append(f)
            continue

        # Solo analizar hasta profundidad 4 para no enterrarse en el código
        if profundidad > 4:
            continue

        archivos_reales = [f for f in filenames if not f.startswith(".")]
        n_archivos = len(archivos_reales)

        resultado.total_archivos += n_archivos
        resultado.total_dirs += 1

        # Registrar profundidad de cada archivo
        for _ in archivos_reales:
            profundidades.append(profundidad)

        # Registrar densidad por directorio de primer nivel
        primer_nivel = ruta_rel.parts[0]
        if primer_nivel not in archivos_por_dir:
            archivos_por_dir[primer_nivel] = 0
        archivos_por_dir[primer_nivel] += n_archivos

        # Carpetas fantasma: dirs con ≤1 archivo (sin subcarpetas)
        n_subdirs = len([d for d in dirnames if d not in cfg.excluir_dirs])
        if n_archivos <= 1 and n_subdirs == 0 and profundidad <= 2:
            resultado.carpetas_fantasma.append(str(ruta_rel))

    # Métricas
    if profundidades:
        resultado.profundidad_promedio = round(statistics.mean(profundidades), 2)
        resultado.profundidad_max = max(profundidades)

    if len(archivos_por_dir) > 1:
        resultado.carpetas_por_tamano = dict(
            sorted(archivos_por_dir.items(), key=lambda x: x[1], reverse=True)
        )
        # Dispersión con IQR (robusto a outliers de proyectos grandes legítimos)
        vals_dispersion = sorted(
            v for k, v in archivos_por_dir.items() if k not in DIRS_TRANSITORIOS
        )
        if len(vals_dispersion) >= 4:
            p25 = vals_dispersion[len(vals_dispersion) // 4]
            p75 = vals_dispersion[3 * len(vals_dispersion) // 4]
            iqr = p75 - p25
            resultado.dispersion = round(float(iqr), 1)
            # Identificar dominantes: dirs con archivos > p75 + 1.5*IQR
            umbral_dominante = p75 + 1.5 * iqr
            resultado.dominantes = {
                k: v for k, v in archivos_por_dir.items()
                if v > umbral_dominante and k not in DIRS_TRANSITORIOS
            }
        else:
            resultado.dispersion = 0.0
            resultado.dominantes = {}

    # Scores parciales (0=perfecto, 100=caótico)
    # Profundidad: ideal 2.0–3.5
    p = resultado.profundidad_promedio
    if p <= 0:
        resultado.score_profundidad = 50
    elif 2.0 <= p <= 3.5:
        resultado.score_profundidad = 0
    elif p < 2.0:
        resultado.score_profundidad = int((2.0 - p) / 2.0 * 50)
    else:
        resultado.score_profundidad = min(100, int((p - 3.5) / 1.5 * 60))

    # Dispersión basada en IQR: ≤30 = equilibrado, ≤80 = aceptable, >150 = caótico
    d = resultado.dispersion
    if d <= 30:
        resultado.score_dispersion = 0
    elif d <= 80:
        resultado.score_dispersion = int((d - 30) / 50 * 50)
    else:
        resultado.score_dispersion = min(100, int(50 + (d - 80) / 70 * 50))

    # Huérfanos
    n_h = len(resultado.archivos_huerfanos)
    resultado.score_huerfanos = min(100, n_h * 15)

    # Fantasmas
    n_f = len(resultado.carpetas_fantasma)
    resultado.score_fantasmas = min(100, n_f * 10)

    # IEE compuesto (pesos: profundidad 30%, dispersión 30%, huérfanos 25%, fantasmas 15%)
    resultado.iee = round(
        resultado.score_profundidad * 0.30
        + resultado.score_dispersion * 0.30
        + resultado.score_huerfanos * 0.25
        + resultado.score_fantasmas * 0.15
    )

    if resultado.iee <= 20:
        resultado.nivel = "ÓPTIMO"
    elif resultado.iee <= 40:
        resultado.nivel = "ACEPTABLE"
    elif resultado.iee <= 65:
        resultado.nivel = "ATENCIÓN"
    else:
        resultado.nivel = "REORGANIZAR"

    # Recomendaciones
    if resultado.proyectos_sin_catalogar:
        resultado.recomendaciones.append(
            f"⚠️ {len(resultado.proyectos_sin_catalogar)} carpeta(s) sin catalogar: "
            + ", ".join(resultado.proyectos_sin_catalogar)
            + ". Revísalas con `iee.py --clasificar`; adopta con `--adoptar NOMBRE` (entra al respaldo) "
            "o ignora con `--ignorar NOMBRE`."
        )

    if resultado.score_profundidad > 30:
        if resultado.profundidad_promedio > 3.5:
            resultado.recomendaciones.append(
                f"Archivos muy profundos (avg {resultado.profundidad_promedio} niveles). "
                "Considera aplanar subcarpetas poco usadas."
            )
        else:
            resultado.recomendaciones.append(
                "Archivos demasiado superficiales. Agrupa por proyecto/módulo."
            )

    if resultado.score_dispersion > 30:
        top = list(resultado.carpetas_por_tamano.items())[:3]
        top_str = ", ".join(f"{k} ({v})" for k, v in top)
        resultado.recomendaciones.append(
            f"Distribución desigual. Carpetas con más archivos: {top_str}."
        )

    if resultado.archivos_huerfanos:
        resultado.recomendaciones.append(
            f"{len(resultado.archivos_huerfanos)} archivo(s) suelto(s) en la raíz: "
            + ", ".join(resultado.archivos_huerfanos[:5])
            + ("..." if len(resultado.archivos_huerfanos) > 5 else "")
        )

    if resultado.carpetas_fantasma:
        resultado.recomendaciones.append(
            f"{len(resultado.carpetas_fantasma)} carpeta(s) casi vacías: "
            + ", ".join(resultado.carpetas_fantasma[:4])
        )

    if not resultado.recomendaciones:
        resultado.recomendaciones.append("Estructura en buen estado. Sin acciones urgentes.")

    return resultado


def imprimir(r: ResultadoIEE) -> None:
    barra = "█" * (r.iee // 5) + "░" * (20 - r.iee // 5)
    print(f"\n{'='*50}")
    print(f"  IEE — Índice de Entropía Estructural")
    print(f"  {r.ruta}  ·  {r.timestamp}")
    print(f"{'='*50}")
    print(f"\n  [{barra}] {r.iee}/100  →  {r.nivel}\n")
    print(f"  Archivos analizados : {r.total_archivos}")
    print(f"  Directorios         : {r.total_dirs}")
    print(f"  Profundidad prom.   : {r.profundidad_promedio} niveles  (score: {r.score_profundidad})")
    print(f"  Dispersión (IQR)    : {r.dispersion}  (score: {r.score_dispersion})")
    print(f"  Huérfanos en raíz   : {len(r.archivos_huerfanos)}  (score: {r.score_huerfanos})")
    print(f"  Carpetas fantasma   : {len(r.carpetas_fantasma)}  (score: {r.score_fantasmas})")
    print(f"  Sin catalogar       : {len(r.proyectos_sin_catalogar)}")
    if r.dominantes:
        print(f"\n  Dominantes (outliers legítimos, excluidos del score):")
        for k, v in sorted(r.dominantes.items(), key=lambda x: x[1], reverse=True):
            print(f"    {k:<25} {v} archivos")
    if r.carpetas_por_tamano:
        top5 = [(k, v) for k, v in r.carpetas_por_tamano.items() if k not in r.dominantes][:5]
        print(f"\n  Top carpetas regulares:")
        for k, v in top5:
            print(f"    {k:<25} {v} archivos")
    print(f"\n  Recomendaciones:")
    for rec in r.recomendaciones:
        print(f"    • {rec}")
    print(f"\n{'='*50}\n")


def _es_movible(nombre: str) -> bool:
    """Devuelve True si el archivo en raíz es seguro de mover a _inbox."""
    p = Path(nombre)
    # Nunca mover extensiones protegidas (credenciales, scripts, config)
    if p.suffix.lower() in EXTENSIONES_PROTEGIDAS:
        return False
    # Solo mover si tiene marcador de backup en el nombre
    if any(nombre.endswith(s) for s in SUFIJOS_BACKUP):
        return True
    # O si tiene extensión explícitamente movible (datos/office)
    if p.suffix.lower() in EXTENSIONES_MOVIBLES:
        return True
    return False


@dataclass
class AccionOrden:
    tipo: str          # "mover_inbox" | "eliminar_vacia"
    origen: str
    destino: str = ""
    motivo: str = ""


def planificar(raiz: Path, resultado: ResultadoIEE) -> list[AccionOrden]:
    """Genera plan de acciones ordenadas. Nunca toca directorios de proyectos."""
    acciones: list[AccionOrden] = []

    # 1. Archivos huérfanos en raíz → <raiz>/_inbox/
    inbox = raiz / "_inbox"
    for nombre in resultado.archivos_huerfanos:
        ruta_archivo = raiz / nombre
        if ruta_archivo.exists() and _es_movible(nombre):
            acciones.append(AccionOrden(
                tipo="mover_inbox",
                origen=str(ruta_archivo),
                destino=str(inbox / nombre),
                motivo="archivo suelto en raíz, no está en raiz_esperados",
            ))

    # 2. Carpetas fantasma → eliminar si están completamente vacías
    for dir_rel in resultado.carpetas_fantasma:
        ruta_dir = raiz / dir_rel
        if ruta_dir.exists() and ruta_dir.is_dir():
            contenido = list(ruta_dir.iterdir())
            if len(contenido) == 0:
                acciones.append(AccionOrden(
                    tipo="eliminar_vacia",
                    origen=str(ruta_dir),
                    motivo="directorio vacío (0 archivos, 0 subcarpetas)",
                ))

    return acciones


def ejecutar(acciones: list[AccionOrden], raiz: Path, dry_run: bool = True) -> None:
    """Muestra o ejecuta las acciones del plan."""
    if not acciones:
        print("\n  Sin acciones pendientes. Estructura ya ordenada.\n")
        return

    inbox = raiz / "_inbox"
    log_path = raiz / "_inbox" / f"iee-ordenar-{datetime.now().strftime('%Y%m%d-%H%M')}.log"
    log_lines: list[str] = []

    modo = "DRY-RUN (sin cambios)" if dry_run else "APLICANDO CAMBIOS"
    print(f"\n{'='*50}")
    print(f"  IEE Ordenar — {modo}")
    print(f"  {len(acciones)} acción(es) planificada(s)")
    print(f"{'='*50}\n")

    for acc in acciones:
        if acc.tipo == "mover_inbox":
            label = f"  MOVER  {acc.origen}\n         → {acc.destino}"
            print(label)
            print(f"         ({acc.motivo})")
            if not dry_run:
                inbox.mkdir(exist_ok=True)
                dest = Path(acc.destino)
                # Evitar colisiones de nombre
                if dest.exists():
                    stem = dest.stem
                    suffix = dest.suffix
                    dest = inbox / f"{stem}_{datetime.now().strftime('%H%M%S')}{suffix}"
                shutil.move(acc.origen, dest)
                log_lines.append(f"MOVIDO: {acc.origen} → {dest}")

        elif acc.tipo == "eliminar_vacia":
            label = f"  BORRAR {acc.origen}"
            print(label)
            print(f"         ({acc.motivo})")
            if not dry_run:
                Path(acc.origen).rmdir()
                log_lines.append(f"ELIMINADO: {acc.origen}")

    if not dry_run and log_lines:
        inbox.mkdir(exist_ok=True)
        log_path.write_text(
            f"IEE Ordenar — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            + "\n".join(log_lines) + "\n",
            encoding="utf-8",
        )
        print(f"\n  Log guardado: {log_path}")
        print(f"\n  Listo. Vuelve a correr sin --fix para ver el nuevo IEE.\n")
    elif dry_run:
        print(f"\n  Esto es un dry-run. Para aplicar: python3 {__file__} --fix --apply\n")

    print(f"{'='*50}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="IEE — Índice de Entropía Estructural")
    parser.add_argument("--path", default=".", help="Ruta a analizar (default: directorio actual)")
    parser.add_argument("--config", help="Ruta a iee.config.yml (default: autodetecta, ver docstring)")
    parser.add_argument("--json", action="store_true", help="Salida JSON del análisis")
    parser.add_argument("--fix", action="store_true", help="Generar plan de reorganización (dry-run)")
    parser.add_argument("--apply", action="store_true", help="Ejecutar el plan (requiere --fix)")
    parser.add_argument("--no-history", action="store_true", help="No registrar esta corrida en el historial")
    parser.add_argument(
        "--list-conocidos", action="store_true",
        help="Imprime proyectos_conocidos (uno por línea, solo los que existen como carpeta) "
             "— útil para que un script de backup derive su lista de directorios a respaldar.",
    )
    parser.add_argument("--clasificar", action="store_true",
                        help="Explica cada carpeta sin catalogar (activa/copia/revisar) y sugiere el comando.")
    parser.add_argument("--adoptar", nargs="+", metavar="NOMBRE",
                        help="Agrega carpeta(s) a proyectos_conocidos (entran al respaldo).")
    parser.add_argument("--ignorar", nargs="+", metavar="NOMBRE",
                        help="Agrega carpeta(s) a raiz_esperados (esperadas, sin respaldo ni aviso).")
    parser.add_argument("--motivo", default="", help="Texto que queda como comentario junto al nombre.")
    parser.add_argument("--simular", action="store_true", help="Con --adoptar/--ignorar: muestra el cambio sin escribir.")
    parser.add_argument("--confirmar-datos", action="store_true",
                        help="Con --adoptar: acepta respaldar carpetas con bases de datos, planillas o .env.")
    args = parser.parse_args()

    raiz = Path(args.path).resolve()
    ruta_config = _localizar_config(args.config)
    cfg = cargar_config(ruta_config)

    if args.adoptar or args.ignorar:
        if args.adoptar and args.ignorar:
            parser.error("usa --adoptar o --ignorar, no ambos a la vez")
        accion = "adoptar" if args.adoptar else "ignorar"
        raise SystemExit(catalogar(accion, args.adoptar or args.ignorar, raiz, ruta_config, args.motivo,
                                   args.simular, args.confirmar_datos))

    if args.list_conocidos:
        for nombre in sorted(cfg.proyectos_conocidos):
            if (raiz / nombre).is_dir():
                print(nombre)
        return

    resultado = escanear(raiz, cfg)

    if args.clasificar:
        imprimir_clasificacion(raiz, resultado.proyectos_sin_catalogar, cfg)
        return

    if not args.no_history:
        historial_path = cfg.historial_path or (raiz / ".iee-history.json")
        registrar_historial(resultado, historial_path, raiz)

    if args.json:
        print(json.dumps(asdict(resultado), ensure_ascii=False, indent=2))
    elif args.fix:
        imprimir(resultado)
        acciones = planificar(raiz, resultado)
        dry_run = not args.apply
        ejecutar(acciones, raiz, dry_run=dry_run)
    else:
        imprimir(resultado)


if __name__ == "__main__":
    main()
