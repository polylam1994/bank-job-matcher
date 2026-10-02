"""Score the latest run against the sample answer key (data/timeline/truth.csv).

Only possible with sample data - a real client has no answer key, which is exactly
why close calls go to a person.

Usage:  python -m bjm.evaluate
"""
from __future__ import annotations

import json

import pandas as pd

from .common import OUTPUT, TIMELINE


def evaluate() -> dict:
    res = json.loads((OUTPUT / "results.json").read_text(encoding="utf-8"))
    truth = pd.read_csv(TIMELINE / "truth.csv", dtype=str, keep_default_na=False)
    t = {r.TransactionID: (r.Kind, set(filter(None, r.Items.split("|")))) for r in truth.itertuples()}

    def correct(cand, items):
        got = {i["id"] for i in cand["items"]}
        return got == items or (got <= items and cand["method"] == "instalments")

    report = {"auto": [0, 0], "suggested_top1": [0, 0], "suggested_top3": [0, 0],
              "unmatched_should_match": 0, "unmatched_no_match_exists": 0, "ignored_correct": [0, 0]}
    misses = []
    for line in res["lines"]:
        kind, items = t[line["id"]]
        g = line["group"]
        if g == "auto":
            ok = correct(line["candidates"][0], items)
            report["auto"][0] += ok; report["auto"][1] += 1
            if not ok:
                misses.append((line["id"], "wrong auto-match", line["candidates"][0]["key"], sorted(items)))
        elif g == "suggested":
            hits = [correct(c, items) for c in line["candidates"]]
            report["suggested_top1"][0] += bool(hits and hits[0]); report["suggested_top1"][1] += 1
            report["suggested_top3"][0] += any(hits); report["suggested_top3"][1] += 1
        elif g == "unmatched":
            report["unmatched_should_match" if items else "unmatched_no_match_exists"] += 1
        else:
            report["ignored_correct"][0] += kind == "ignore"; report["ignored_correct"][1] += 1
    report["wrong_auto_examples"] = misses[:10]
    return report


def main() -> None:
    r = evaluate()
    (OUTPUT / "accuracy.json").write_text(json.dumps({
        "note": "Sample data only: scored against the generator's answer key.",
        "auto_correct": r["auto"], "suggested_top1": r["suggested_top1"], "suggested_listed": r["suggested_top3"],
        "unmatched_should_match": r["unmatched_should_match"]}), encoding="utf-8")
    pct = lambda a: f"{a[0]}/{a[1]}" + (f" ({100 * a[0] / a[1]:.0f}%)" if a[1] else "")
    print(f"Auto-matched correct:         {pct(r['auto'])}")
    print(f"Suggested, right one first:   {pct(r['suggested_top1'])}")
    print(f"Suggested, right one listed:  {pct(r['suggested_top3'])}")
    print(f"Unmatched (a match existed):  {r['unmatched_should_match']}")
    print(f"Unmatched (nothing to match): {r['unmatched_no_match_exists']}")
    print(f"Ignored correctly:            {pct(r['ignored_correct'])}")
    for m in r["wrong_auto_examples"]:
        print("  wrong auto:", m)


if __name__ == "__main__":
    main()
