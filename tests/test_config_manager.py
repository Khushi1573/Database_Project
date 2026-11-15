"""AC-1: study+cell validation -- 6 valid cells (A-F) and 4+ invalid configs."""
from __future__ import annotations

import pytest
import yaml

from src.config_manager import CellConfig, ConfigValidationError, load_study, validate_cell

VALID_CELLS = [
    CellConfig(cell_id="A", engine="InnoDB", index="BTREE", ahi="off"),
    CellConfig(cell_id="B", engine="InnoDB", index="BTREE", ahi="on"),
    CellConfig(cell_id="C", engine="InnoDB", index="HASH", ahi="off"),
    CellConfig(cell_id="D", engine="InnoDB", index="HASH", ahi="on"),
    CellConfig(cell_id="E", engine="MEMORY", index="BTREE", ahi="n/a"),
    CellConfig(cell_id="F", engine="MEMORY", index="HASH", ahi="n/a"),
]


@pytest.mark.parametrize("cell", VALID_CELLS, ids=[c.cell_id for c in VALID_CELLS])
def test_valid_cells_pass(cell):
    assert validate_cell(cell) == []


@pytest.mark.parametrize(
    "cell",
    [
        CellConfig(cell_id="X1", engine="PostgreSQL", index="BTREE", ahi="off"),
        CellConfig(cell_id="X2", engine="InnoDB", index="BITMAP", ahi="off"),
        CellConfig(cell_id="X3", engine="MEMORY", index="BTREE", ahi="on"),
        CellConfig(cell_id="X4", engine="InnoDB", index="BTREE", ahi="n/a"),
    ],
    ids=["bad-engine", "bad-index", "memory-with-ahi", "innodb-without-ahi"],
)
def test_invalid_cells_fail(cell):
    errors = validate_cell(cell)
    assert errors, f"expected validation errors for {cell}"


def _write_config(tmp_path, overrides: dict) -> str:
    base = {
        "study": {
            "name": "t",
            "mysql_image": "mysql:8.4",
            "seed": 1,
            "rows": 1_000_000,
            "repeats": 5,
            "warmup_seconds": 1,
            "phases": [
                {"read_pct": 95, "seconds": 1},
                {"read_pct": 10, "seconds": 1},
                {"read_pct": 95, "seconds": 1},
            ],
            "threads": 1,
            "cpu_pinning": {"driver": "0-1", "mysql": "2-3"},
        },
        "adaptation": {"median_window": 5, "window": 10, "epsilon": 0.02},
        "cells": {"A": {"engine": "InnoDB", "index": "BTREE", "ahi": "off"}},
    }

    def deep_merge(a, b):
        out = dict(a)
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v
        return out

    merged = deep_merge(base, overrides)
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(merged), encoding="utf-8")
    return str(path)


def test_load_study_valid(tmp_path):
    path = _write_config(tmp_path, {})
    config = load_study(path)
    assert config.cells["A"].engine == "InnoDB"
    assert config.study.read_pct_phases == [95, 10, 95]


@pytest.mark.parametrize(
    "overrides",
    [
        {"study": {"rows": 42}},
        {"study": {"repeats": 1}},
        {"cells": {"A": {"engine": "Oracle", "index": "BTREE", "ahi": "off"}}},
        {"study": {"cpu_pinning": {"driver": "not-a-cpuset", "mysql": "2-3"}}},
    ],
    ids=["bad-rows", "too-few-repeats", "bad-engine", "bad-cpuset"],
)
def test_load_study_invalid(tmp_path, overrides):
    path = _write_config(tmp_path, overrides)
    with pytest.raises(ConfigValidationError):
        load_study(path)


def test_load_study_missing_file(tmp_path):
    with pytest.raises(ConfigValidationError):
        load_study(tmp_path / "does_not_exist.yaml")
