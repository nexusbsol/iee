# IEE — Índice de Entropía Estructural

Script en Python (sin dependencias pesadas, solo `pyyaml` opcional) que mide
qué tan ordenado está un árbol de carpetas y devuelve un score 0-100.

Nace de un problema concreto: en un entorno con muchos proyectos activos
(bots, apps, scripts) es fácil que la estructura se desordene con el tiempo
sin que nadie lo note a tiempo — carpetas nuevas que nadie cataloga, archivos
sueltos en la raíz, subcarpetas casi vacías que quedaron a medio camino. IEE
lo convierte en un número y una lista de recomendaciones concretas, en vez de
depender de la memoria de alguien.

## Qué mide

- **Profundidad promedio** de los archivos (ideal 2.0–3.5 niveles).
- **Dispersión** entre carpetas de primer nivel, calculada con rango
  intercuartílico (IQR) — robusta a proyectos grandes legítimos (los
  identifica como "dominantes" y los excluye del score en vez de penalizarlos).
- **Archivos huérfanos**: archivos sueltos en la raíz que no están en tu
  lista de esperados.
- **Carpetas fantasma**: directorios con ≤1 archivo y sin subcarpetas.
- **Carpetas sin catalogar**: carpetas de primer nivel que no reconoces —
  señal temprana de "algo nuevo apareció y nadie lo registró", útil por
  ejemplo para que un script de backup no las salte en silencio.

## Uso

```bash
python3 iee.py                          # mide el directorio actual
python3 iee.py --path /otra/ruta        # mide otra ruta
python3 iee.py --json                   # salida JSON (para integrarlo en otro script)
python3 iee.py --fix                    # dry-run: plan de orden, no toca nada
python3 iee.py --fix --apply            # ejecuta el plan (mueve huérfanos a _inbox/, borra carpetas vacías)
python3 iee.py --list-conocidos         # lista tus proyectos_conocidos que existen como carpeta
python3 iee.py --no-history             # no registrar esta corrida en el historial
python3 iee.py --clasificar              # qué es cada carpeta sin catalogar y qué hacer con ella
python3 iee.py --adoptar NOMBRE         # la agrega a proyectos_conocidos (entra a tu respaldo)
python3 iee.py --ignorar NOMBRE         # la agrega a raiz_esperados (esperada, sin respaldo)
```

`--fix` nunca toca directorios de proyectos ni archivos con extensiones
sensibles (`.env`, `.key`, `.pem`, scripts, etc.) — solo mueve archivos con
marcador explícito de backup/temporal (`.bak`, `.old`, `.tmp`, `.csv`, `.log`,
etc.) y borra carpetas realmente vacías.

## Configuración

Todo lo específico de tu entorno (nombres de tus proyectos, tus archivos
propios en la raíz) va en un `iee.config.yml` que **no** vives en este repo —
copia `iee.config.example.yml`, ajústalo, y déjalo en:

1. La ruta que pases con `--config /ruta/a/config.yml`, o
2. `$IEE_CONFIG` (variable de entorno), o
3. `./iee.config.yml` (directorio desde el que corres el script), o
4. `~/.config/iee/config.yml`

Sin config, IEE corre igual con defaults genéricos (ruido técnico común:
`.git`, `node_modules`, `__pycache__`, etc.) pero sin `proyectos_conocidos`
propios — el primer run va a mostrar todas tus carpetas de primer nivel como
"sin catalogar", lo cual es esperado: agrégalas a tu config a medida que las
revisas.

## Catastro asistido (carpetas sin catalogar)

Cuando aparece una carpeta nueva, `--clasificar` la mira sin modificar nada y la ubica en una de tres clases:
**activa** (la menciona tu crontab o una unidad systemd), **copia o prueba** (por el nombre: `.bak`, `test`,
`backup`...) o **a revisar**. También informa tamaño, si tiene git y si contiene bases de datos, planillas o `.env`,
y sugiere el comando.

- `--adoptar NOMBRE` la agrega a `proyectos_conocidos` (si tu respaldo deriva su lista de ahí, entra al respaldo).
  Si la carpeta tiene datos o `.env` se niega, salvo que agregues `--confirmar-datos`: respaldar copia todo eso fuera.
- `--ignorar NOMBRE` la agrega a `raiz_esperados`: queda aceptada pero sin respaldo ni más avisos.
- Ambos aceptan `--motivo "texto"` (queda como comentario junto al nombre) y `--simular` (muestra el cambio sin escribir).
- Editan tu config como texto: conservan comentarios y orden, validan el YAML antes de escribir, escriben de forma
  atómica, respaldan la versión anterior (`.bak-iee-<fecha>`, se conservan las últimas 3) y son idempotentes.
- Nunca borran ni mueven carpetas; eso sigue siendo decisión tuya (o de `--fix`).

Pruebas: `python3 -m unittest tests/test_catalogo.py`.

## Historial

Cada corrida (salvo con `--no-history`) agrega una entrada con timestamp y
score al historial (`historial_path` en tu config, o
`<ruta-analizada>/.iee-history.json` por defecto), para poder ver tendencia
en el tiempo en vez de solo la foto del momento.

## Por qué no es una API / servicio

IEE está pensado para invocarse por línea de comandos desde otro script o
cron (por ejemplo, un backup diario que solo respalda lo que está en
`proyectos_conocidos`, o un reporte semanal por chat). No expone un servicio
HTTP a propósito: agregar un puerto y un proceso corriendo permanentemente no
tiene beneficio cuando los consumidores son procesos locales del mismo host.

## Licencia

MIT.
