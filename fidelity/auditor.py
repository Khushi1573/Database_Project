"""Structural Fidelity (Fs) auditor  -- FR-4, FR-5.

After a cell is provisioned and its index is created, the auditor asks the
engine what was really built. It reads the index twice, from
information_schema.statistics and from SHOW INDEX, checks that the two sources
agree, and compares the built structure with the requested one.

Fs = 1 when the built index matches the request on all three components
(index type, ordered column list, uniqueness), otherwise Fs = 0. The
per-component result is kept so the report can say *why* Fs is 0
(e.g. InnoDB silently building a BTREE for a HASH request).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Mapping, Sequence


class AuditError(RuntimeError):
    """Raised when the index cannot be found or the two metadata sources disagree."""


@dataclass(frozen=True)
class IndexSpec:
    """What was requested, or what was built."""

    name: str
    index_type: str            # 'BTREE' or 'HASH'
    columns: tuple[str, ...]   # ordered by SEQ_IN_INDEX
    unique: bool

    def normalized(self) -> "IndexSpec":
        return IndexSpec(
            name=self.name,
            index_type=self.index_type.strip().upper(),
            columns=tuple(c.strip().lower() for c in self.columns),
            unique=bool(self.unique),
        )


@dataclass
class AuditResult:
    cell: str
    engine: str
    requested: IndexSpec
    built: IndexSpec
    type_match: bool
    columns_match: bool
    unique_match: bool
    sources_agree: bool
    notes: list[str] = field(default_factory=list)

    @property
    def fs(self) -> int:
        return int(self.type_match and self.columns_match and self.unique_match)

    def to_row(self) -> dict:
        return {
            "cell": self.cell,
            "engine": self.engine,
            "index": self.requested.name,
            "requested_type": self.requested.index_type,
            "built_type": self.built.index_type,
            "requested_columns": ",".join(self.requested.columns),
            "built_columns": ",".join(self.built.columns),
            "requested_unique": self.requested.unique,
            "built_unique": self.built.unique,
            "type_match": self.type_match,
            "columns_match": self.columns_match,
            "unique_match": self.unique_match,
            "sources_agree": self.sources_agree,
            "Fs": self.fs,
            "notes": "; ".join(self.notes),
        }


# --------------------------------------------------------------------------
# SQL used against a live server (parameterised; table names are validated)
# --------------------------------------------------------------------------

STATISTICS_SQL = (
    "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE, INDEX_TYPE "
    "FROM information_schema.statistics "
    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s "
    "ORDER BY SEQ_IN_INDEX"
)

ENGINE_SQL = (
    "SELECT ENGINE FROM information_schema.tables "
    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s"
)


def _safe_identifier(name: str) -> str:
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _rows_as_dicts(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) if not isinstance(r, Mapping) else dict(r)
            for r in cursor.fetchall()]


def fetch_statistics_rows(cursor, schema: str, table: str, index: str) -> list[dict]:
    cursor.execute(STATISTICS_SQL, (schema, table, index))
    return _rows_as_dicts(cursor)


def fetch_show_index_rows(cursor, schema: str, table: str, index: str) -> list[dict]:
    cursor.execute(
        f"SHOW INDEX FROM `{_safe_identifier(schema)}`.`{_safe_identifier(table)}`"
    )
    rows = _rows_as_dicts(cursor)
    return [r for r in rows if r.get("Key_name") == index]


def fetch_engine(cursor, schema: str, table: str) -> str:
    cursor.execute(ENGINE_SQL, (schema, table))
    row = cursor.fetchone()
    if not row:
        raise AuditError(f"table {schema}.{table} not found")
    return (row["ENGINE"] if isinstance(row, Mapping) else row[0]).upper()


# --------------------------------------------------------------------------
# Pure functions (unit-tested against the ground-truth fixture)
# --------------------------------------------------------------------------

def spec_from_statistics(index: str, rows: Sequence[Mapping]) -> IndexSpec:
    if not rows:
        raise AuditError(f"index {index!r} not found in information_schema.statistics")
    rows = sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
    types = {str(r["INDEX_TYPE"]).upper() for r in rows}
    if len(types) != 1:
        raise AuditError(f"inconsistent INDEX_TYPE across columns: {types}")
    return IndexSpec(
        name=index,
        index_type=types.pop(),
        columns=tuple(str(r["COLUMN_NAME"]) for r in rows),
        unique=int(rows[0]["NON_UNIQUE"]) == 0,
    ).normalized()


def spec_from_show_index(index: str, rows: Sequence[Mapping]) -> IndexSpec:
    if not rows:
        raise AuditError(f"index {index!r} not found in SHOW INDEX")
    rows = sorted(rows, key=lambda r: int(r["Seq_in_index"]))
    types = {str(r["Index_type"]).upper() for r in rows}
    if len(types) != 1:
        raise AuditError(f"inconsistent Index_type across columns: {types}")
    return IndexSpec(
        name=index,
        index_type=types.pop(),
        columns=tuple(str(r["Column_name"]) for r in rows),
        unique=int(rows[0]["Non_unique"]) == 0,
    ).normalized()


def compare(cell: str, engine: str, requested: IndexSpec,
            stats_rows: Sequence[Mapping], show_rows: Sequence[Mapping],
            strict_sources: bool = True) -> AuditResult:
    """Compare the request with what both metadata sources report."""
    req = requested.normalized()
    from_stats = spec_from_statistics(req.name, stats_rows)
    from_show = spec_from_show_index(req.name, show_rows)

    sources_agree = (
        from_stats.index_type == from_show.index_type
        and from_stats.columns == from_show.columns
        and from_stats.unique == from_show.unique
    )
    notes: list[str] = []
    if not sources_agree:
        msg = (f"metadata sources disagree: statistics={asdict(from_stats)} "
               f"show_index={asdict(from_show)}")
        if strict_sources:
            raise AuditError(msg)
        notes.append(msg)

    built = from_stats
    type_match = built.index_type == req.index_type
    if not type_match:
        notes.append(f"{engine} built {built.index_type} for a {req.index_type} request")
    columns_match = built.columns == req.columns
    if not columns_match:
        notes.append(f"column list differs: built {built.columns}, requested {req.columns}")
    unique_match = built.unique == req.unique
    if not unique_match:
        notes.append("uniqueness differs")

    return AuditResult(cell, engine.upper(), req, built,
                       type_match, columns_match, unique_match, sources_agree, notes)


def audit(cursor, cell: str, schema: str, table: str,
          requested: IndexSpec, strict_sources: bool = True) -> AuditResult:
    """Live entry point: query the server and return the audit for one cell."""
    engine = fetch_engine(cursor, schema, table)
    stats = fetch_statistics_rows(cursor, schema, table, requested.name)
    show = fetch_show_index_rows(cursor, schema, table, requested.name)
    return compare(cell, engine, requested, stats, show, strict_sources)


def audit_many(results: Iterable[AuditResult]) -> list[dict]:
    return [r.to_row() for r in results]
