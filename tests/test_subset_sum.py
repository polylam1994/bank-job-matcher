from itertools import combinations

from bjm.subset_sum import Item, find_combos, rank, statement_windows


def items(*cents, day0=0):
    return [Item(f"M-{i}", c, day0 + i, "J-1") for i, c in enumerate(cents)]


def brute(pool, target, tol, max_items):
    out = set()
    for k in range(1, max_items + 1):
        for c in combinations(pool, k):
            if abs(sum(x.cents for x in c) - target) <= tol:
                out.add(frozenset(x.id for x in c))
    return out


def test_matches_brute_force():
    pool = items(21480, 4999, 12500, 3001, 8000, 1999, 7600, 15000, 2500, 499)
    for target in (24480, 30000, 12500, 1, 77000):
        for tol in (0, 200):
            got = {frozenset(x.id for x in c) for c in find_combos(pool, target, tol, max_items=10).combos}
            assert got == brute(pool, target, tol, 10), (target, tol)


def test_credit_notes_are_handled():
    pool = items(10000, 5000, -2500)
    got = {frozenset(x.id for x in c) for c in find_combos(pool, 12500, 0).combos}
    assert frozenset({"M-0", "M-1", "M-2"}) in got


def test_max_items_limits_combo_size():
    pool = items(100, 100, 100, 100)
    assert all(len(c) <= 2 for c in find_combos(pool, 200, 0, max_items=2).combos)
    assert find_combos(pool, 400, 0, max_items=3).combos == []


def test_node_budget_reports_incomplete():
    pool = items(*([100] * 30))
    res = find_combos(pool, 1500, 0, max_items=15, max_nodes=1000)
    assert not res.complete


def test_statement_window_is_contiguous_by_date():
    pool = items(1000, 2000, 3000, 4000)
    wins = statement_windows(pool, 5000)  # 2000+3000 is contiguous; 1000+4000 is not
    assert [[x.id for x in w] for w in wins] == [["M-1", "M-2"]]


def test_rank_prefers_exact_then_fewer_items():
    pool = items(5000, 2500, 2500, 4990)
    combos = find_combos(pool, 5000, 10).combos
    ranked = rank(combos, 5000, ref_day=3)
    assert [x.id for x in ranked[0]] == ["M-0"]
