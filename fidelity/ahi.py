"""Effective Hash Coverage (Uh) -- FR-6.

For InnoDB cells with the Adaptive Hash Index on, Uh is the share of index
lookups during the measured phases that were answered by the AHI:

    Uh = d_hash / (d_hash + d_btree)

where d_hash and d_btree are the increases of the InnoDB metrics
`adaptive_hash_searches` and `adaptive_hash_searches_btree` between a
snapshot taken just before and just after the measured phase.

Fixed values:
    * InnoDB with AHI off       -> Uh = 0.0 (no hash path exists)
    * MEMORY engine             -> Uh = 1.0 if a HASH index was built, else 0.0
                                   (MEMORY has no AHI; the built structure
                                   decides every lookup)

The counters belong to the `module_adaptive_hash` monitor group. Enable it
before measuring:
    SET GLOBAL innodb_monitor_enable = 'module_adaptive_hash';
Counter semantics are still to be confirmed against EXPLAIN and
performance_schema in the pilot runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

HASH_COUNTER = "adaptive_hash_searches"
BTREE_COUNTER = "adaptive_hash_searches_btree"

COUNTERS_SQL = (
    "SELECT NAME, COUNT, STATUS FROM information_schema.INNODB_METRICS "
    "WHERE NAME IN (%s, %s)"
)


class CounterError(RuntimeError):
    pass


@dataclass(frozen=True)
class CounterSnapshot:
    hash_searches: int
    btree_searches: int


def read_counters(cursor) -> CounterSnapshot:
    cursor.execute(COUNTERS_SQL, (HASH_COUNTER, BTREE_COUNTER))
    rows = cursor.fetchall()
    values, disabled = {}, []
    for r in rows:
        name, count, status = (r["NAME"], r["COUNT"], r["STATUS"]) if isinstance(r, Mapping) else r
        values[name] = int(count)
        if str(status).lower() != "enabled":
            disabled.append(name)
    if disabled:
        raise CounterError(
            f"InnoDB metrics not enabled: {disabled}. "
            "Run SET GLOBAL innodb_monitor_enable = 'module_adaptive_hash';")
    missing = {HASH_COUNTER, BTREE_COUNTER} - values.keys()
    if missing:
        raise CounterError(f"missing InnoDB metrics: {sorted(missing)}")
    return CounterSnapshot(values[HASH_COUNTER], values[BTREE_COUNTER])


def compute_uh(engine: str, ahi_enabled: bool, built_type: str,
               before: Optional[CounterSnapshot] = None,
               after: Optional[CounterSnapshot] = None) -> Optional[float]:
    """Return Uh in [0, 1], or None if no lookups were recorded."""
    engine = engine.upper()
    if engine == "MEMORY":
        return 1.0 if built_type.upper() == "HASH" else 0.0
    if engine != "INNODB":
        raise ValueError(f"unsupported engine {engine!r}")
    if not ahi_enabled:
        return 0.0
    if before is None or after is None:
        raise ValueError("AHI-on InnoDB cells need before/after counter snapshots")

    d_hash = after.hash_searches - before.hash_searches
    d_btree = after.btree_searches - before.btree_searches
    if d_hash < 0 or d_btree < 0:
        raise CounterError(
            "counter decreased between snapshots (server restart or "
            "innodb_monitor_reset during the phase?)")
    total = d_hash + d_btree
    if total == 0:
        return None
    return d_hash / total
