"""Reconciliation state: what has already been matched, ignored or rejected.

state/decisions.json
{
  "matches":  {"<bank id>": {"items": ["INV-2044"], "bank_group": ["<bank id>", ...],
                             "side": "customer", "source": "history|auto|confirmed"}},
  "ignored":  {"<bank id>": "reason"},
  "rejected": {"<bank id>": ["<candidate key>", ...]}
}

`history` entries stand in for the office's past reconciliations (seeded once from the
sample answer key up to `reconciled_up_to`). They are also the labelled pairs used to
estimate the Fellegi-Sunter m probabilities.
"""
from __future__ import annotations

import json

import pandas as pd

from .common import STATE, TIMELINE, settings


def empty() -> dict:
    return {"matches": {}, "ignored": {}, "rejected": {}}


def load() -> dict:
    if not STATE.exists():
        return empty()
    data = json.loads(STATE.read_text(encoding="utf-8"))
    for k, v in empty().items():
        data.setdefault(k, v)
    return data


def save(state: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def seed_history(cutoff: str | None = None) -> dict:
    """Mark every bank line up to the cutoff as reconciled, as the office would have."""
    cutoff = cutoff or settings()["reconciled_up_to"]
    bank = pd.read_csv(TIMELINE / "bank_transactions.csv", dtype=str, keep_default_na=False)
    truth = pd.read_csv(TIMELINE / "truth.csv", dtype=str, keep_default_na=False)
    df = bank.merge(truth, on="TransactionID")
    df = df[df["Date"] <= cutoff]
    state = empty()
    for r in df.itertuples():
        if r.Kind in ("ignore", "other"):
            state["ignored"][r.TransactionID] = "reconciled before go-live"
        else:
            side = "supplier" if r.Kind == "supplier" else "customer"
            state["matches"][r.TransactionID] = {"items": r.Items.split("|"), "bank_group": [r.TransactionID],
                                                 "side": side, "source": "history"}
    save(state)
    return state


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Reset state/decisions.json to the office's past reconciliations.")
    ap.add_argument("--cutoff", help="reconciled up to this date (default: reconciled_up_to in settings)")
    args = ap.parse_args()
    s = seed_history(args.cutoff)
    print(f"Seeded {len(s['matches'])} historical matches and {len(s['ignored'])} ignored lines "
          f"up to {args.cutoff or settings()['reconciled_up_to']}")


if __name__ == "__main__":
    main()
