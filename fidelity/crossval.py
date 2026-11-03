"""Cell A vs Cell C cross-validation -- FR-7.

Cell A: explicit BTREE request on InnoDB, AHI off.
Cell C: HASH request on InnoDB, AHI off.

If InnoDB silently substitutes a B-tree for the HASH request, the structure
built in Cell C is the same as in Cell A. We compare built type, ordered
columns and uniqueness; a full match is reported as `structural_match = True`,
which corroborates Fs = 0 for Cell C with an independent observation.
"""

from __future__ import annotations

from dataclasses import dataclass

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
        return (self.structural_match
                and self.c_requested_type == "HASH"
                and self.c_built_type == "BTREE")

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
