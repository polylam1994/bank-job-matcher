"""Generate a realistic, fully fictional data timeline for a Sydney plumbing business.

Writes to data/timeline/:
  bank_transactions.csv  - bank feed (Xero-style statement lines), no invoice/job IDs
  jobs_invoices.csv      - ServiceM8 jobs joined to their Xero invoices
  materials.csv          - ServiceM8 job materials with supplier and cost
  truth.csv              - the hidden answer key: which bank line paid which item(s)

The timeline runs past "today" so the daily feed (bjm.feed) can keep releasing new
bank lines; nothing in truth.csv is ever shown to the matcher.

Usage:  python data/generate_sample.py
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SEED = 42
START = date(2025, 10, 1)
END = date(2027, 3, 31)
OUT = Path(__file__).parent / "timeline"

rng = random.Random(SEED)

SUBURBS = {
    "Bondi": ["Curlewis St", "Wairoa Ave", "Glenayr Ave", "Warners Ave"],
    "Coogee": ["Arden St", "Dolphin St", "Mount St", "Bream St"],
    "Randwick": ["Belmore Rd", "Avoca St", "Clovelly Rd", "Frenchmans Rd"],
    "Paddington": ["Glenmore Rd", "Underwood St", "Gurner St", "Hargrave St"],
    "Newtown": ["Wilson St", "Australia St", "Station St", "Bucknell St"],
    "Marrickville": ["Victoria Rd", "Livingstone Rd", "Illawarra Rd", "Despointes St"],
    "Leichhardt": ["Norton St", "Marion St", "Short St", "Allen St"],
    "Balmain": ["Darling St", "Mort St", "Beattie St", "Ewenton St"],
    "Glebe": ["Bridge Rd", "Wigram Rd", "Toxteth Rd", "Forest St"],
    "Surry Hills": ["Crown St", "Bourke St", "Riley St", "Fitzroy St"],
    "Mosman": ["Military Rd", "Raglan St", "Belmont Rd", "Cowles Rd"],
    "Neutral Bay": ["Yeo St", "Ben Boyd Rd", "Wycombe Rd", "Kurraba Rd"],
    "Manly": ["Pittwater Rd", "Darley Rd", "Addison Rd", "Ashburner St"],
    "Dee Why": ["Pacific Pde", "Oaks Ave", "Fisher Rd", "Redman Rd"],
    "Chatswood": ["Victoria Ave", "Archer St", "Help St", "Johnson St"],
    "Rozelle": ["Victoria Rd", "Evans St", "Wellington St", "Terry St"],
    "Annandale": ["Johnston St", "Booth St", "Nelson St", "Trafalgar St"],
    "Maroubra": ["Anzac Pde", "Malabar Rd", "Fitzgerald Ave", "Haig St"],
    "Bronte": ["Bronte Rd", "Macpherson St", "St Thomas St", "Hewlett St"],
    "Kensington": ["Anzac Pde", "Doncaster Ave", "Todman Ave", "Day Ave"],
}

FIRST = ["James", "Olivia", "William", "Charlotte", "Jack", "Amelia", "Noah", "Isla",
         "Thomas", "Mia", "Liam", "Grace", "Henry", "Chloe", "Lucas", "Sophie",
         "Ethan", "Emily", "Daniel", "Hannah", "Wei", "Mei", "Raj", "Priya",
         "Giovanni", "Sofia", "Kostas", "Eleni", "Sean", "Aoife", "Minh", "Linh"]
LAST = ["Smith", "Nguyen", "Williams", "Brown", "Wilson", "Taylor", "Johnson", "White",
        "Martin", "Anderson", "Thompson", "Walker", "Harris", "Lee", "Ryan", "Robinson",
        "Kelly", "King", "Chen", "Wang", "Patel", "Singh", "Rossi", "Papadopoulos",
        "O'Brien", "Murphy", "Tran", "Le", "Campbell", "Mitchell", "Clarke", "Hughes"]
BUSINESS = ["{sub} Strata Management", "Coastline Property Management", "{last} Property Group",
            "The Daily Grind Cafe", "{sub} Physiotherapy", "Harbour Realty {sub}",
            "{last} & Co Accountants", "Little Sprouts Childcare {sub}"]

# (description, price range in dollars, deposit probability, material profile)
JOB_TYPES = [
    ("Blocked drain clearing", (220, 660), 0.0, "small"),
    ("Leaking tap repair", (165, 330), 0.0, "small"),
    ("Hot water system replacement", (1900, 3600), 0.35, "hws"),
    ("Toilet cistern replacement", (380, 850), 0.0, "medium"),
    ("Gas leak detection and repair", (280, 900), 0.0, "small"),
    ("Bathroom renovation rough-in", (4500, 14000), 0.9, "large"),
    ("Burst pipe repair", (350, 1400), 0.0, "medium"),
    ("Gas cooktop installation", (450, 1100), 0.0, "medium"),
    ("Roof gutter replacement", (1200, 3800), 0.4, "large"),
    ("Call-out and inspection", (165, 165), 0.0, "none"),
]
JOB_WEIGHTS = [16, 14, 7, 8, 7, 3, 9, 6, 4, 10]
REF_KEYWORDS = {
    "Hot water system replacement": ["HWS", "HOT WATER", "HW SYSTEM"],
    "Bathroom renovation rough-in": ["BATHRM", "BATHROOM RENO", "BTHRM"],
    "Blocked drain clearing": ["DRAIN", "BLOCKED DRAIN"],
    "Leaking tap repair": ["TAP", "TAP REPAIR"],
    "Toilet cistern replacement": ["TOILET", "CISTERN"],
    "Gas leak detection and repair": ["GAS LEAK", "GAS"],
    "Burst pipe repair": ["BURST PIPE", "PIPE"],
    "Gas cooktop installation": ["COOKTOP", "GAS COOKTOP"],
    "Roof gutter replacement": ["GUTTER", "GUTTERS"],
    "Call-out and inspection": ["CALLOUT", "INSPECTION"],
}

MATERIALS = {
    "Reece": [("Copper pipe 15mm x 6m", 48, 95), ("PEX fittings pack", 22, 64), ("Flick mixer tap", 129, 389),
              ("Pressure limiting valve", 89, 165), ("Shower mixer valve", 210, 540), ("Waste trap 40mm", 14, 38)],
    "Tradelink": [("Toilet suite", 299, 890), ("Cistern inlet valve", 32, 58), ("Basin waste", 18, 46),
                  ("Flexible hoses pair", 16, 34), ("Gas bayonet fitting", 45, 92)],
    "Bunnings": [("PVC pipe 100mm x 3m", 28, 49), ("Silicone sealant", 9, 19), ("Thread tape 5pk", 6, 12),
                 ("Pipe clips 10pk", 8, 17), ("Drain cleaner", 12, 26), ("Gutter brackets 10pk", 21, 44)],
    "Rheem": [("Rheem Stellar 330L gas HWS", 1450, 2350), ("Rheem 26L continuous flow", 980, 1480)],
}
PROFILE = {
    "none": [],
    "small": [("Bunnings", 0.6, (1, 2)), ("Reece", 0.3, (1, 2))],
    "medium": [("Reece", 0.6, (1, 3)), ("Tradelink", 0.5, (1, 2)), ("Bunnings", 0.4, (1, 2))],
    "hws": [("Rheem", 1.0, (1, 1)), ("Reece", 0.8, (1, 3)), ("Bunnings", 0.3, (1, 2))],
    "large": [("Reece", 1.0, (3, 6)), ("Tradelink", 0.7, (1, 3)), ("Bunnings", 0.6, (1, 3))],
}
BANK_PAYEE = {
    "Reece": ["REECE PTY LTD", "REECE PTY LTD DD", "REECE TRADE ACC"],
    "Tradelink": ["TRADELINK PLUMBING SUPP", "TRADELINK PTY LTD"],
    "Bunnings": ["BUNNINGS 742000 ALEXANDRIA", "BUNNINGS 765000 RANDWICK", "BUNNINGS 703000 ROZELLE"],
    "Rheem": ["RHEEM AUSTRALIA PTY", "RHEEM AUST PTY LTD"],
}


def money(lo: float, hi: float, step: int = 5) -> int:
    """A price in cents, usually rounded to $5, sometimes with odd cents."""
    dollars = rng.uniform(lo, hi)
    if rng.random() < 0.8:
        return int(round(dollars / step) * step * 100)
    return int(round(dollars * 100))


def business_days(a: date, b: date):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def make_customers(n: int = 320) -> list[dict]:
    customers = []
    for i in range(n):
        sub = rng.choice(list(SUBURBS))
        street = rng.choice(SUBURBS[sub])
        num = rng.randint(1, 180)
        roll = rng.random()
        if roll < 0.12:
            name = rng.choice(BUSINESS).format(sub=sub, last=rng.choice(LAST))
            kind = "business"
            first, last, partner = None, None, None
        else:
            first, last = rng.choice(FIRST), rng.choice(LAST)
            partner = rng.choice(FIRST) if roll < 0.30 else None
            name = f"{first} & {partner} {last}" if partner else f"{first} {last}"
            kind = "couple" if partner else "person"
        customers.append(dict(cid=f"C{i:04d}", name=name, kind=kind, first=first, last=last,
                              partner=partner, suburb=sub, street=street, num=num))
    return customers


def bank_payee_for(c: dict) -> str:
    """How a customer's name shows up on a bank statement (truncated at 22 chars)."""
    if c["kind"] == "business":
        base = c["name"].upper().replace("&", "AND")
        s = rng.choice([base, base + " PTY LTD", base.replace("MANAGEMENT", "MGMT")])
    else:
        f, l = c["first"].upper(), c["last"].upper().replace("'", "")
        opts = [f"{f[0]} {l}", f"{f} {l}", f"{l} {f[0]}", f"MR {f[0]} {l}" if rng.random() < .5 else f"MS {f[0]} {l}"]
        if c["partner"]:
            p = c["partner"].upper()
            opts += [f"{f[0]} & {p[0]} {l}", f"{f} AND {p} {l}", f"{p[0]} {l}"]
        s = rng.choice(opts)
    return s[:22]


