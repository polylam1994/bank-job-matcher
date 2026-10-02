"""Find which open items add up to one bank amount (all arithmetic in integer cents).

Two searches, cheapest first:
  statement_windows  - contiguous runs of a supplier's lines in date order. Trade
                       accounts (Reece, Tradelink) bill by statement period, so this
                       finds a 25-line statement in O(n^2) instead of 2^25.
  find_combos        - general pruned depth-first search for anything else
                       (a Bunnings receipt, two instalments paying one invoice).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    id: str
    cents: int
    day: int  # days since epoch, for ranking by date spread
    group: str = ""  # e.g. job number, to prefer combos from fewer jobs


@dataclass
class SearchResult:
    combos: list[list[Item]] = field(default_factory=list)
    complete: bool = True  # False if the node budget ran out before the search finished


def statement_windows(items: list[Item], target: int, tol: int = 0) -> list[list[Item]]:
    """Every contiguous date-ordered run of items whose sum is within tol of target."""
    items = sorted(items, key=lambda x: (x.day, x.id))
    out = []
    for i in range(len(items)):
        total = 0
        for j in range(i, len(items)):
            total += items[j].cents
            if abs(total - target) <= tol:
                out.append(items[i:j + 1])
    return out


def find_combos(items: list[Item], target: int, tol: int = 0, max_items: int = 10,
                max_solutions: int = 50, max_nodes: int = 2_000_000) -> SearchResult:
    """All subsets (up to max_items) whose sum is within tol of target.

    Items are sorted largest first so the two prunes fire early:
      - overshoot: total already above target + tol and only non-negative items remain
      - unreachable: even taking every remaining positive item can't reach target - tol
    Negative items (supplier credit notes) are placed last so the overshoot prune stays valid.
    """
    items = sorted(items, key=lambda x: (x.cents < 0, -abs(x.cents)))
    n = len(items)
    pos_suffix = [0] * (n + 1)
    neg_suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        pos_suffix[i] = pos_suffix[i + 1] + max(items[i].cents, 0)
        neg_suffix[i] = neg_suffix[i + 1] + min(items[i].cents, 0)

    res = SearchResult()
    nodes = 0
    chosen: list[Item] = []

    def stop() -> bool:
        if nodes > max_nodes or len(res.combos) >= max_solutions:
            res.complete = False  # there may be more answers we did not look for
            return True
        return False

    def dfs(i: int, total: int) -> None:
        nonlocal nodes
        nodes += 1
        if stop():
            return
        if chosen and abs(total - target) <= tol:
            res.combos.append(list(chosen))
        if i == n or len(chosen) == max_items:
            return
        if total + pos_suffix[i] < target - tol:          # unreachable
            return
        if total + neg_suffix[i] > target + tol:          # overshoot, credits can't bring it back
            return
        chosen.append(items[i])
        dfs(i + 1, total + items[i].cents)
        chosen.pop()
        if not stop():
            dfs(i + 1, total)

    dfs(0, 0)
    return res


def rank(combos: list[list[Item]], target: int, ref_day: int) -> list[list[Item]]:
    """Closest sum, then fewest items, then tightest dates nearest the payment, then fewest jobs."""
    def key(c: list[Item]):
        days = [x.day for x in c]
        return (abs(sum(x.cents for x in c) - target), len(c), max(days) - min(days),
                abs(ref_day - max(days)), len({x.group for x in c}))
    seen, out = set(), []
    for c in sorted(combos, key=key):
        k = frozenset(x.id for x in c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out
