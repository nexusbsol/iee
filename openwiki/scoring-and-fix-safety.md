# Scoring & Fix Safety

This page documents the IEE composite scoring algorithm, level thresholds, and
the exact safety rules that govern `--fix` behaviour. All logic lives in
[`iee.py`](../iee.py).

## Composite score formula

The final IEE score (0–100, where 0 is perfect) is a weighted sum of four
partial scores:

| Component | Weight | What it measures |
|---|---|---|
| `score_profundidad` | 30% | Average file depth deviation from the 2.0–3.5 ideal band |
| `score_dispersion` | 30% | IQR-based spread of file counts across top-level folders |
| `score_huerfanos` | 25% | Count of orphan files in the root |
| `score_fantasmas` | 15% | Count of near-empty ghost directories |

### Profundidad (depth)

- Ideal range: **2.0–3.5** → score 0
- Below 2.0 (too shallow): `int((2.0 - p) / 2.0 * 50)` — penalises flat structures
- Above 3.5 (too deep): `min(100, int((p - 3.5) / 1.5 * 60))` — penalises deeply nested files
- If no files found (p ≤ 0): score 50

Only files up to depth 4 are counted; deeper files are skipped to avoid
penalising legitimate deep source trees.

### Dispersión (dispersion)

Calculated using the **interquartile range (IQR)** of file counts per top-level
directory, excluding the `_inbox` transitory directory:

- Requires ≥4 directories to compute; otherwise dispersion = 0
- p25 and p75 are computed by index position on the sorted values
- IQR = p75 − p25
- **Dominantes** (legitimate large projects): dirs with file count > p75 + 1.5×IQR
  — these are listed in the report but excluded from the dispersion score
- Score mapping: ≤30 → 0, ≤80 → linear 0–50, >80 → 50 + linear up to 100

### Huérfanos (orphans)

Each orphan file (loose file in root not in `raiz_esperados` and not hidden)
adds 15 points, capped at 100.

### Fantasmas (ghosts)

Each ghost directory (≤1 file, no subdirectories, depth ≤ 2) adds 10 points,
capped at 100.

## Level thresholds

| IEE score | Level |
|---|---|
| ≤ 20 | ÓPTIMO |
| ≤ 40 | ACEPTABLE |
| ≤ 65 | ATENCIÓN |
| > 65 | REORGANIZAR |

## Recommendations engine

After scoring, IEE generates human-readable recommendations when any of these
conditions are true:

- Uncatalogued folders exist → suggests adding them to `proyectos_conocidos`
- Depth score > 30 → suggests flattening or grouping
- Dispersion score > 30 → lists the 3 largest folders
- Orphan files exist → lists up to 5
- Ghost directories exist → lists up to 4
- If none of the above → "Estructura en buen estado. Sin acciones urgentes."

## Fix safety model

`--fix` generates a plan of `AccionOrden` objects. `--apply` executes them.
Two action types exist:

### `mover_inbox` — move orphan to `_inbox/`

An orphan root file is only considered movable if **all** of these hold:

1. Its extension is **not** in `EXTENSIONES_PROTEGIDAS` — this blocks
   credentials, scripts, config, and plain-text docs:

   `.pass`, `.key`, `.pem`, `.pfx`, `.p12`, `.crt`, `.cer`, `.sh`, `.py`,
   `.env`, `.json`, `.toml`, `.cfg`, `.conf`, `.ini`, `.md`, `.txt`

2. It has a backup suffix in its name: `-bak`, `.bak`, `.old`, `.orig`,
   `.backup`, `.disabled`

   **OR** its extension is in `EXTENSIONES_MOVIBLES`:
   `.bak`, `.bak-`, `.xlsx`, `.xls`, `.csv`, `.tmp`, `.log`

On `--apply`, files are moved to `<root>/_inbox/`. Name collisions are resolved
by appending a timestamp (`_HHMMSS`) to the stem.

### `eliminar_vacia` — delete empty directory

A ghost directory is only deleted if, at execution time, it is **completely
empty** (0 files, 0 subdirectories). The plan is generated from the scan, but
the deletion re-checks the directory state before acting.

### What `--fix` never touches

- Project directories (`proyectos_conocidos`)
- Files with protected extensions (credentials, scripts, config, docs)
- Hidden files (those starting with `.`)
- Directories with any content remaining

### Execution logging

When `--apply` runs, a log file is written to
`<root>/_inbox/iee-ordenar-YYYYMMDD-HHMM.log` recording every move and deletion.
