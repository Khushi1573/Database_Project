import pytest

from fidelity.ahi import CounterError, CounterSnapshot, compute_uh, read_counters


def test_fixed_values():
    assert compute_uh("MEMORY", False, "HASH") == 1.0
    assert compute_uh("MEMORY", False, "BTREE") == 0.0
    assert compute_uh("InnoDB", False, "BTREE") == 0.0


def test_uh_from_counter_deltas():
    before, after = CounterSnapshot(100, 400), CounterSnapshot(400, 500)
    assert compute_uh("InnoDB", True, "BTREE", before, after) == pytest.approx(0.75)


def test_hash_request_can_have_fs0_but_uh_positive():
    # Cell D: HASH requested, BTREE built (Fs = 0), AHI still answers lookups
    uh = compute_uh("InnoDB", True, "BTREE", CounterSnapshot(0, 0), CounterSnapshot(30, 70))
    assert 0 < uh < 1


def test_no_lookups_returns_none():
    s = CounterSnapshot(5, 5)
    assert compute_uh("InnoDB", True, "BTREE", s, s) is None


def test_counter_reset_raises():
    with pytest.raises(CounterError):
        compute_uh("InnoDB", True, "BTREE", CounterSnapshot(10, 10), CounterSnapshot(5, 20))


def test_missing_snapshots_raise():
    with pytest.raises(ValueError):
        compute_uh("InnoDB", True, "BTREE")


def test_read_counters(fake_cursor):
    cur = fake_cursor([(["NAME", "COUNT", "STATUS"],
                        [["adaptive_hash_searches", 12, "enabled"],
                         ["adaptive_hash_searches_btree", 34, "enabled"]])])
    assert read_counters(cur) == CounterSnapshot(12, 34)


def test_read_counters_disabled(fake_cursor):
    cur = fake_cursor([(["NAME", "COUNT", "STATUS"],
                        [["adaptive_hash_searches", 0, "disabled"],
                         ["adaptive_hash_searches_btree", 0, "disabled"]])])
    with pytest.raises(CounterError, match="innodb_monitor_enable"):
        read_counters(cur)
