"""Fellegi-Sunter probabilistic linkage with Splink (DuckDB backend).

Each pair (bank line, open item) gets a match weight = sum over comparisons of
log2(m/u), plus the prior log2(lambda / (1 - lambda)):
  m = P(this level | true match)       - estimated from past reconciliations (labels),
                                         or by EM when there is no history yet
  u = P(this level | not a match)      - estimated by random sampling
  lambda = prior chance a random pair matches, set to 1 / number of open items

Two models share this module:
  customer  - money in   vs open invoices        (amount, name, reference, date)
  supplier  - money out  vs open material lines  (amount, supplier name, date)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd
import splink.comparison_level_library as cll
import splink.comparison_library as cl
from splink import Linker, SettingsCreator, block_on
from splink.backends.duckdb import DuckDBAPI

logging.getLogger("splink").setLevel(logging.ERROR)


@dataclass
class Level:
    label: str
    sql: str | None = None  # None for the else level


def date_levels(windows: list[int]) -> list[Level]:
    lv = [Level(f"within {w} days", f"abs(date_diff('day', txn_date_l, txn_date_r)) <= {w}") for w in windows]
    return lv + [Level(f"more than {windows[-1]} days apart")]


def customer_comparisons(tol: int) -> dict[str, list[Level]]:
    exact = ("(amount_cents_l = amount_cents_r OR amount_cents_l = amount_alt_cents_r "
             "OR amount_alt_cents_l = amount_cents_r)")
    return {
        "amount": [
            Level("exact", exact),
            Level(f"within ${tol / 100:.2f}", f"abs(amount_cents_l - amount_cents_r) <= {tol}"),
            Level("smaller - could be a part payment",
                  "least(amount_cents_l, amount_cents_r) >= 0.1 * greatest(amount_cents_l, amount_cents_r)"),
            Level("different"),
        ],
        "name": [
            Level("exact", "name_key_l = name_key_r"),
            Level("2+ name words shared", "len(list_intersect(name_tokens_l, name_tokens_r)) >= 2"),
            Level("very similar (Jaro-Winkler >= 0.9)", "jaro_winkler_similarity(name_key_l, name_key_r) >= 0.9"),
            Level("1 name word shared", "len(list_intersect(name_tokens_l, name_tokens_r)) >= 1"),
            Level("different"),
        ],
        "reference": [
            Level("2+ words match address/job", "len(list_intersect(ref_tokens_l, ref_tokens_r)) >= 2"),
            Level("1 word matches address/job", "len(list_intersect(ref_tokens_l, ref_tokens_r)) >= 1"),
            Level("no overlap"),
        ],
        "date": date_levels([7, 21, 45]),
    }


def supplier_comparisons() -> dict[str, list[Level]]:
    return {
        "amount": [Level("exact", "amount_cents_l = amount_cents_r"), Level("different")],
        "name": [
            Level("same supplier", "name_key_l = name_key_r"),
            Level("similar supplier name", "jaro_winkler_similarity(name_key_l, name_key_r) >= 0.88"),
            Level("different supplier"),
        ],
        "date": date_levels([2, 14, 45]),
    }


# columns that may be empty and should be treated as "no information" rather than disagreement
NULLABLE = {"name": "name_key", "reference": "ref_tokens"}


def _comparison(name: str, levels: list[Level]):
    built = []
    if name in NULLABLE:
        col = NULLABLE[name]
        cond = (f"{col}_l IS NULL OR {col}_r IS NULL OR len({col}_l) = 0 OR len({col}_r) = 0")
        built.append(cll.CustomLevel(cond, "missing").configure(is_null_level=True))
    for lv in levels:
        built.append(cll.ElseLevel() if lv.sql is None else cll.CustomLevel(lv.sql, lv.label))
    return cl.CustomComparison(built, output_column_name=name, comparison_description=name)


def gamma_label(levels: list[Level], gamma: int) -> str:
    """Splink numbers levels from the else level (0) upwards; -1 is the null level."""
    if gamma == -1:
        return "not available"
    return levels[len(levels) - 1 - gamma].label


class Model:
    def __init__(self, side: str, comparisons: dict[str, list[Level]], window_days: int):
        self.side = side
        self.comparisons = comparisons
        self.window_days = window_days
        self.trained: dict | None = None
        self.training_method = ""

    def _settings(self, prior: float) -> SettingsCreator:
        return SettingsCreator(
            link_type="link_only",
            comparisons=[_comparison(k, v) for k, v in self.comparisons.items()],
            blocking_rules_to_generate_predictions=[
                f"abs(date_diff('day', l.txn_date, r.txn_date)) <= {self.window_days}"],
            probability_two_random_records_match=prior,
            retain_intermediate_calculation_columns=True,
            additional_columns_to_retain=["label"],
        )

    @staticmethod
    def _register(db: DuckDBAPI, bank: pd.DataFrame, items: pd.DataFrame):
        return [db.register(bank, dataset_display_name="bank"), db.register(items, dataset_display_name="items")]

    def train(self, bank: pd.DataFrame, items: pd.DataFrame, labels: pd.DataFrame) -> None:
        """u by random sampling; m from labelled history if there is enough, else EM."""
        db = DuckDBAPI()
        prior = 1 / max(len(items), 1)
        linker = Linker(self._register(db, bank, items), self._settings(prior), log_level=logging.ERROR)
        linker.training.estimate_u_using_random_sampling(max_pairs=2e6, seed=1)
        if len(labels) >= 30:
            lab = labels.assign(source_dataset_l="bank", source_dataset_r="items")
            linker.training.estimate_m_from_pairwise_labels(db.register(lab, dataset_display_name="labels"))
            self.training_method = f"m from {len(labels)} reconciled pairs, u from random sampling"
        else:
            for rule in (block_on("amount_cents"), block_on("name_key")):
                linker.training.estimate_parameters_using_expectation_maximisation(rule)
            self.training_method = "m by expectation maximisation, u from random sampling"
        self.trained = self._smooth(linker.misc.save_model_to_json(), max(len(labels), 1))

    @staticmethod
    def _smooth(model: dict, n_labels: int) -> dict:
        """Levels never seen in training get a small probability instead of Splink's default.

        e.g. if no reconciled pair was ever more than 45 days apart, m for that level is
        (0.5 / (n+1)) - rare, but not impossible (Laplace-style smoothing).
        """
        for comp in model["comparisons"]:
            for lv in comp["comparison_levels"]:
                if lv.get("is_null_level"):
                    continue
                if lv.get("m_probability") is None:
                    lv["m_probability"] = 0.5 / (n_labels + 1)
                if lv.get("u_probability") is None:
                    lv["u_probability"] = 1e-4
        return model

    def predict(self, bank: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
        """Score every (bank line, item) pair inside the date window."""
        if bank.empty or items.empty:
            return pd.DataFrame()
        model = dict(self.trained)
        model["probability_two_random_records_match"] = 1 / len(items)
        db = DuckDBAPI()
        linker = Linker(self._register(db, bank, items), model, log_level=logging.ERROR)
        df = linker.inference.predict().as_pandas_dataframe()
        # orient every row as bank (left) -> item (right)
        flip = df["source_dataset_l"] != "bank"
        swap = {}
        for c in df.columns:
            if c.endswith("_l") and c[:-2] + "_r" in df.columns:
                swap[c], swap[c[:-2] + "_r"] = c[:-2] + "_r", c
        df = pd.concat([df[~flip], df[flip].rename(columns=swap)], ignore_index=True)
        return df.rename(columns={"unique_id_l": "bank_id", "unique_id_r": "item_id"})

    def level_weight(self, name: str, gamma: int) -> float:
        """Fellegi-Sunter weight log2(m/u) for one comparison level (0 for missing data)."""
        if gamma == -1:
            return 0.0
        comp = next(c for c in self.trained["comparisons"] if c["output_column_name"] == name)
        levels = [lv for lv in comp["comparison_levels"] if not lv.get("is_null_level")]
        lv = levels[len(levels) - 1 - gamma]
        return math.log2(lv["m_probability"] / lv["u_probability"])

    def reasons(self, row) -> list[dict]:
        out = []
        for name, levels in self.comparisons.items():
            g = int(row[f"gamma_{name}"])
            out.append({"factor": name, "detail": gamma_label(levels, g),
                        "weight": round(self.level_weight(name, g), 2)})
        return out

    def parameters(self) -> list[dict]:
        """m, u and weight for each level, for the 'how it works' panel."""
        rows = []
        for comp in self.trained["comparisons"]:
            for lv in comp["comparison_levels"]:
                if lv.get("is_null_level"):
                    continue
                m, u = lv.get("m_probability"), lv.get("u_probability")
                w = math.log2(m / u) if m and u else None
                rows.append({"side": self.side, "comparison": comp["output_column_name"],
                             "level": lv.get("label_for_charts", ""), "m": m, "u": u,
                             "weight": round(w, 2) if w is not None else None})
        return rows
