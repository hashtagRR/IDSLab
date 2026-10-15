# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Controls computed after the configuration is frozen (DESIGN.md section 6).

Everything here takes the frozen selection as given; nothing feeds back into it.
All controls use the same fitted first-stage model as the cascade.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from idslab.config import Config
from idslab.graph import Routing, route, with_off
from idslab.metrics import binary_metrics, em_prior, frontier_ci, frontier_gap, objective, paired_bootstrap
from idslab.selection import Scores, Selection, active_scores


def budget_matched_threshold(p_val: np.ndarray, y_val: np.ndarray, target_fpr: float) -> tuple[float, float]:
    """Highest threshold whose VAL FPR is at least ``target_fpr``; returns (threshold, achieved FPR)."""
    benign = np.sort(p_val[y_val == 0])[::-1]
    if len(benign) == 0:
        raise ValueError("no benign VAL rows to match a false-alarm budget")
    k = math.ceil(target_fpr * len(benign) - 1e-12)
    if k <= 0:
        return math.inf, 0.0
    t = float(benign[min(k, len(benign)) - 1])
    return t, float((benign >= t).mean())


def best_cutoff(p_val: np.ndarray, y_val: np.ndarray, grid: tuple[float, ...], goal: str) -> float:
    values = [objective(y_val, (p_val >= c).astype(int), goal) for c in grid]
    return float(grid[int(np.argmax(values))])


def evaluate(
    cfg: Config,
    sel: Selection,
    *,
    val_scores: Scores,
    test_scores: Scores,
    y_val: np.ndarray,
    y_test: np.ndarray,
    shares: dict[str, float],
    first_train_share: float,
    seed: int,
) -> tuple[dict, dict[str, np.ndarray], Routing]:
    """TEST evaluation of the frozen cascade and every control.

    Returns the summary block, the per-flow predictions of each system and the TEST routing.
    """
    goal = cfg.protocol.objective
    order, state = sel.order, sel.state
    n_val, n_test = len(y_val), len(y_test)
    first = cfg.controls.first_stage
    fs = state[first]
    if fs.off or fs.candidate is None or fs.threshold is None:
        raise RuntimeError("the control first stage must be active in the frozen configuration")
    p1_val, p1_test = val_scores[first][fs.candidate], test_scores[first][fs.candidate]

    def run(st, scores, n):
        return route(order, cfg.stages, active_scores(scores, st), st, n)

    cascade_val = run(state, val_scores, n_val).verdict
    routing = run(state, test_scores, n_test)
    cascade = routing.verdict

    preds = {"cascade": cascade, "B1_first_stage": (p1_test >= fs.threshold).astype(np.int8)}
    b2_cut = best_cutoff(p1_val, y_val, cfg.controls.cutoff_grid, goal)
    preds["B2_first_stage_val_cutoff"] = (p1_test >= b2_cut).astype(np.int8)
    target = binary_metrics(y_val, cascade_val)["fpr"]
    t_budget, achieved = budget_matched_threshold(p1_val, y_val, target)
    preds["budget_matched_first_stage"] = (p1_test >= t_budget).astype(np.int8)

    systems = {name: binary_metrics(y_test, p) for name, p in preds.items()}
    frontier = frontier_gap(y_test, p1_test, cascade)
    frontier["ci95_interp"] = (
        frontier_ci(y_test, p1_test, cascade, cfg.controls.frontier_bootstrap, seed)
        if frontier["defined"]
        else None
    )
    boot = {
        name: paired_bootstrap(y_test, cascade, preds[name], n=cfg.controls.bootstrap, seed=seed + i)
        for i, name in enumerate(preds)
        if name != "cascade"
    }

    removal = {}
    for s in order:
        if state[s].off:
            continue
        p = run(with_off(state, [s]), test_scores, n_test).verdict
        m = binary_metrics(y_test, p)
        removal[s] = {"metrics": m, f"cascade_minus_removed_{goal}": systems["cascade"][goal] - m[goal]}

    prefix = []
    for k in range(len(order) + 1):
        st_k = {s: (state[s] if i < k else replace(state[s], off=True)) for i, s in enumerate(order)}
        m = binary_metrics(y_test, run(st_k, test_scores, n_test).verdict)
        prefix.append({"stages_on": [s for s in order[:k] if not state[s].off], goal: m[goal], "metrics": m})
    for k in range(1, len(prefix)):
        prefix[k][f"step_{goal}"] = prefix[k][goal] - prefix[k - 1][goal]

    calibrated = cfg.stages[first].calibrate != "none"
    prevalence = {
        "attack_share": {**shares, "TEST": float(np.mean(y_test))},
        "em_estimate_TEST": em_prior(p1_test, first_train_share) if calibrated else None,
        "note": "EM assumes label shift only"
        if calibrated
        else "EM estimate needs a calibrated first stage; not computed",
    }
    summary = {
        "objective": goal,
        "first_stage": first,
        "systems_test": systems,
        "cascade_val": binary_metrics(y_val, cascade_val),
        "b1_threshold": fs.threshold,
        "b2_val_cutoff": b2_cut,
        "budget_matched": {
            "target_val_fpr": target,
            "achieved_val_fpr": achieved,
            "threshold": None if math.isinf(t_budget) else t_budget,
        },
        "frontier_gap_test": frontier,
        "bootstrap_cascade_minus": boot,
        "stage_removal_test": removal,
        "prefix_test": prefix,
        "prevalence": prevalence,
    }
    return summary, preds, routing
