"""Cell A vs Cell C cross-validation -- FR-7, AC-4.

Primary FR-7 check (structural):
    Cell A: explicit BTREE request on InnoDB, AHI off.
    Cell C: HASH request on InnoDB, AHI off.
    If InnoDB silently substitutes a B-tree for the HASH request, the structure
    built in Cell C is the same as in Cell A. We compare built type, ordered
    columns, and uniqueness; a full match is reported as
    `structural_match = True`, corroborating Fs = 0 for Cell C.

Secondary helper (behavioral):
    Two-sided Mann-Whitney U test comparing Cell A vs Cell C performance
    distributions per metric and phase.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Sequence

from scipy import stats

from .auditor import AuditResult


@dataclass(frozen=True)
class CrossValidation:
    type_equal: bool
    columns_equal: bool
    unique_equal: bool
    a_built_type: str
    c_built_type: str
    c_requested_type: str

    @property
    def structural_match(self) -> bool:
        return self.type_equal and self.columns_equal and self.unique_equal

    @property
    def substitution_confirmed(self) -> bool:
        """True when C asked for HASH but got the same B-tree as A."""
        return (
            self.structural_match
            and self.c_requested_type == "HASH"
            and self.c_built_type == "BTREE"
        )

    def status_label(self) -> str:
        return "match" if self.structural_match else "mismatch"

    def to_row(self) -> dict:
        return {
            "type_equal": self.type_equal,
            "columns_equal": self.columns_equal,
            "unique_equal": self.unique_equal,
            "A_built": self.a_built_type,
            "C_requested": self.c_requested_type,
            "C_built": self.c_built_type,
            "structural_match": self.structural_match,
            "substitution_confirmed": self.substitution_confirmed,
            "status": self.status_label(),
        }


def cross_validate(cell_a: AuditResult, cell_c: AuditResult) -> CrossValidation:
    if cell_a.engine != "INNODB" or cell_c.engine != "INNODB":
        raise ValueError("cross-validation is defined for InnoDB cells A and C only")
    a, c = cell_a.built, cell_c.built
    return CrossValidation(
        type_equal=a.index_type == c.index_type,
        columns_equal=a.columns == c.columns,
        unique_equal=a.unique == c.unique,
        a_built_type=a.index_type,
        c_built_type=c.index_type,
        c_requested_type=cell_c.requested.index_type,
    )


@dataclasses.dataclass
class BehavioralCrossValidation:
    """Two-sided Mann-Whitney U comparison of Cell A vs Cell C samples."""

    cell_a_id: str
    cell_c_id: str
    metric: str
    phase: int
    n_a: int
    n_c: int
    statistic: float
    p_value: float
    consistent: bool
    alpha: float = 0.05


def cross_validate_behavioral(
    values_a: Sequence[float],
    values_c: Sequence[float],
    metric: str,
    phase: int,
    cell_a_id: str = "A",
    cell_c_id: str = "C",
    alpha: float = 0.05,
) -> BehavioralCrossValidation:
    """Two-sided Mann-Whitney U test comparing Cell A vs Cell C distributions."""
    if len(values_a) == 0 or len(values_c) == 0:
        return BehavioralCrossValidation(
            cell_a_id=cell_a_id,
            cell_c_id=cell_c_id,
            metric=metric,
            phase=phase,
            n_a=len(values_a),
            n_c=len(values_c),
            statistic=0.0,
            p_value=1.0,
            consistent=True,
            alpha=alpha,
        )

    if len(set(values_a)) == 1 and set(values_a) == set(values_c):
        return BehavioralCrossValidation(
            cell_a_id=cell_a_id,
            cell_c_id=cell_c_id,
            metric=metric,
            phase=phase,
            n_a=len(values_a),
            n_c=len(values_c),
            statistic=0.0,
            p_value=1.0,
            consistent=True,
            alpha=alpha,
        )

    result = stats.mannwhitneyu(values_a, values_c, alternative="two-sided")
    return BehavioralCrossValidation(
        cell_a_id=cell_a_id,
        cell_c_id=cell_c_id,
        metric=metric,
        phase=phase,
        n_a=len(values_a),
        n_c=len(values_c),
        statistic=float(result.statistic),
        p_value=float(result.pvalue),
        consistent=bool(result.pvalue >= alpha),
        alpha=alpha,
    )
