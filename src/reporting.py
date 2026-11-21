"""Analysis & reporting pipeline: reads raw per-run results, aggregates
them phase-aware with bootstrap CIs, performs FR-7 structural cross-validation
(Cell A vs Cell C) plus behavioral Mann-Whitney U comparison, plots Wireframe S1
figures, and writes static results/report.html.

Implements FR-7, FR-14, FR-15, FR-16, FR-17, AC-4, AC-9, AC-10.
"""
from __future__ import annotations

import csv
import dataclasses
import json
import logging
from pathlib import Path
from typing import Optional

from fidelity.adaptation import PhaseAdaptation
from fidelity.aggregate import (
    MetricRecord,
    PhaseSummary,
    summarize_by_phase,
    write_phase_summary_csv,
)
from fidelity.auditor import AuditResult, IndexSpec
from fidelity.crossval import (
    BehavioralCrossValidation,
    CrossValidation,
    cross_validate,
    cross_validate_behavioral,
)
from fidelity.plots import plot_s1_all

logger = logging.getLogger(__name__)

TELEMETRY_METRICS = ["qps_read", "tps_write", "lat_avg_ms", "lat_p95_ms", "rows_modified"]


@dataclasses.dataclass
class RunData:
    run_id: str
    cell_id: str
    repeat: int
    meta: dict
    telemetry_rows: list[dict]


def load_valid_runs(results_dir: Path) -> list[RunData]:
    """Read results/raw/*_meta.json + *_telemetry.csv, excluding failed runs."""
    raw_dir = results_dir / "raw"
    runs: list[RunData] = []
    if not raw_dir.exists():
        return runs

    for meta_path in sorted(raw_dir.glob("*_meta.json")):
        run_id = meta_path.stem[: -len("_meta")]
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("excluding %s: unreadable meta (%s)", run_id, exc)
            continue

        if meta.get("status") != "success":
            logger.info("excluding %s: status=%s", run_id, meta.get("status"))
            continue

        telemetry_path = raw_dir / f"{run_id}_telemetry.csv"
        if not telemetry_path.exists():
            logger.warning("excluding %s: missing telemetry file", run_id)
            continue
        with open(telemetry_path, "r", newline="", encoding="utf-8") as fh:
            telemetry_rows = list(csv.DictReader(fh))

        runs.append(
            RunData(
                run_id=run_id,
                cell_id=str(meta.get("cell", run_id.split("_")[0])),
                repeat=int(meta.get("repeat", 0)),
                meta=meta,
                telemetry_rows=telemetry_rows,
            )
        )

    return runs


def _phase_average(rows: list[dict], phase_no: int, metric: str) -> float:
    values = [float(r[metric]) for r in rows if int(r["phase_no"]) == phase_no]
    return sum(values) / len(values) if values else 0.0


def build_metric_records(runs: list[RunData]) -> list[MetricRecord]:
    """Build phase-aware MetricRecord items plus whole-run Fs/Uh (phase=0)."""
    records: list[MetricRecord] = []
    for run in runs:
        src = f"raw/{run.run_id}_telemetry.csv"
        for phase_no in (1, 2, 3):
            for metric in TELEMETRY_METRICS:
                value = _phase_average(run.telemetry_rows, phase_no, metric)
                records.append(
                    MetricRecord(
                        cell_id=run.cell_id,
                        metric=metric,
                        phase=phase_no,
                        value=value,
                        run_id=run.run_id,
                        source_file=src,
                    )
                )

        meta_src = f"raw/{run.run_id}_meta.json"
        if run.meta.get("Fs") is not None:
            records.append(
                MetricRecord(
                    cell_id=run.cell_id,
                    metric="Fs",
                    phase=0,
                    value=float(run.meta["Fs"]),
                    run_id=run.run_id,
                    source_file=meta_src,
                )
            )
        if run.meta.get("Uh") is not None:
            records.append(
                MetricRecord(
                    cell_id=run.cell_id,
                    metric="Uh",
                    phase=0,
                    value=float(run.meta["Uh"]),
                    run_id=run.run_id,
                    source_file=meta_src,
                )
            )

    return records


