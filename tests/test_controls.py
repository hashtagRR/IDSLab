# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import math

import numpy as np
import pytest

from idslab.config import from_dict
from idslab.controls import best_cutoff, budget_matched_threshold, evaluate
from idslab.graph import OFF, StageState
from idslab.selection import Selection


def test_budget_match_takes_highest_threshold_reaching_target():
    p = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.95])
    y = np.array([0, 0, 0, 0, 0, 1])
    t, achieved = budget_matched_threshold(p, y, 0.4)
    assert t == pytest.approx(0.8)
    assert achieved == pytest.approx(0.4)
    t, achieved = budget_matched_threshold(p, y, 0.3)
    assert t == pytest.approx(0.8)
    assert achieved >= 0.3
    t, achieved = budget_matched_threshold(p, y, 0.0)
    assert math.isinf(t)
    assert achieved == 0.0


def test_best_cutoff_prefers_first_on_ties():
    p = np.array([0.2, 0.4, 0.6, 0.8])
    y = np.array([0, 0, 1, 1])
    assert best_cutoff(p, y, (0.5, 0.55, 0.9), "accuracy") == 0.5


def fixture_data(seed=0, n=600):
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.35).astype(int)
    p1 = np.clip(0.6 * y + rng.normal(0.2, 0.2, n), 0, 1)
    router = np.where(y == 1, 0.9, rng.random(n))
    gate = rng.normal(size=n) + 2.5 * y
    return y, {"binary": [p1, p1], "router": [router], "gate": [gate]}


def on(t, c=0):
    return StageState(off=False, candidate=c, threshold=t, value=t)


def run_eval(r1, state):
    cfg = from_dict(r1)
    y_val, sv = fixture_data(0)
    y_test, st = fixture_data(1)
    sel = Selection(order=cfg.order(), state=state, trace=[])
    summary, preds, _ = evaluate(
        cfg,
        sel,
        val_scores=sv,
        test_scores=st,
        y_val=y_val,
        y_test=y_test,
        shares={"FIT": 0.35, "VAL": float(y_val.mean())},
        first_train_share=0.35,
        seed=0,
    )
    return summary, preds


def test_b1_equals_cascade_when_later_stages_are_off(r1):
    summary, preds = run_eval(r1, {"binary": on(0.5), "router": OFF, "gate": OFF})
    # router off -> attack, so the cascade is the first stage at its threshold
    np.testing.assert_array_equal(preds["cascade"], preds["B1_first_stage"])
    # a single threshold of the first stage never beats its own ROC envelope
    assert summary["frontier_gap_test"]["gap_step"] <= 1e-12
    assert summary["frontier_gap_test"]["gap_interp"] <= 1e-12
    assert set(summary["stage_removal_test"]) == {"binary"}


def test_prefix_steps_add_up_and_removal_is_reported(r1):
    summary, _ = run_eval(r1, {"binary": on(0.5), "router": on(0.8), "gate": on(1.5)})
    prefix = summary["prefix_test"]
    assert len(prefix) == 4
    total = sum(p["step_accuracy"] for p in prefix[1:])
    assert total == pytest.approx(prefix[-1]["accuracy"] - prefix[0]["accuracy"])
    assert prefix[-1]["accuracy"] == pytest.approx(summary["systems_test"]["cascade"]["accuracy"])
    assert set(summary["stage_removal_test"]) == {"binary", "router", "gate"}
    for name in ("B1_first_stage", "B2_first_stage_val_cutoff", "budget_matched_first_stage"):
        assert summary["bootstrap_cascade_minus"][name]["accuracy"]["ci95"] is not None
    bm = summary["budget_matched"]
    assert bm["achieved_val_fpr"] >= bm["target_val_fpr"] - 1e-12
    assert summary["prevalence"]["em_estimate_TEST"] is not None
