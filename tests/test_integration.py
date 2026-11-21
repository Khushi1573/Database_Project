"""Live integration tests requiring Docker and a reachable MySQL.
Deselected by default (see pyproject.toml addopts); run with `pytest -m integration`.
"""
from __future__ import annotations

import os

import pytest

from src.config_manager import DBCredentials, destroy, load_study, provision


@pytest.mark.integration
def test_provision_and_destroy_real_container():
    if not os.environ.get("MYSQL_ROOT_PASSWORD"):
        pytest.skip("requires .env credentials and a running Docker daemon")

    study = load_study("configs/study.yaml")
    cell = study.cells["A"]
    creds = DBCredentials.from_env()

    instance = provision(cell, study, run_id="it_test", creds=creds)
    try:
        assert instance.container_id
    finally:
        destroy(instance)
