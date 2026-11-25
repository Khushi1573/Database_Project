# Measuring Index-Request Fidelity (COMP.8157 Team 2)

**Hash-Index Substitution and the Adaptive Hash Index in MySQL InnoDB versus MEMORY under Shifting Read/Write Workloads**

Implemented from the requirements in `docs/D_4_1_TEAM_2.pdf` and the design blueprint in `docs/D4_2_TEAM_2.pdf`.

## Experimental Cells

| Cell | Engine | Requested index | AHI |
|---|---|---|---|
| A | InnoDB | BTREE | off |
| B | InnoDB | BTREE | on |
| C | InnoDB | HASH | off |
| D | InnoDB | HASH | on |
| E | MEMORY | BTREE | n/a |
| F | MEMORY | HASH | n/a |

Each run creates `bench.sbtest1` before bulk loading, audits Structural Fidelity (`Fs`), runs a warm-up followed by three workload phases (`95% -> 10% -> 95%` reads), samples telemetry every second (`1 s`), computes Effective Hash Coverage (`Uh`) and AHI adaptation times, cross-validates Cell A vs Cell C (`FR-7`), and generates `results/aggregated/summary.csv`, `results/figures/*.png`, and `results/report.html`.

## Setup

```bash
cp .env.example .env   # fill in passwords; .env is gitignored
pip install -r requirements.txt
```

Docker is required for `run` and `matrix` (provisions pinned `mysql:8.4` containers per cell/run). `validate`, `report` (over existing raw files), and offline unit tests run without Docker or MySQL.

## CLI Usage (D.4.2 section 6.1)

```bash
# Validate study configuration (exit 0 ok, 2 invalid)
python -m src.cli validate --config configs/study.yaml

# Run a single cell and repeat (exit 0, 3, 4)
python -m src.cli run --config configs/study.yaml --cell A --repeat 1 --results results

# Run the full randomized 6 x 5 matrix with resumability & 1 retry (exit 0, 2, 3, 4)
python -m src.cli matrix --config configs/study.yaml --results results

# Aggregate raw results and write summary.csv, figures/*.png, and report.html (exit 0, 4)
python -m src.cli report --results results

# Remove any leftover containers from interrupted runs (exit 0)
python -m src.cli clean
```

Or via Docker Compose:

```bash
docker compose -f docker/docker-compose.yml build runner
docker compose -f docker/docker-compose.yml run --rm runner matrix --config configs/study.yaml --results results
```

## Tests

```bash
python -m pytest -q                    # offline unit tests (default)
python -m pytest -m integration -q     # live Docker + MySQL integration test
```

## Repository Layout

- `configs/study.yaml` — study parameters and six-cell matrix (`FR-1`..`FR-3`)
- `src/config_manager.py` — YAML validation and Docker MySQL 8.4 provisioning (`FR-1`..`FR-3`, `NFR-5`, `NFR-7`, `NFR-9`)
- `src/workload_driver.py` — schema rendering, seeded loader, MEMORY 1M-row fallback, sysbench runner (`FR-8`, `FR-9`, `FR-11`)
- `src/telemetry.py` — 1s sampler, continuity check, raw CSV/JSON writer (`FR-13`, `FR-16`, `AC-8`)
- `src/orchestrator.py` — shuffled schedule, derived seeds, resumability, 1 retry (`FR-10`, `AC-6`)
- `src/reporting.py` — aggregation, Cell A vs C cross-validation, Wireframe S1 report (`FR-7`, `FR-14`..`FR-17`)
- `src/cli.py` — `validate`, `run`, `matrix`, `report`, `clean` CLI
- `fidelity/` — `auditor.py`, `ahi.py`, `crossval.py`, `adaptation.py`, `aggregate.py`, `plots.py`
- `sql/schema.sql`, `lua/mixed_rw.lua`, `docker/` — database schema, sysbench Lua workload, container configs
- `tests/` — offline unit tests (`AC-1`, `AC-3`, `AC-4`, `AC-6`..`AC-10`) and marked integration test
- `docs/` — `D_4_1_TEAM_2.pdf`, `D4_2_TEAM_2.pdf`, `D3_1_Midterm_Project_Status_Report_Team2.pdf`
