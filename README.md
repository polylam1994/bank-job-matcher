<img width="891" height="437" alt="image" src="https://github.com/user-attachments/assets/3748aaf8-5d04-4655-8376-5f1f91e31e4b" />
<img width="878" height="794" alt="image" src="https://github.com/user-attachments/assets/1cacf263-021a-4f12-92d3-37773ccfe708" />
<img width="864" height="770" alt="image" src="https://github.com/user-attachments/assets/37117b4e-a3c1-4721-b218-9a3378dba188" />
<img width="870" height="689" alt="image" src="https://github.com/user-attachments/assets/56db0728-f69d-45b6-9e54-8e51b95efd6e" />
<img width="879" height="663" alt="image" src="https://github.com/user-attachments/assets/5c6984ae-c846-4d82-8c8d-cb12360bb303" />





# Bank-to-Job Matcher

A daily scheduled workflow that reconciles a trades business's bank feed against its jobs, invoices and supplier materials when the bank lines carry **no invoice or job numbers**.

Each morning a Claude scheduled task:

1. reads what the office confirmed or rejected on a live review page,
2. pulls the new bank lines, invoices and job materials,
3. matches them with **Fellegi-Sunter probabilistic record linkage** (Splink), **one-to-one assignment** (SciPy) and **subset-sum** for one payment covering several items,
4. writes a short note on each line a person needs to look at,
5. updates the review page: auto-matched lines, lines needing review with the top candidates and *why*, and running costs set aside.

Everything runs on a **fictional** Sydney plumbing business ("Harbourside Plumbing & Gas"). In a client engagement, the sample feed is replaced by the ServiceM8 / simPRO and Xero connectors.

## The problem

| What the bank shows | What it actually pays |
|---|---|
| `+1,980.00  J & M SMITH  ref: BONDI` | INV-2231, John & Mary Smith, hot water system, Bondi |
| `+630.00  COASTLINE PROP MGMT  ref: MANLY` | INV-2355, a tenant's gas repair, paid by the property manager |
| `−1,237.42  REECE TRADE ACC  ref: STATEMENT` | 11 Reece material lines across 6 jobs, one fortnightly statement |
| `−76.25  BUNNINGS 742000 ALEXANDRIA` | 2 PVC pipe lines bought for job J-1310 that morning |
| `+360.00  SOPHIE TRAN  ref: PLUMBER` | The first part of a $595.00 invoice |

## How it matches

**Money in → open invoices: Fellegi-Sunter.** Every (payment, invoice) pair inside a 45-day window is compared on four signals:

| Comparison | Levels |
|---|---|
| Amount | exact (or equals the balance still owed) · within $2 · smaller, could be a part payment · different |
| Name | exact · 2+ name words shared · Jaro-Winkler ≥ 0.9 · 1 word shared · different |
| Reference | 2+ words match the site address / job · 1 word · none |
| Date | within 7 · 21 · 45 days |

Each level adds `log2(m/u)` bits of evidence. **m** (how often the level occurs for true matches) is estimated from the office's past reconciliations. With no history yet, it falls back to expectation maximisation. **u** (how often it occurs by chance) is estimated by random sampling. The prior is 1 / number of open invoices. The page shows this breakdown for every candidate.

Then:
- **One invoice, one payment:** `scipy.optimize.linear_sum_assignment` on the match weights, so two payments never claim the same invoice.
- **Auto-match rule:** probability ≥ 95%, amount exact or within tolerance, and at least 3 bits clear of both the runner-up invoice and any competing payment. Everything else goes to review.
- **Instalments:** part payments from the same payer that together add up to what is still owed (subset-sum, at most 3 payments).

**Money out → material lines: supplier first, then subset-sum.** The supplier comes from payee aliases (or Splink name similarity). How each supplier bills is set in `config/settings.json`, because it changes which search makes sense:

| Billing (from discovery) | Search, strongest first |
|---|---|
| `statement` (Reece, Tradelink) | contiguous statement period of lines in date order, O(n²) |
| `card` (Bunnings) | pruned subset-sum over lines within ±2 days |
| `invoice` (Rheem) | single line with the exact amount |
| any, as a last resort | wide subset-sum over the 45-day pool, **never auto-matched** |

The last rule exists because exact-cent coincidences are common. In testing, 24 open Reece lines held **21 different combinations** adding up to exactly $1,237.42, and only one was the real statement.

The subset-sum (`bjm/subset_sum.py`) works in integer cents, sorts items largest first, and prunes on two checks: "already over the target and only positives remain" and "even taking every remaining item can't reach it". It handles supplier credit notes, enumerates every solution up to a cap, and reports when its node budget runs out. A search that stopped early is never auto-matched.

## Results on the sample data

Scored against the generator's hidden answer key (`python -m bjm.evaluate`):

| Scenario | Auto-matched correct | Review: right answer ranked first | Missed |
|---|---|---|---|
| Six months unreconciled (Apr–Sep 2026), m from 515 past pairs | 252 / 252 | 25 / 25 | 0 |
| Cold start: no history, m by EM (Oct 2025–Jan 2026) | 123 / 123 | 43 / 44 | 6 left for a person |
| Daily run, 1 Oct 2026 | 37 / 37 | 2 / 2 | 0 |

The data generator and the matcher were written together, so these numbers show the method works, not how it will score on a real client's data. Real bank feeds are messier: card-terminal payouts net of fees, customers paying three invoices at once, and so on. That is why every uncertain line goes to a person, and why their decisions feed back into the m estimates.

## Run it

```bash
py -m venv C:\Users\<you>\AppData\Local\bjm-venv     # keep the venv on a short path (DuckDB + long Windows paths)
<venv>\Scripts\python -m pip install -r requirements.txt
python data/generate_sample.py          # sample timeline, Oct 2025 – Mar 2027 (already committed)
python -m bjm.state                     # mark everything up to 31 Aug 2026 as reconciled (the office's history)
python -m bjm.feed --as-of 2026-10-01   # release the exports as they'd look that morning
python -m bjm.run                       # match -> output/results.json
python -m bjm.evaluate                  # score against the answer key
python -m pytest -q tests
```

Backtest any period: `python -m bjm.state --cutoff 2026-03-31` then `python -m bjm.feed --as-of 2026-09-30`.

## Layout

| Path | What it is |
|---|---|
| `data/generate_sample.py` | Builds the fictional business: 462 jobs, 500 invoices, 1,008 material lines, 1,208 bank lines, plus `truth.csv` |
| `bjm/feed.py` | Simulates the daily ServiceM8 / Xero / bank exports into `inbox/` |
| `bjm/linkage.py` | Splink models (comparisons, training, prediction, per-factor weights) |
| `bjm/subset_sum.py` | Statement windows and pruned subset-sum |
| `bjm/run.py` | The daily pipeline: assignment, instalments, supplier matching, grouping |
| `bjm/state.py` | What is already reconciled, ignored or rejected |
| `bjm/sync.py` | Pull decisions from / push results to the review page's database |
| `bjm/evaluate.py` | Accuracy against the sample answer key |
| `config/settings.json` | Windows, tolerances, thresholds, supplier billing types, aliases, abbreviations |
| `page/index.html` | The review page (a Claude artifact with a shared database) |
| `scheduled-task/SKILL.md` | The Claude scheduled-task prompt that runs it every weekday morning |

## Built with

Claude Code in the Claude desktop app: the Python pipeline, a scheduled task, and a live artifact page whose shared database carries the office's decisions back into the next morning's run.
