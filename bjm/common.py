"""Shared paths, settings, text normalisation and data loading."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "settings.json"
TIMELINE = ROOT / "data" / "timeline"
INBOX = ROOT / "inbox"
STATE = ROOT / "state" / "decisions.json"
OUTPUT = ROOT / "output"


@lru_cache
def settings() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def to_cents(series: pd.Series) -> pd.Series:
    """'1,284.50' -> 128450, exact (no float rounding drift)."""
    return series.astype(str).str.replace(",", "").map(lambda s: int(round(float(s) * 100)))


def tokens(text: str | None) -> list[str]:
    """Upper-case, strip punctuation, expand abbreviations, drop stopwords and initials."""
    if not isinstance(text, str) or not text.strip():
        return []
    cfg = settings()
    text = text.upper().replace("&", " ").replace("'", "")
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    out: list[str] = []
    for tok in text.split():
        for t in cfg["abbreviations"].get(tok, tok).split():
            if len(t) > 1 and t not in cfg["stopwords"] and t not in out:
                out.append(t)
    return out


def name_key(text: str | None) -> str:
    """Order-independent name key for Jaro-Winkler, e.g. 'SMITH JOHN' == 'JOHN SMITH'."""
    return " ".join(sorted(tokens(text)))


def canonical_supplier(payee: str) -> str | None:
    up = (payee or "").upper()
    for key, name in settings()["supplier_aliases"].items():
        if key in up:
            return name
    return None


def is_ignored(payee: str) -> bool:
    up = (payee or "").upper() + " "
    return any(k in up for k in settings()["ignore_payees"])


def fmt_money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def load_inbox() -> dict[str, pd.DataFrame]:
    """Read the three exports as they would arrive from the bank, ServiceM8 and Xero."""
    bank = pd.read_csv(INBOX / "bank_transactions.csv", dtype=str, keep_default_na=False)
    bank["amount_cents"] = to_cents(bank["Amount"])
    bank["date"] = pd.to_datetime(bank["Date"])

    inv = pd.read_csv(INBOX / "jobs_invoices.csv", dtype=str, keep_default_na=False)
    inv["total_cents"] = to_cents(inv["Total"])
    inv["date"] = pd.to_datetime(inv["InvoiceDate"])

    mat = pd.read_csv(INBOX / "materials.csv", dtype=str, keep_default_na=False)
    mat["cost_cents"] = to_cents(mat["CostIncGST"])
    mat["date"] = pd.to_datetime(mat["PurchaseDate"])
    return {"bank": bank, "invoices": inv, "materials": mat}
