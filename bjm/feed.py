"""Simulate the daily exports: copy everything dated up to --as-of from the sample
timeline into inbox/, exactly as a bank feed, ServiceM8 export and Xero export would
look on that morning. In a client engagement this step is replaced by the
ServiceM8 / simPRO and Xero API connectors.

Usage:  python -m bjm.feed [--as-of YYYY-MM-DD]     (default: yesterday)
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import pandas as pd

from .common import INBOX, TIMELINE

FILES = {
    "bank_transactions.csv": "Date",
    "jobs_invoices.csv": "InvoiceDate",
    "materials.csv": "PurchaseDate",
}


def release(as_of: date) -> dict[str, int]:
    INBOX.mkdir(exist_ok=True)
    counts = {}
    for name, date_col in FILES.items():
        df = pd.read_csv(TIMELINE / name, dtype=str, keep_default_na=False)
        df = df[df[date_col] <= as_of.isoformat()]
        df.to_csv(INBOX / name, index=False)
        counts[name] = len(df)
    (INBOX / "AS_OF").write_text(as_of.isoformat())
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    args = ap.parse_args()
    counts = release(args.as_of)
    print(f"Exports as of {args.as_of}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
