"""Orchestrator schedule/retry/resume behavior with mocked run_fn (FR-10, AC-6)."""
from __future__ import annotations

import json

import yaml

from src import orchestrator
from src.config_manager import load_study
from src.orchestrator import RunOutcome, RunTask, build_schedule, run_matrix


def _study(tmp_path, repeats=5):
    raw = {
        "study": {
            "name": "t",
            "mysql_image": "mysql:8.4",
            "seed": 7,
            "rows": 1_000_000,
            "repeats": repeats,
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
        "cells": {
            "A": {"engine": "InnoDB", "index": "BTREE", "ahi": "off"},
            "E": {"engine": "MEMORY", "index": "BTREE", "ahi": "n/a"},
        },
    }
    path = tmp_path / "study.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_study(path)


def test_build_schedule_covers_all_cells_and_repeats(tmp_path):
    study = _study(tmp_path, repeats=5)
    schedule = build_schedule(study)
    assert len(schedule) == 2 * 5
    run_ids = {t.run_id for t in schedule}
    for cell in ("A", "E"):
        for r in range(1, 6):
            assert f"{cell}_r{r}" in run_ids


def test_build_schedule_is_deterministic_given_seed(tmp_path):
    study = _study(tmp_path)
    s1 = [t.run_id for t in build_schedule(study)]
    s2 = [t.run_id for t in build_schedule(study)]
    assert s1 == s2


def test_run_matrix_retries_once_then_succeeds(tmp_path):
    study = _study(tmp_path, repeats=5)
    results_dir = tmp_path / "results"
    attempts: dict[str, int] = {}

    def flaky_run_fn(task: RunTask, study_, results_dir_, retries=0):
        attempts[task.run_id] = attempts.get(task.run_id, 0) + 1
        status = "success" if attempts[task.run_id] >= 2 else "failed"
        return RunOutcome(task=task, status=status, retries=retries)

    code = run_matrix(study, results_dir, resume=False, run_fn=flaky_run_fn)
    assert code == orchestrator.EXIT_OK
    assert all(v == 2 for v in attempts.values())


def test_run_matrix_excludes_after_two_failures_and_returns_exit_4(tmp_path):
    study = _study(tmp_path, repeats=5)
    results_dir = tmp_path / "results"

    def always_fail(task: RunTask, study_, results_dir_, retries=0):
        return RunOutcome(task=task, status="failed", retries=retries, error="boom")

    code = run_matrix(study, results_dir, resume=False, run_fn=always_fail)
    assert code == orchestrator.EXIT_RUN_FAILURE

    for cell in ("A", "E"):
        meta_path = results_dir / "raw" / f"{cell}_r1_meta.json"
        assert meta_path.exists()
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        assert meta["status"] == "failed"


def test_run_matrix_skips_already_completed_runs(tmp_path):
    study = _study(tmp_path, repeats=5)
    raw_dir = tmp_path / "results" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "A_r1_meta.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")

    called = []

    def recording_run_fn(task: RunTask, study_, results_dir_, retries=0):
        called.append(task.run_id)
        return RunOutcome(task=task, status="success", retries=retries)

    code = run_matrix(study, raw_dir.parent, resume=True, run_fn=recording_run_fn)
    assert code == orchestrator.EXIT_OK
    assert "A_r1" not in called
    assert "A_r2" in called
    assert "E_r1" in called


def test_run_matrix_invalid_config_returns_exit_2(tmp_path):
    study = _study(tmp_path)
    study.study.repeats = 0
    code = run_matrix(
        study,
        tmp_path / "results",
        run_fn=lambda *a, **k: RunOutcome(task=RunTask("A", 1, "A_r1", 0), status="success", retries=0),
    )
    assert code == orchestrator.EXIT_INVALID_CONFIG
