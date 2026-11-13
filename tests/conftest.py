"""Ground-truth fixture for AC-3.

Each case is a cell from the six-cell matrix with the index that was
requested and the metadata rows the server is known to return for it
(information_schema.statistics and SHOW INDEX), together with the expected
built type and Fs. Replace the rows with captures from a real pilot server
once available so the fixture stays tied to observed behaviour.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fidelity.auditor import IndexSpec  # noqa: E402


def stats_rows(index, itype, cols, unique):
    return [{"INDEX_NAME": index, "SEQ_IN_INDEX": i + 1, "COLUMN_NAME": c,
             "NON_UNIQUE": 0 if unique else 1, "INDEX_TYPE": itype}
            for i, c in enumerate(cols)]


def show_rows(index, itype, cols, unique):
    return [{"Key_name": index, "Seq_in_index": i + 1, "Column_name": c,
             "Non_unique": 0 if unique else 1, "Index_type": itype}
            for i, c in enumerate(cols)]


# cell, engine, ahi, requested type, built type (ground truth), expected Fs
MATRIX = [
    ("A", "InnoDB", False, "BTREE", "BTREE", 1),
    ("B", "InnoDB", True,  "BTREE", "BTREE", 1),
    ("C", "InnoDB", False, "HASH",  "BTREE", 0),
    ("D", "InnoDB", True,  "HASH",  "BTREE", 0),
    ("E", "MEMORY", False, "HASH",  "HASH",  1),
    ("F", "MEMORY", False, "BTREE", "BTREE", 1),
]

COLS = ("k",)
INDEX = "idx_k"


@pytest.fixture(params=MATRIX, ids=[m[0] for m in MATRIX])
def ground_truth(request):
    cell, engine, ahi, req_type, built_type, fs = request.param
    return {
        "cell": cell, "engine": engine, "ahi": ahi, "expected_fs": fs,
        "expected_built": built_type,
        "requested": IndexSpec(INDEX, req_type, COLS, False),
        "stats": stats_rows(INDEX, built_type, COLS, False),
        "show": show_rows(INDEX, built_type, COLS, False),
    }


@pytest.fixture
def matrix_rows():
    return {"stats": stats_rows, "show": show_rows}


class FakeCursor:
    """Minimal DB-API cursor returning canned results in call order."""

    def __init__(self, results):
        self._results = list(results)
        self.description = None
        self._rows = []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        cols, rows = self._results.pop(0)
        self.description = [(c,) for c in cols]
        self._rows = [tuple(r) for r in rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def fake_cursor():
    return FakeCursor
