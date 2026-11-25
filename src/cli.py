"""Command-line entrypoint: `python -m src.cli` (also installed as `audit`).

Subcommands (D.4.2 section 6.1): validate, run, matrix, report, clean.
Exit codes: 0 success, 2 invalid config, 3 provisioning failure, 4 run/report failure.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src import config_manager, orchestrator, reporting
from src.config_manager import ConfigValidationError, ProvisionError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cli")

EXIT_OK = 0
EXIT_INVALID_CONFIG = 2
EXIT_PROVISION_FAILURE = 3
EXIT_RUN_FAILURE = 4


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        config_manager.load_study(args.config)
    except ConfigValidationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID_CONFIG
    print(f"{args.config}: valid")
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        study = config_manager.load_study(args.config)
    except ConfigValidationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID_CONFIG

    if args.cell not in study.cells:
        print(f"unknown cell {args.cell!r}; valid cells: {sorted(study.cells)}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    from src import workload_driver

    seed = workload_driver.derive_seed(study.study.seed, args.cell, args.repeat)
    run_id = f"{args.cell}_r{args.repeat}"
    task = orchestrator.RunTask(cell_id=args.cell, repeat=args.repeat, run_id=run_id, seed=seed)

    try:
        outcome = orchestrator.run_single(task, study, Path(args.results))
    except ProvisionError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PROVISION_FAILURE

    if outcome.status != "success":
        print(f"run failed: {outcome.error}", file=sys.stderr)
        return EXIT_RUN_FAILURE

    print(f"{run_id}: success")
    return EXIT_OK


def _cmd_matrix(args: argparse.Namespace) -> int:
    try:
        study = config_manager.load_study(args.config)
    except ConfigValidationError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID_CONFIG

    try:
        return orchestrator.run_matrix(study, Path(args.results), resume=not args.no_resume)
    except ProvisionError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PROVISION_FAILURE


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        reporting.generate_report(Path(args.results))
    except Exception as exc:  # noqa: BLE001
        print(f"report generation failed: {exc}", file=sys.stderr)
        return EXIT_RUN_FAILURE
    print(f"report written to {Path(args.results) / 'report.html'}")
    return EXIT_OK


def _cmd_clean(args: argparse.Namespace) -> int:
    try:
        import docker

        client = docker.from_env()
        containers = client.containers.list(all=True, filters={"name": "audit-"})
        for container in containers:
            logger.info("removing leftover container %s", container.name)
            container.remove(force=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("clean: %s", exc)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audit", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a study config file")
    p_validate.add_argument("--config", required=True)
    p_validate.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser("run", help="run a single cell/repeat")
    p_run.add_argument("--config", required=True)
    p_run.add_argument("--cell", required=True, choices=list("ABCDEF"))
    p_run.add_argument("--repeat", required=True, type=int)
    p_run.add_argument("--results", default="results")
    p_run.set_defaults(func=_cmd_run)

    p_matrix = sub.add_parser("matrix", help="run the full cells x repeats experiment matrix")
    p_matrix.add_argument("--config", required=True)
    p_matrix.add_argument("--results", default="results")
    p_matrix.add_argument("--no-resume", action="store_true", help="re-run even already-completed runs")
    p_matrix.set_defaults(func=_cmd_matrix)

    p_report = sub.add_parser("report", help="aggregate raw results and generate the report")
    p_report.add_argument("--results", required=True)
    p_report.set_defaults(func=_cmd_report)

    p_clean = sub.add_parser("clean", help="remove any leftover study containers")
    p_clean.set_defaults(func=_cmd_clean)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
