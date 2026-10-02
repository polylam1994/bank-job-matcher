"""Daily run: match open bank lines to open invoices and material lines.

  1. Load the exports in inbox/ and the reconciliation state.
  2. Customer side: Fellegi-Sunter scores (Splink) -> one-to-one assignment (SciPy)
     -> instalments (several payments, one invoice) by subset-sum.
  3. Supplier side: identify the supplier -> single line, statement window, or
     subset-sum over that supplier's open lines.
  4. Sort every open line into auto-matched / suggested / unmatched / ignored,
     record auto-matches in the state, and write output/results.json.

Usage:  python -m bjm.run
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from . import state as st
from .common import (INBOX, OUTPUT, canonical_supplier, fmt_money, is_ignored, load_inbox, name_key,
                     settings, tokens)
from .linkage import Model, customer_comparisons, supplier_comparisons
from .subset_sum import Item, find_combos, rank, statement_windows

EPOCH = pd.Timestamp("1970-01-01")


def day(ts) -> int:
    return int((pd.Timestamp(ts) - EPOCH).days)


def cand_key(bank_ids: list[str], item_ids: list[str]) -> str:
    return ",".join(sorted(bank_ids)) + "->" + ",".join(sorted(item_ids))


# ---------------------------------------------------------------- table preparation

def bank_table(bank: pd.DataFrame, supplier_side: bool = False) -> pd.DataFrame:
    if supplier_side:
        names = [canonical_supplier(p) or p for p in bank["Payee"]]
        refs = [[] for _ in names]
    else:
        names = list(bank["Payee"])
        refs = [tokens(r) for r in bank["Reference"]]
    return pd.DataFrame({
        "unique_id": bank["TransactionID"].values,
        "label": (bank["Payee"] + " " + bank["Reference"]).str.strip().values,
        "name_key": [name_key(n) or None for n in names],
        "name_tokens": [tokens(n) for n in names],
        "ref_tokens": refs,
        "amount_cents": bank["amount_cents"].abs().astype("int64").values,
        "amount_alt_cents": pd.array([None] * len(bank), dtype="Int64"),
        "txn_date": bank["date"].values,
    })


def invoice_table(inv: pd.DataFrame, due: pd.Series | None = None) -> pd.DataFrame:
    amount = inv["total_cents"] if due is None else due
    return pd.DataFrame({
        "unique_id": inv["InvoiceNumber"].values,
        "label": (inv["InvoiceNumber"] + " " + inv["CustomerName"] + " - " + inv["JobDescription"]).values,
        "name_key": [name_key(n) or None for n in inv["CustomerName"]],
        "name_tokens": [tokens(n) for n in inv["CustomerName"]],
        "ref_tokens": [tokens(f"{a} {s} {d} {n}") for a, s, d, n in
                       zip(inv["SiteAddress"], inv["Suburb"], inv["JobDescription"], inv["CustomerName"])],
        "amount_cents": np.asarray(amount, dtype="int64"),
        "amount_alt_cents": pd.array(inv["total_cents"].values, dtype="Int64"),
        "txn_date": inv["date"].values,
    })


def material_table(mat: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "unique_id": mat["LineID"].values,
        "label": (mat["LineID"] + " " + mat["Supplier"] + " - " + mat["Item"] + " (" + mat["JobNumber"] + ")").values,
        "name_key": [name_key(s) for s in mat["Supplier"]],
        "name_tokens": [tokens(s) for s in mat["Supplier"]],
        "ref_tokens": [[] for _ in range(len(mat))],
        "amount_cents": mat["cost_cents"].astype("int64").values,
        "amount_alt_cents": pd.array([None] * len(mat), dtype="Int64"),
        "txn_date": mat["date"].values,
    })


def labels_for(state: dict, side: str, bank_ids: set, item_ids: set) -> pd.DataFrame:
    rows = [(b, i) for b, m in state["matches"].items() if m["side"] == side and b in bank_ids
            for i in m["items"] if i in item_ids]
    return pd.DataFrame(rows, columns=["unique_id_l", "unique_id_r"])


# ---------------------------------------------------------------- output helpers

def item_view(kind: str, row) -> dict:
    if kind == "invoice":
        return {"id": row.InvoiceNumber, "kind": "invoice", "date": row.InvoiceDate, "cents": int(row.total_cents),
                "title": f"{row.CustomerName} - {row.JobDescription}",
                "detail": f"{row.InvoiceType} invoice · {row.JobNumber} · {row.SiteAddress}"}
    return {"id": row.LineID, "kind": "material", "date": row.PurchaseDate, "cents": int(row.cost_cents),
            "title": f"{row.Supplier} - {row.Item}", "detail": f"{row.JobNumber}"}


def bank_view(r) -> dict:
    return {"id": r.TransactionID, "date": r.Date, "cents": int(r.amount_cents), "payee": r.Payee,
            "reference": r.Reference, "description": r.Description}


# ---------------------------------------------------------------- customer side

def match_customers(data, state, open_bank, cfg, out, applied):
    th, tol = cfg["thresholds"], cfg["customer"]["amount_tolerance_cents"]
    inv, bank_all = data["invoices"], data["bank"]

    # what is still owed on each invoice, after matches already made
    paid: dict[str, int] = {}
    amounts = dict(zip(bank_all["TransactionID"], bank_all["amount_cents"]))
    for b, m in state["matches"].items():
        if m["side"] == "customer":
            for i in m["items"]:
                paid[i] = paid.get(i, 0) + amounts.get(b, 0) // len(m["items"])
    inv = inv.assign(due_cents=inv["total_cents"] - inv["InvoiceNumber"].map(paid).fillna(0).astype(int))
    open_inv = inv[inv["due_cents"] > tol].reset_index(drop=True)
    credits = open_bank[open_bank["amount_cents"] > 0].reset_index(drop=True)
    inv_by_id = {r.InvoiceNumber: r for r in inv.itertuples()}

    model = Model("customer", customer_comparisons(tol), cfg["customer"]["date_window_days"])
    all_credits = bank_all[(bank_all["amount_cents"] > 0)]
    train_bank, train_items = bank_table(all_credits), invoice_table(inv)
    model.train(train_bank, train_items,
                labels_for(state, "customer", set(train_bank.unique_id), set(train_items.unique_id)))
    out["model"]["customer"] = {"training": model.training_method, "parameters": model.parameters()}

    pred = model.predict(bank_table(credits), invoice_table(open_inv, open_inv["due_cents"]))
    rejected = state["rejected"]
    all_pairs = pred
    if not pred.empty:
        pred = pred[[cand_key([b], [i]) not in rejected.get(b, []) for b, i in zip(pred.bank_id, pred.item_id)]]
        all_pairs = pred
        pred = pred[pred.match_probability >= th["suggest_min_probability"]]

    # one-to-one assignment on match weight, so one invoice is never claimed twice
    assigned: dict[str, str] = {}
    if not pred.empty:
        b_ids, i_ids = sorted(pred.bank_id.unique()), sorted(pred.item_id.unique())
        bi, ii = {b: k for k, b in enumerate(b_ids)}, {i: k for k, i in enumerate(i_ids)}
        cost = np.full((len(b_ids), len(i_ids)), 1e6)
        for r in pred.itertuples():
            cost[bi[r.bank_id], ii[r.item_id]] = -r.match_weight
        for rr, cc in zip(*linear_sum_assignment(cost)):
            if cost[rr, cc] < 1e6:
                assigned[b_ids[rr]] = i_ids[cc]

    best_other_claim = {}  # item -> best weight from any bank line, for the margin test
    by_bank = {}
    if not pred.empty:
        for item, g in pred.groupby("item_id"):
            best_other_claim[item] = g.sort_values("match_weight", ascending=False)[["bank_id", "match_weight"]].values
        by_bank = {b: g.sort_values("match_weight", ascending=False) for b, g in pred.groupby("bank_id")}

    # several credits paying one invoice (instalments): subset-sum over credits from the same payer.
    # Uses all scored pairs - each instalment alone is weak evidence, together they add up - but
    # skips credits that already have a strong single-invoice match.
    instalments: dict[str, list[dict]] = {}
    if not all_pairs.empty:
        n_amount = len(model.comparisons["amount"])
        part_gamma = n_amount - 1 - 2  # "smaller - could be a part payment"
        strong = set(all_pairs[(all_pairs.match_probability >= th["auto_match_probability"])
                               & (all_pairs.gamma_amount >= n_amount - 1 - 1)].bank_id)
        weak = all_pairs[(all_pairs.gamma_amount == part_gamma) & (all_pairs.gamma_name >= 1)
                         & ~all_pairs.bank_id.isin(strong)]
        cred = credits.set_index("TransactionID")
        for item, g in weak.groupby("item_id"):
            due = int(open_inv.loc[open_inv.InvoiceNumber == item, "due_cents"].iloc[0])
            for payer, ids in g.groupby(g.bank_id.map(lambda b: name_key(cred.at[b, "Payee"])))["bank_id"]:
                pool = [Item(b, int(cred.at[b, "amount_cents"]), day(cred.at[b, "date"])) for b in ids.unique()]
                if len(pool) < 2:
                    continue
                res = find_combos(pool, due, tol, max_items=3)
                for combo in rank([c for c in res.combos if len(c) >= 2], due, max(x.day for x in pool)):
                    combo_ids = [x.id for x in combo]
                    key = cand_key(combo_ids, [item])
                    if any(key in rejected.get(b, []) for b in combo_ids):
                        continue
                    cand = {"key": key, "method": "instalments", "bank_ids": combo_ids,
                            "items": [item_view("invoice", inv_by_id[item])], "probability": None, "weight": None,
                            "reasons": [{"factor": "payer", "detail": f"all {len(combo_ids)} payments from the same payer",
                                         "weight": None},
                                        {"factor": "sum", "detail": f"together they add up to the {fmt_money(due)} "
                                         f"still owed on {item}", "weight": None}]}
                    for b in combo_ids:
                        instalments.setdefault(b, []).append(cand)

    for r in credits.itertuples():
        b = r.TransactionID
        line = bank_view(r) | {"side": "customer", "candidates": []}
        rows = by_bank.get(b)
        cands = []
        if rows is not None:
            order = list(rows.itertuples())
            if b in assigned:
                order.sort(key=lambda x: x.item_id != assigned[b])
            for x in order[: th["max_suggestions"]]:
                cands.append({"key": cand_key([b], [x.item_id]), "method": "fellegi-sunter", "bank_ids": [b],
                              "items": [item_view("invoice", inv_by_id[x.item_id])],
                              "probability": round(float(x.match_probability), 4),
                              "weight": round(float(x.match_weight), 2), "reasons": model.reasons(x._asdict())})
        # instalments go first only when no single invoice is a convincing match
        inst = instalments.get(b, [])[:2]
        if cands and cands[0]["probability"] >= th["auto_match_probability"]:
            cands = cands + inst
        else:
            cands = inst + cands
        line["candidates"] = cands[: th["max_suggestions"] + 1]

        group = "unmatched"
        if b in assigned and cands:
            top = next(c for c in cands if c["method"] == "fellegi-sunter" and c["items"][0]["id"] == assigned[b])
            w = top["weight"]
            runner_up = max([c["weight"] for c in cands if c is not top and c["weight"] is not None], default=-99)
            claims = [cw for (cb, cw) in best_other_claim.get(assigned[b], []) if cb != b]
            amount_ok = top["reasons"][0]["detail"] in ("exact", f"within ${tol / 100:.2f}")
            if (top["probability"] >= th["auto_match_probability"] and amount_ok
                    and w - runner_up >= th["auto_min_margin_weight"]
                    and w - max(claims, default=-99) >= th["auto_min_margin_weight"] and b not in instalments):
                group = "auto"
                line["candidates"] = [top] + [c for c in cands if c is not top]
            else:
                group = "suggested"
        elif cands:
            group = "suggested"
        line["group"] = group
        if group == "auto":
            applied[b] = {"items": [line["candidates"][0]["items"][0]["id"]], "bank_group": [b],
                          "side": "customer", "source": "auto"}
        out["lines"].append(line)


# ---------------------------------------------------------------- supplier side

def match_suppliers(data, state, open_bank, cfg, out, applied):
    th, scfg, sub = cfg["thresholds"], cfg["supplier"], cfg["subset_sum"]
    tol = scfg["amount_tolerance_cents"]
    mat, bank_all = data["materials"], data["bank"]
    used = {i for m in state["matches"].values() if m["side"] == "supplier" for i in m["items"]}
    open_mat = mat[~mat.LineID.isin(used)].reset_index(drop=True)
    debits = open_bank[open_bank["amount_cents"] < 0].sort_values(["date", "TransactionID"]).reset_index(drop=True)
    mat_by_id = {r.LineID: r for r in mat.itertuples()}

    model = Model("supplier", supplier_comparisons(), scfg["date_window_days"])
    all_debits = bank_all[(bank_all.amount_cents < 0) & ~bank_all.Payee.map(is_ignored)]
    tb, ti = bank_table(all_debits, supplier_side=True), material_table(mat)
    model.train(tb, ti, labels_for(state, "supplier", set(tb.unique_id), set(ti.unique_id)))
    out["model"]["supplier"] = {"training": model.training_method, "parameters": model.parameters()}
    pred = model.predict(bank_table(debits, supplier_side=True), material_table(open_mat))
    by_bank = {b: g.sort_values("match_weight", ascending=False) for b, g in pred.groupby("bank_id")} if not pred.empty else {}

    consumed: set[str] = set()
    for r in debits.itertuples():
        b, target = r.TransactionID, -int(r.amount_cents)
        line = bank_view(r) | {"side": "supplier", "candidates": [], "group": "unmatched"}
        supplier = canonical_supplier(r.Payee)
        how = f"payee '{r.Payee}' is a known alias of {supplier}" if supplier else ""
        if not supplier and b in by_bank:
            top = by_bank[b].iloc[0]
            if top.gamma_name >= 1:
                supplier = mat_by_id[top.item_id].Supplier
                how = f"payee name is similar to {supplier}"
        if not supplier:
            out["lines"].append(line)
            continue

        d0 = day(r.date)
        pool = [Item(m.LineID, int(m.cost_cents), day(m.date), m.JobNumber) for m in open_mat.itertuples()
                if m.Supplier == supplier and m.LineID not in consumed
                and d0 - scfg["date_window_days"] <= day(m.date) <= d0 + 1]
        targets = [target]
        if scfg.get("try_ex_gst"):
            targets.append(round(target / (1 + cfg["gst_rate"])))

        # Search in order of structural strength. Coincidences are common: 24 open lines can
        # hold 20+ unrelated combinations that add up to the exact cent, so a wider search
        # is only tried when a stronger one finds nothing, and its results are never auto-matched.
        rejected = state["rejected"].get(b, [])
        near = [x for x in pool if abs(x.day - d0) <= scfg["same_day_window_days"]]
        statement = ("statement", lambda t: statement_windows(pool, t, tol), True)
        same_day = ("subset-sum", lambda t: find_combos(near, t, tol, sub["max_items"], sub["max_solutions"],
                                                        sub["max_nodes"]), True)
        single = ("single line", lambda t: [[x] for x in pool if abs(x.cents - t) <= tol], True)
        wide = ("wide search", lambda t: find_combos(pool, t, tol, sub["max_items"], sub["max_solutions"],
                                                     sub["max_nodes"]) if len(pool) <= 25 else None, False)
        billing = cfg["supplier_billing"].get(supplier, "card")
        stages = {"statement": [statement, wide], "card": [same_day, wide],
                  "invoice": [single, same_day, wide]}[billing]
        ranked, method, can_auto, complete = [], "", False, True
        for method, search, can_auto in stages:
            combos = []
            for t in targets:
                res = search(t)
                if res is None:
                    continue
                if hasattr(res, "combos"):
                    complete &= res.complete
                    res = res.combos
                combos += res
            ranked = [c for c in rank(combos, target, d0) if cand_key([b], [x.id for x in c]) not in rejected]
            if ranked:
                break
        method_of = {frozenset(x.id for x in c): method for c in ranked}

        for c in ranked[: th["max_suggestions"]]:
            ids = [x.id for x in c]
            method = method_of[frozenset(ids)]
            days = sorted(x.day for x in c)
            span = (EPOCH + pd.Timedelta(days=days[0])).strftime("%d %b"), (EPOCH + pd.Timedelta(days=days[-1])).strftime("%d %b")
            reasons = [{"factor": "supplier", "detail": how, "weight": None},
                       {"factor": "sum", "detail": f"{len(c)} line{'s' if len(c) > 1 else ''} add up exactly to "
                        f"{fmt_money(target)}" if len(c) > 1 else f"amount is exactly {fmt_money(target)}", "weight": None},
                       {"factor": "dates", "detail": span[0] if span[0] == span[1] else f"{span[0]} to {span[1]}"
                        + (" - one contiguous statement period" if method == "statement" else ""), "weight": None},
                       {"factor": "search", "detail": f"{len(ranked)} possible combination{'s' if len(ranked) != 1 else ''} "
                        f"among {len(pool)} open {supplier} lines ({billing} supplier)"
                        + ("" if complete else " - search budget reached")
                        + ("" if can_auto else " - wide search, coincidences likely, needs review"), "weight": None}]
            prob = weight = None
            if len(c) == 1 and b in by_bank:
                hit = by_bank[b][by_bank[b].item_id == ids[0]]
                if not hit.empty:
                    prob, weight = round(float(hit.match_probability.iloc[0]), 4), round(float(hit.match_weight.iloc[0]), 2)
                    reasons += [dict(x, factor="F-S " + x["factor"]) for x in model.reasons(hit.iloc[0])]
            line["candidates"].append({"key": cand_key([b], ids), "method": method, "bank_ids": [b],
                                       "items": [item_view("material", mat_by_id[i]) for i in ids],
                                       "probability": prob, "weight": weight, "reasons": reasons})

        if len(ranked) == 1 and complete and can_auto:
            line["group"] = "auto"
            ids = [i["id"] for i in line["candidates"][0]["items"]]
            consumed.update(ids)
            applied[b] = {"items": ids, "bank_group": [b], "side": "supplier", "source": "auto"}
        elif ranked:
            line["group"] = "suggested"
        out["lines"].append(line)


# ---------------------------------------------------------------- main

def run() -> dict:
    cfg = settings()
    data = load_inbox()
    state = st.load()
    as_of = (INBOX / "AS_OF").read_text().strip() if (INBOX / "AS_OF").exists() else date.today().isoformat()

    done = set(state["matches"]) | set(state["ignored"])
    for m in state["matches"].values():
        done |= set(m.get("bank_group", []))
    open_bank = data["bank"][~data["bank"].TransactionID.isin(done)]
    ignored = open_bank[open_bank.Payee.map(is_ignored)]
    open_bank = open_bank[~open_bank.Payee.map(is_ignored)]

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": as_of,
           "business": cfg["business"], "lines": [], "model": {}}
    applied: dict = {}
    match_customers(data, state, open_bank, cfg, out, applied)
    match_suppliers(data, state, open_bank, cfg, out, applied)
    for r in ignored.itertuples():
        out["lines"].append(bank_view(r) | {"side": "expense", "group": "ignored", "candidates": [],
                                            "note": "Running cost (wages, fuel, tax, subscriptions) - no job to match"})
        state["ignored"][r.TransactionID] = "ignore rule"

    state["matches"].update(applied)
    st.save(state)

    groups = ["auto", "suggested", "unmatched", "ignored"]
    out["stats"] = {g: {"count": sum(l["group"] == g for l in out["lines"]),
                        "cents": sum(abs(l["cents"]) for l in out["lines"] if l["group"] == g)} for g in groups}
    out["lines"].sort(key=lambda l: (groups.index(l["group"]), l["date"], l["id"]))
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def main() -> None:
    out = run()
    s = out["stats"]
    print(f"As of {out['as_of']}: " + " | ".join(f"{g} {v['count']} ({fmt_money(v['cents'])})" for g, v in s.items()))
    print(f"Customer model: {out['model']['customer']['training']}")
    print(f"Supplier model: {out['model']['supplier']['training']}")


if __name__ == "__main__":
    main()
