"""Execution-accuracy comparison: does the predicted query return the same data as the reference?

Comparing SQL text is useless (many queries are equivalent), so we compare results:
- rows are compared as a multiset (order ignored) and floats are rounded;
- extra columns in the prediction are allowed, as long as every reference
  column can be matched to a predicted column with the same values.
"""

from __future__ import annotations

from collections import Counter
from itertools import permutations


def _norm(v):
    if isinstance(v, float):
        v = round(v, 2)
        return int(v) if v.is_integer() else v
    if isinstance(v, str):
        return v.strip()
    return v


def results_match(gold: list[tuple], pred: list[tuple]) -> bool:
    if len(gold) != len(pred):
        return False
    if not gold:
        return True
    gold = [tuple(_norm(v) for v in r) for r in gold]
    pred = [tuple(_norm(v) for v in r) for r in pred]
    g_cols, p_cols = len(gold[0]), len(pred[0])
    if p_cols < g_cols:
        return False

    gold_rows = Counter(gold)
    # Candidate predicted columns for each gold column: same multiset of values.
    col_vals = lambda rows, i: Counter(r[i] for r in rows)
    candidates = [
        [j for j in range(p_cols) if col_vals(pred, j) == col_vals(gold, i)] for i in range(g_cols)
    ]
    if any(not c for c in candidates):
        return False
    # Try each consistent assignment of predicted columns (tiny search in practice).
    for mapping in permutations(range(p_cols), g_cols):
        if all(mapping[i] in candidates[i] for i in range(g_cols)):
            if Counter(tuple(r[j] for j in mapping) for r in pred) == gold_rows:
                return True
    return False
