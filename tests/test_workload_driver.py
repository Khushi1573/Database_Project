"""MEMORY fallback logic, deterministic seeding, and schema rendering (FR-8, FR-11)."""
from __future__ import annotations

from src.workload_driver import choose_dataset, derive_seed, estimate_bytes, render_schema


def test_choose_dataset_no_fallback_for_innodb():
    rows, fallback = choose_dataset("InnoDB", 10_000_000, container_mem_limit_bytes=1)
    assert rows == 10_000_000
    assert fallback is False


def test_choose_dataset_memory_falls_back_when_too_big():
    big_rows = 10_000_000
    tiny_limit = estimate_bytes(big_rows)
    rows, fallback = choose_dataset("MEMORY", big_rows, container_mem_limit_bytes=tiny_limit)
    assert fallback is True
    assert rows == 1_000_000


def test_choose_dataset_memory_no_fallback_when_fits():
    rows_requested = 1_000_000
    huge_limit = estimate_bytes(rows_requested) * 100
    rows, fallback = choose_dataset("MEMORY", rows_requested, container_mem_limit_bytes=huge_limit)
    assert fallback is False
    assert rows == rows_requested


def test_derive_seed_is_deterministic():
    s1 = derive_seed(study_seed=123, cell_id="A", repeat=1)
    s2 = derive_seed(study_seed=123, cell_id="A", repeat=1)
    assert s1 == s2


def test_derive_seed_varies_by_cell_and_repeat():
    base = derive_seed(123, "A", 1)
    assert derive_seed(123, "B", 1) != base
    assert derive_seed(123, "A", 2) != base


def test_render_schema_memory_uses_index_on_pk_and_secondary():
    sql = render_schema("MEMORY", "HASH")
    assert "ENGINE=MEMORY" in sql
    assert "PRIMARY KEY (id) USING HASH" in sql
    assert "KEY idx_k (k) USING HASH" in sql
    assert "{{" not in sql


def test_render_schema_innodb_keeps_plain_pk():
    sql = render_schema("InnoDB", "HASH")
    assert "ENGINE=InnoDB" in sql
    assert "PRIMARY KEY (id)," in sql
    assert "KEY idx_k (k) USING HASH" in sql
    assert "{{" not in sql
