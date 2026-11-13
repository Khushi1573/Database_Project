# Fidelity Auditor and Analysis (Malik Oyewole, Team 2)

Implements my modules for the COMP.8157 project, following D.4.2 (section 5.3 for
the metrics, section 6.4 for the stage interfaces).

| Module | Requirement | What it does |
|---|---|---|
| `fidelity/auditor.py` | FR-4, FR-5 | Reads the built index from `information_schema.statistics` and `SHOW INDEX`, checks they agree, computes Fs (type, ordered columns, uniqueness) |
| `fidelity/ahi.py` | FR-6 | Uh = Δadaptive_hash_searches / (Δadaptive_hash_searches + Δadaptive_hash_searches_btree); fixed values for MEMORY and AHI-off cells |
| `fidelity/crossval.py` | FR-7 | Cell A vs Cell C structural comparison; flags silent HASH→BTREE substitution |
| `fidelity/adaptation.py` | FR-14 | Adaptation time = first point after which the series stays within ε of its plateau for ≥ W samples |
| `fidelity/aggregate.py` | FR-15, FR-16, NFR-4 | Median + percentile bootstrap 95% CI (SciPy, 10,000 resamples, seed 8157); summary CSV keeps run ids and source files |
| `fidelity/plots.py` | FR-17 | One figure per metric: median dot with CI whisker per cell |
| `tests/` | AC-3 | Ground-truth fixture for the six cells plus unit tests (33 tests) |

## Run the tests

```bash
pip install numpy scipy matplotlib pytest
python -m pytest -q
```

No MySQL server is needed for the tests; the live functions take any DB-API
cursor (e.g. PyMySQL or mysql-connector).

## Live use (sketch)

```python
from fidelity.auditor import IndexSpec, audit
from fidelity.ahi import read_counters, compute_uh

res = audit(cur, "C", "adt", "t_c", IndexSpec("idx_k", "HASH", ("k",), False))
before = read_counters(cur)   # needs: SET GLOBAL innodb_monitor_enable = 'module_adaptive_hash';
# ... measured phase ...
after = read_counters(cur)
uh = compute_uh(res.engine, ahi_enabled=True, built_type=res.built.index_type,
                before=before, after=after)
```

## Still open (after pilots)

- Replace the fixture's synthetic rows with metadata captured from the pilot server.
- Confirm counter semantics against EXPLAIN and performance_schema on one pilot cell.
- Fix W and ε for the adaptation detector (defaults 5 and 0.05 are placeholders).

## Suggested commits (one per requirement)

1. `FR-4/FR-5: Fidelity Auditor - Fs from statistics and SHOW INDEX with cross-check`
2. `AC-3: ground-truth fixture for six-cell matrix and auditor tests`
3. `FR-6: Uh from InnoDB AHI counter deltas, fixed values for MEMORY/AHI-off`
4. `FR-7: Cell A vs C structural cross-validation`
5. `FR-15/FR-16: bootstrap median CI aggregation and traceable summary CSV`
6. `FR-14: AHI adaptation-time detector (W, epsilon parameters)`
7. `FR-17: per-metric summary figures`