def bank_reference_for(c: dict, job_desc: str) -> str:
    sub = c["suburb"].upper()
    last = (c["last"] or c["name"].split()[0]).upper().replace("'", "")
    street = c["street"].upper()
    opts = ["", "", last, sub, f"{c['num']} {street}", rng.choice(REF_KEYWORDS[job_desc]),
            "PLUMBING", "PLUMBER", f"{last} {sub}", f"{rng.choice(REF_KEYWORDS[job_desc])} {sub}",
            "INVOICE", sub.lower() + " plumbing"]
    return rng.choice(opts)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    customers = make_customers()
    jobs, invoices, materials, bank, truth = [], [], [], [], []
    inv_no, job_no, line_no = 2001, 1001, 30001

    # ---- jobs, invoices, materials ----
    for d in business_days(START, END - timedelta(days=50)):
        for _ in range(sum(rng.random() < 0.45 for _ in range(3))):  # ~1.35 jobs per day
            desc, (lo, hi), p_dep, profile = rng.choices(JOB_TYPES, weights=JOB_WEIGHTS)[0]
            c = rng.choice(customers)
            total = money(lo, hi)
            job_id = f"J-{job_no}"; job_no += 1
            completed = d
            address = f"{c['num']} {c['street']}, {c['suburb']} NSW"
            base = dict(JobNumber=job_id, CustomerName=c["name"], SiteAddress=address, Suburb=c["suburb"],
                        JobDescription=desc, CompletedDate=completed.isoformat())
            if rng.random() < p_dep:
                dep = int(round(total * 0.3 / 1000) * 1000)  # 30%, rounded to $10
                booked = completed - timedelta(days=rng.randint(7, 21))
                invoices.append(dict(base, InvoiceNumber=f"INV-{inv_no}", InvoiceType="Deposit",
                                     InvoiceDate=booked.isoformat(), TotalCents=dep, _cust=c)); inv_no += 1
                total -= dep
            inv_date = completed + timedelta(days=rng.choice([0, 0, 1, 1, 2]))
            invoices.append(dict(base, InvoiceNumber=f"INV-{inv_no}", InvoiceType="Final",
                                 InvoiceDate=inv_date.isoformat(), TotalCents=total, _cust=c)); inv_no += 1

            for supplier, p, (kmin, kmax) in PROFILE[profile]:
                if rng.random() > p:
                    continue
                buy_date = completed - timedelta(days=rng.choice([0, 0, 0, 1, 2]))
                for _ in range(rng.randint(kmin, kmax)):
                    item, plo, phi = rng.choice(MATERIALS[supplier])
                    materials.append(dict(LineID=f"M-{line_no}", JobNumber=job_id, Supplier=supplier, Item=item,
                                          Qty=1, PurchaseDate=buy_date.isoformat(),
                                          CostCents=int(round(rng.uniform(plo, phi) * 100))))
                    line_no += 1
            jobs.append(job_id)

    # a few supplier credit notes (returned items) on trade accounts
    for m in rng.sample([m for m in materials if m["Supplier"] in ("Reece", "Tradelink")], 12):
        materials.append(dict(m, LineID=f"M-{line_no}", Item="Credit: returned " + m["Item"].lower(),
                              CostCents=-m["CostCents"],
                              PurchaseDate=(date.fromisoformat(m["PurchaseDate"]) + timedelta(days=3)).isoformat()))
        line_no += 1

    # ---- customer payments ----
    def add_bank(d: date, cents: int, payee: str, desc: str, ref: str, kind: str, items: list[str]):
        bank.append(dict(Date=d, AmountCents=cents, Payee=payee, Description=desc, Reference=ref,
                         _kind=kind, _items=items))

    for inv in invoices:
        c = inv["_cust"]
        inv_date = date.fromisoformat(inv["InvoiceDate"])
        roll = rng.random()
        if roll < 0.04:
            continue  # never paid: stays overdue
        third_party = rng.random() < 0.08
        if third_party:
            payee = rng.choice([f"{rng.choice(FIRST)[0]} {rng.choice(LAST).upper()}",
                                f"HARBOUR REALTY {c['suburb'].upper()}", "COASTLINE PROP MGMT"])[:22].upper()
            ref = rng.choice([c["suburb"].upper(), f"{c['num']} {c['street'].upper()}",
                              f"{c['suburb'].upper()} {rng.choice(REF_KEYWORDS[inv['JobDescription']])}"])
        else:
            payee, ref = bank_payee_for(c), bank_reference_for(c, inv["JobDescription"])
        delay = rng.randint(0, 5) if inv["InvoiceType"] == "Deposit" else min(int(rng.expovariate(1 / 9)) + 1, 42)
        pay_date = inv_date + timedelta(days=delay)
        amt = inv["TotalCents"]
        desc = rng.choice(["OSKO PAYMENT", "TRANSFER FROM", "DIRECT CREDIT", "BPAY"])
        if roll < 0.08 and amt >= 40000:  # two instalments
            first = int(round(amt * rng.choice([0.4, 0.5, 0.6]) / 1000) * 1000)
            add_bank(pay_date, first, payee, desc, ref, "customer_part", [inv["InvoiceNumber"]])
            add_bank(pay_date + timedelta(days=rng.randint(10, 28)), amt - first, payee, desc, ref,
                     "customer_part", [inv["InvoiceNumber"]])
        else:
            if roll < 0.11:  # short-paid by a few cents up to $2 (rounding, fees)
                amt -= rng.choice([1, 5, 50, 100, 150, 200])
            add_bank(pay_date, amt, payee, desc, ref, "customer", [inv["InvoiceNumber"]])

    # ---- supplier payments ----
    by_supplier: dict[str, list[dict]] = {}
    for m in materials:
        by_supplier.setdefault(m["Supplier"], []).append(m)

    # Bunnings: card, one debit per (job, day) receipt
    receipts: dict[tuple, list[dict]] = {}
    for m in by_supplier["Bunnings"]:
        receipts.setdefault((m["JobNumber"], m["PurchaseDate"]), []).append(m)
    for (job, d), lines in receipts.items():
        add_bank(date.fromisoformat(d), -sum(x["CostCents"] for x in lines), rng.choice(BANK_PAYEE["Bunnings"]),
                 "VISA PURCHASE", "", "supplier", [x["LineID"] for x in lines])
    # Rheem: per invoice, 14-30 days
    for m in by_supplier["Rheem"]:
        add_bank(date.fromisoformat(m["PurchaseDate"]) + timedelta(days=rng.randint(14, 30)), -m["CostCents"],
                 rng.choice(BANK_PAYEE["Rheem"]), "DIRECT DEBIT", "", "supplier", [m["LineID"]])

    # Reece: fortnightly statements; Tradelink: monthly statements
    def statements(supplier: str, period_of, pay_date_of):
        groups: dict = {}
        for m in by_supplier[supplier]:
            groups.setdefault(period_of(date.fromisoformat(m["PurchaseDate"])), []).append(m)
        for period, lines in sorted(groups.items()):
            total = sum(x["CostCents"] for x in lines)
            if total > 0:
                add_bank(pay_date_of(period), -total, rng.choice(BANK_PAYEE[supplier]), "DIRECT DEBIT",
                         "STATEMENT", "supplier", [x["LineID"] for x in lines])

    def next_month(y, mth):
        return (y + 1, 1) if mth == 12 else (y, mth + 1)

    statements("Reece",
               lambda d: (d.year, d.month, 1 if d.day <= 15 else 2),
               lambda p: date(p[0], p[1], 20 + rng.randint(0, 2)) if p[2] == 1
               else date(*next_month(p[0], p[1]), 5 + rng.randint(0, 2)))
    statements("Tradelink",
               lambda d: (d.year, d.month),
               lambda p: date(*next_month(*p), 12 + rng.randint(0, 3)))

    # ---- running costs that are not job-related (should be ignored) and odd credits ----
    staff = ["PAY - J OCONNOR", "PAY - M TRAN", "PAY - S PATEL"]
    d = START
    while d <= END:
        if d.weekday() == 3:
            for s in staff:
                add_bank(d, -rng.randint(165000, 215000), s, "PAYROLL", "WAGES", "ignore", [])
        if d.weekday() == 1:
            add_bank(d, -rng.randint(9000, 21000), rng.choice(["AMPOL ALEXANDRIA", "BP MASCOT"]), "VISA PURCHASE",
                     "", "ignore", [])
        if d.day == 3:
            add_bank(d, -18900, "TELSTRA", "DIRECT DEBIT", "", "ignore", [])
            add_bank(d, -64250, "TOYOTA FINANCE", "DIRECT DEBIT", "VEHICLE LEASE", "ignore", [])
        if d.day == 8:
            add_bank(d, -21400, "AAMI INSURANCE", "DIRECT DEBIT", "", "ignore", [])
            add_bank(d, -7000, "XERO AUSTRALIA", "DIRECT DEBIT", "", "ignore", [])
        if d.day == 28:
            add_bank(d, -1000, "MONTHLY ACCOUNT FEE", "FEE", "", "ignore", [])
        if d.month in (1, 4, 7, 10) and d.day == 26:
            add_bank(d, -rng.randint(800000, 1400000), "ATO", "BPAY", "BAS", "ignore", [])
        if rng.random() < 0.012:
            add_bank(d, rng.choice([500000, 1000000]), "TRANSFER FROM SAVINGS", "INTERNAL TRANSFER", "", "other", [])
        if rng.random() < 0.006:
            add_bank(d, rng.randint(3000, 25000), "REECE PTY LTD", "DIRECT CREDIT", "REFUND", "other", [])
        d += timedelta(days=1)

    # ---- write ----
    bank = [b for b in bank if b["Date"] <= END]
    bank.sort(key=lambda b: (b["Date"], b["Payee"]))
    rows, truth = [], []
    for i, b in enumerate(bank, 1):
        tid = f"BT-{i:06d}"
        rows.append(dict(TransactionID=tid, Date=b["Date"].isoformat(), Amount=f"{b['AmountCents'] / 100:.2f}",
                         Payee=b["Payee"], Description=b["Description"], Reference=b["Reference"]))
        truth.append(dict(TransactionID=tid, Kind=b["_kind"], Items="|".join(b["_items"])))

    pd.DataFrame(rows).to_csv(OUT / "bank_transactions.csv", index=False)
    pd.DataFrame(truth).to_csv(OUT / "truth.csv", index=False)
    inv_df = pd.DataFrame([{k: v for k, v in i.items() if not k.startswith("_")} for i in invoices])
    inv_df["Total"] = (inv_df.pop("TotalCents") / 100).map(lambda x: f"{x:.2f}")
    inv_df = inv_df[["InvoiceNumber", "InvoiceType", "JobNumber", "CustomerName", "SiteAddress", "Suburb",
                     "JobDescription", "CompletedDate", "InvoiceDate", "Total"]].sort_values("InvoiceDate")
    inv_df.to_csv(OUT / "jobs_invoices.csv", index=False)
    mat_df = pd.DataFrame(materials)
    mat_df["CostIncGST"] = (mat_df.pop("CostCents") / 100).map(lambda x: f"{x:.2f}")
    mat_df.sort_values(["PurchaseDate", "LineID"]).to_csv(OUT / "materials.csv", index=False)

    print(f"jobs={len(jobs)} invoices={len(inv_df)} material_lines={len(mat_df)} bank_lines={len(rows)}")


if __name__ == "__main__":
    main()
