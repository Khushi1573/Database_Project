import pytest

from fidelity.auditor import AuditError, IndexSpec, audit, compare
from fidelity.crossval import cross_validate
from conftest import show_rows, stats_rows


def test_fs_matches_ground_truth(ground_truth):
    g = ground_truth
    r = compare(g["cell"], g["engine"], g["requested"], g["stats"], g["show"])
    assert r.built.index_type == g["expected_built"]
    assert r.fs == g["expected_fs"]
    assert r.sources_agree


def test_hash_on_innodb_is_explained_in_notes():
    req = IndexSpec("idx_k", "HASH", ("k",), False)
    r = compare("C", "InnoDB", req, stats_rows("idx_k", "BTREE", ["k"], False),
                show_rows("idx_k", "BTREE", ["k"], False))
    assert r.fs == 0 and not r.type_match
    assert "built BTREE for a HASH request" in r.to_row()["notes"]


def test_column_order_and_uniqueness_count():
    req = IndexSpec("idx_ab", "BTREE", ("a", "b"), True)
    r = compare("A", "InnoDB", req, stats_rows("idx_ab", "BTREE", ["b", "a"], True),
                show_rows("idx_ab", "BTREE", ["b", "a"], True))
    assert r.fs == 0 and not r.columns_match
    r = compare("A", "InnoDB", req, stats_rows("idx_ab", "BTREE", ["a", "b"], False),
                show_rows("idx_ab", "BTREE", ["a", "b"], False))
    assert r.fs == 0 and not r.unique_match


def test_case_and_row_order_are_normalized():
    req = IndexSpec("idx_ab", "btree", ("A", "B"), False)
    rows = stats_rows("idx_ab", "BTREE", ["a", "b"], False)[::-1]
    r = compare("A", "InnoDB", req, rows, show_rows("idx_ab", "BTREE", ["a", "b"], False))
    assert r.fs == 1


def test_sources_disagree_raises_or_notes():
    req = IndexSpec("idx_k", "HASH", ("k",), False)
    s = stats_rows("idx_k", "HASH", ["k"], False)
    sh = show_rows("idx_k", "BTREE", ["k"], False)
    with pytest.raises(AuditError):
        compare("E", "MEMORY", req, s, sh)
    r = compare("E", "MEMORY", req, s, sh, strict_sources=False)
    assert not r.sources_agree and "disagree" in r.to_row()["notes"]


def test_missing_index_raises():
    with pytest.raises(AuditError):
        compare("A", "InnoDB", IndexSpec("nope", "BTREE", ("k",), False), [], [])


def test_live_audit_with_fake_cursor(fake_cursor):
    stats = stats_rows("idx_k", "BTREE", ["k"], False)
    show = show_rows("idx_k", "BTREE", ["k"], False) + \
        show_rows("PRIMARY", "BTREE", ["id"], True)
    cur = fake_cursor([
        (["ENGINE"], [["InnoDB"]]),
        (list(stats[0]), [list(r.values()) for r in stats]),
        (list(show[0]), [list(r.values()) for r in show]),
    ])
    r = audit(cur, "C", "adt", "t_c", IndexSpec("idx_k", "HASH", ("k",), False))
    assert r.engine == "INNODB" and r.fs == 0
    assert cur.executed[2][0] == "SHOW INDEX FROM `adt`.`t_c`"


def test_unsafe_identifier_rejected(fake_cursor):
    cur = fake_cursor([(["ENGINE"], [["InnoDB"]]),
                       (list(stats_rows("i", "BTREE", ["k"], 0)[0]),
                        [list(stats_rows("i", "BTREE", ["k"], 0)[0].values())])])
    with pytest.raises(ValueError):
        audit(cur, "A", "adt", "t`; DROP TABLE x", IndexSpec("i", "BTREE", ("k",), False))


def test_cross_validation_confirms_substitution():
    a = compare("A", "InnoDB", IndexSpec("idx_k", "BTREE", ("k",), False),
                stats_rows("idx_k", "BTREE", ["k"], False), show_rows("idx_k", "BTREE", ["k"], False))
    c = compare("C", "InnoDB", IndexSpec("idx_k", "HASH", ("k",), False),
                stats_rows("idx_k", "BTREE", ["k"], False), show_rows("idx_k", "BTREE", ["k"], False))
    cv = cross_validate(a, c)
    assert cv.structural_match and cv.substitution_confirmed


def test_cross_validation_detects_difference():
    a = compare("A", "InnoDB", IndexSpec("idx_k", "BTREE", ("k",), False),
                stats_rows("idx_k", "BTREE", ["k"], False), show_rows("idx_k", "BTREE", ["k"], False))
    c = compare("C", "InnoDB", IndexSpec("idx_k", "HASH", ("k",), False),
                stats_rows("idx_k", "BTREE", ["k", "j"], False),
                show_rows("idx_k", "BTREE", ["k", "j"], False))
    cv = cross_validate(a, c)
    assert not cv.structural_match and not cv.columns_equal


def test_cross_validation_rejects_memory():
    e = compare("E", "MEMORY", IndexSpec("idx_k", "HASH", ("k",), False),
                stats_rows("idx_k", "HASH", ["k"], False), show_rows("idx_k", "HASH", ["k"], False))
    with pytest.raises(ValueError):
        cross_validate(e, e)
