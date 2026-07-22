# IEE — Índice de Entropía Estructural

IEE is a zero-dependency Python 3 CLI tool (optional `pyyaml`) that measures how
orderly a directory tree is and returns a score from 0 (perfect) to 100 (chaotic).
It scans any filesystem path, identifies structural problems — orphan files, ghost
directories, uncatalogued folders, uneven dispersion — and produces actionable
recommendations.

The project lives entirely in [`iee.py`](../iee.py) (~524 lines). There is no API,
no server, and no framework — by design. IEE is meant to be invoked from cron jobs
or other scripts (e.g., a daily backup that only touches `proyectos_conocidos`).

## What IEE measures

| Metric | What it detects | Ideal range |
|---|---|---|
| **Profundidad promedio** | Average file depth in the tree | 2.0–3.5 levels |
| **Dispersión (IQR)** | File-count spread across top-level folders, via interquartile range | ≤30 |
| **Archivos huérfanos** | Loose files in the root not in your `raiz_esperados` list | 0 |
| **Carpetas fantasma** | Directories with ≤1 file and no subdirectories | 0 |
| **Sin catalogar** | Top-level folders not in `proyectos_conocidos`, `excluir_dirs`, or `raiz_esperados` | 0 |

Large legitimate projects are detected as **dominantes** (outliers beyond
p75 + 1.5×IQR) and excluded from the dispersion score rather than penalised.

## Quick usage

```bash
python3 iee.py                          # scan current directory
python3 iee.py --path /otra/ruta        # scan another path
python3 iee.py --json                   # JSON output for scripting
python3 iee.py --fix                    # dry-run: reorganisation plan
python3 iee.py --fix --apply            # execute the plan
python3 iee.py --list-conocidos         # list known projects that exist as folders
python3 iee.py --no-history             # skip history logging
```

## Configuration

Copy [`iee.config.example.yml`](../iee.config.example.yml) to one of these
locations (checked in order):

1. Path passed via `--config /ruta/a/config.yml`
2. `$IEE_CONFIG` environment variable
3. `./iee.config.yml` (current working directory)
4. `~/.config/iee/config.yml`

Without a config file, IEE runs with sensible defaults (excludes `.git`,
`node_modules`, `__pycache__`, etc.) but has no `proyectos_conocidos`, so the
first run flags every top-level folder as "sin catalogar" — this is expected;
add them to your config as you review each one.

Key config fields:

- `proyectos_conocidos` — list of expected top-level project folders
- `raiz_esperados` — expected loose files/dirs in the root (added to built-in defaults)
- `excluir_dirs` — directories to skip during analysis (added to built-in defaults)
- `historial_path` — where to store score history (default: `<scanned-path>/.iee-history.json`)

## History

Each run (unless `--no-history`) appends a timestamped entry with the IEE score,
level, and key counts to the history file. The history is capped at 104 entries
(~2 years of weekly reports) so it stays bounded.

## `--fix` safety model

The `--fix` dry-run generates a reorganisation plan. `--fix --apply` executes it.
The plan **never** touches project directories or files with sensitive extensions
(`.env`, `.key`, `.pem`, `.sh`, `.py`, `.json`, `.md`, etc.). It only:

- Moves orphan root files with backup/temp markers (`.bak`, `.old`, `.tmp`, `.csv`,
  `.log`, etc.) to `_inbox/`
- Deletes directories that are completely empty (0 files, 0 subdirectories)

See [Scoring & Fix Safety](scoring-and-fix-safety.md) for the full algorithm.

## Sections

- [Scoring & Fix Safety](scoring-and-fix-safety.md) — How the IEE composite score
  is calculated, score weights, level thresholds, and the exact rules governing
  what `--fix` will and will not move or delete.

## Source reference

| File | Purpose |
|---|---|
| [`iee.py`](../iee.py) | Entire application: scanning, scoring, reporting, fix planning/execution, config loading, history |
| [`iee.config.example.yml`](../iee.config.example.yml) | Configuration template with placeholder values |
| [`.gitignore`](../.gitignore) | Ignores `iee.config.yml`, history files, `__pycache__`, and `.github/` |