def _audit_result_from_meta(run: RunData) -> Optional[AuditResult]:
    rec = run.meta.get("audit_record") or {}
    if not rec or "built_type" not in rec:
        return None
    idx_name = str(rec.get("index", "idx_k"))
    req_cols = tuple(c for c in str(rec.get("requested_columns", "k")).split(",") if c)
    built_cols = tuple(c for c in str(rec.get("built_columns", "k")).split(",") if c)
    requested = IndexSpec(
        name=idx_name,
        index_type=str(rec.get("requested_type", "")),
        columns=req_cols,
        unique=bool(rec.get("requested_unique", False)),
    ).normalized()
    built = IndexSpec(
        name=idx_name,
        index_type=str(rec.get("built_type", "")),
        columns=built_cols,
        unique=bool(rec.get("built_unique", False)),
    ).normalized()
    return AuditResult(
        cell=run.cell_id,
        engine=str(rec.get("engine", "INNODB")).upper(),
        requested=requested,
        built=built,
        type_match=bool(rec.get("type_match", requested.index_type == built.index_type)),
        columns_match=bool(rec.get("columns_match", requested.columns == built.columns)),
        unique_match=bool(rec.get("unique_match", requested.unique == built.unique)),
        sources_agree=bool(rec.get("sources_agree", True)),
    )


def build_structural_crossvalidations(
    runs: list[RunData],
    cell_a: str = "A",
    cell_c: str = "C",
) -> list[tuple[int, CrossValidation]]:
    """FR-7 / AC-4: structural Cell A vs Cell C cross-validation per repeat."""
    a_by_repeat = {r.repeat: r for r in runs if r.cell_id == cell_a}
    c_by_repeat = {r.repeat: r for r in runs if r.cell_id == cell_c}
    out: list[tuple[int, CrossValidation]] = []
    for rep in sorted(set(a_by_repeat) & set(c_by_repeat)):
        aud_a = _audit_result_from_meta(a_by_repeat[rep])
        aud_c = _audit_result_from_meta(c_by_repeat[rep])
        if aud_a and aud_c and aud_a.engine == "INNODB" and aud_c.engine == "INNODB":
            out.append((rep, cross_validate(aud_a, aud_c)))
    return out


def build_behavioral_crossvalidations(
    runs: list[RunData],
    cell_a: str = "A",
    cell_c: str = "C",
) -> list[BehavioralCrossValidation]:
    """Secondary Mann-Whitney U comparison of Cell A vs Cell C per metric/phase."""
    results = []
    for phase_no in (1, 2, 3):
        for metric in TELEMETRY_METRICS:
            values_a = [_phase_average(r.telemetry_rows, phase_no, metric) for r in runs if r.cell_id == cell_a]
            values_c = [_phase_average(r.telemetry_rows, phase_no, metric) for r in runs if r.cell_id == cell_c]
            results.append(
                cross_validate_behavioral(
                    values_a, values_c, metric=metric, phase=phase_no, cell_a_id=cell_a, cell_c_id=cell_c
                )
            )
    return results


def collect_adaptations(
    runs: list[RunData],
    ahi_on_cells: tuple[str, ...] = ("B", "D"),
) -> list[PhaseAdaptation]:
    """FR-14 / AC-9: collect AHI adaptation-time records for AHI-on cells B and D."""
    adaptations: list[PhaseAdaptation] = []
    for run in runs:
        if run.cell_id not in ahi_on_cells:
            continue
        for entry in run.meta.get("adaptation_times", []):
            adaptations.append(
                PhaseAdaptation(
                    cell_id=str(entry.get("cell_id", run.cell_id)),
                    transition_no=int(entry.get("transition_no", 0)),
                    transition_t=float(entry.get("transition_t", 0.0)),
                    stabilized=bool(entry.get("stabilized", False)),
                    adaptation_time_s=entry.get("adaptation_time_s"),
                    steady_value=entry.get("steady_value"),
                    median_window=int(entry.get("median_window", 5)),
                    stabilization_window=int(entry.get("stabilization_window", 10)),
                    epsilon=float(entry.get("epsilon", 0.02)),
                    method=str(entry.get("method", "moving_median+range_band")),
                    detail=str(entry.get("detail", "")),
                )
            )
    return adaptations


REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Index-Request Fidelity Study Report</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2rem; color: #1a1a1a; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: right; font-variant-numeric: tabular-nums; }}
  th {{ background: #f2f2f2; text-align: center; }}
  td:first-child, th:first-child {{ text-align: left; }}
  figure {{ margin: 0 0 2rem 0; }}
  figcaption {{ font-size: 0.9rem; color: #444; }}
  img {{ max-width: 100%; border: 1px solid #ddd; }}
</style>
</head>
<body>
<h1>Measuring Index-Request Fidelity (COMP.8157 Team 2)</h1>
<p>Hash-Index Substitution and the Adaptive Hash Index in MySQL InnoDB versus MEMORY under Shifting Read/Write Workloads</p>

<h2>Table 1. Primary Metrics Summary (Median [Bootstrap 95% CI])</h2>
<table>
<tr><th>Cell</th><th>Metric</th><th>Phase</th><th>Median</th><th>95% CI low</th><th>95% CI high</th><th>n</th></tr>
{summary_rows}
</table>

<h2>Structural Cross-Validation: Cell A vs Cell C (FR-7 / AC-4)</h2>
<table>
<tr><th>Repeat</th><th>Cell A built</th><th>Cell C requested</th><th>Cell C built</th><th>Columns equal</th><th>Unique equal</th><th>Result</th><th>Substitution confirmed?</th></tr>
{structural_crossval_rows}
</table>

<h2>Behavioral Cross-Validation: Cell A vs Cell C (Mann-Whitney U)</h2>
<table>
<tr><th>Metric</th><th>Phase</th><th>n_A</th><th>n_C</th><th>U statistic</th><th>p-value</th><th>Consistent?</th></tr>
{behavioral_crossval_rows}
</table>

<h2>Figures (Wireframe S1)</h2>
<figure><img src="figures/fs_uh_by_cell.png" alt="Fs and Uh by cell"><figcaption>Fig 1. Structural Fidelity (Fs) and Effective Hash Coverage (Uh) by cell.</figcaption></figure>
<figure><img src="figures/lookup_latency_by_phase.png" alt="Lookup latency by phase"><figcaption>Fig 2. p95 lookup latency by phase and cell.</figcaption></figure>
<figure><img src="figures/write_throughput_by_phase.png" alt="Write throughput by phase"><figcaption>Fig 3. Write throughput (tps) by phase and cell.</figcaption></figure>
<figure><img src="figures/ahi_adaptation_time.png" alt="AHI adaptation time"><figcaption>Fig 4. AHI adaptation time per transition, cells B and D.</figcaption></figure>

<h2>Run Metadata</h2>
<table>
<tr><th>Run</th><th>Cell</th><th>Repeat</th><th>Rows</th><th>Fallback</th><th>MySQL image</th><th>Git commit</th></tr>
{meta_rows}
</table>
</body>
</html>
"""


def _render_summary_rows(summaries: list[PhaseSummary]) -> str:
    return "\n".join(
        f"<tr><td>{s.cell_id}</td><td>{s.metric}</td><td>{s.phase}</td>"
        f"<td>{s.median:.4f}</td><td>{s.ci95_low:.4f}</td><td>{s.ci95_high:.4f}</td><td>{s.n_repeats}</td></tr>"
        for s in summaries
    )


def _render_structural_rows(items: list[tuple[int, CrossValidation]]) -> str:
    if not items:
        return "<tr><td colspan='8'>No structural audit records available for Cell A vs Cell C.</td></tr>"
    return "\n".join(
        f"<tr><td>{rep}</td><td>{cv.a_built_type}</td><td>{cv.c_requested_type}</td>"
        f"<td>{cv.c_built_type}</td><td>{'yes' if cv.columns_equal else 'no'}</td>"
        f"<td>{'yes' if cv.unique_equal else 'no'}</td><td>{cv.status_label()}</td>"
        f"<td>{'yes' if cv.substitution_confirmed else 'no'}</td></tr>"
        for rep, cv in items
    )


def _render_behavioral_rows(crossvals: list[BehavioralCrossValidation]) -> str:
    return "\n".join(
        f"<tr><td>{c.metric}</td><td>{c.phase}</td><td>{c.n_a}</td><td>{c.n_c}</td>"
        f"<td>{c.statistic:.3f}</td><td>{c.p_value:.4f}</td><td>{'yes' if c.consistent else 'no'}</td></tr>"
        for c in crossvals
    )


def _render_meta_rows(runs: list[RunData]) -> str:
    return "\n".join(
        f"<tr><td>{r.run_id}</td><td>{r.cell_id}</td><td>{r.repeat}</td>"
        f"<td>{r.meta.get('rows')}</td><td>{r.meta.get('dataset_fallback')}</td>"
        f"<td>{r.meta.get('mysql_version')}</td><td>{r.meta.get('git_commit_hash')}</td></tr>"
        for r in runs
    )


def generate_report(results_dir: Path) -> Path:
    """Load valid runs, aggregate, cross-validate, plot, and write report.html."""
    runs = load_valid_runs(results_dir)
    if not runs:
        raise RuntimeError(f"no valid runs found under {results_dir / 'raw'}")

    records = build_metric_records(runs)
    summaries = summarize_by_phase(records)
    write_phase_summary_csv(summaries, results_dir / "aggregated" / "summary.csv")

    structural_cv = build_structural_crossvalidations(runs)
    behavioral_cv = build_behavioral_crossvalidations(runs)
    adaptations = collect_adaptations(runs)

    plot_s1_all(summaries, adaptations, results_dir / "figures")

    html = REPORT_TEMPLATE.format(
        summary_rows=_render_summary_rows(summaries),
        structural_crossval_rows=_render_structural_rows(structural_cv),
        behavioral_crossval_rows=_render_behavioral_rows(behavioral_cv),
        meta_rows=_render_meta_rows(runs),
    )
    report_path = results_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
