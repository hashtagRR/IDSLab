# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import numpy as np
import pytest
from sklearn import metrics as skm

from idslab.metrics import (
    _SortedROC,
    binary_metrics,
    em_prior,
    first_stage_tpr_at,
    frontier_ci,
    frontier_gap,
    paired_bootstrap,
)


def sample(n=500, seed=0):
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.3).astype(int)
    score = np.clip(0.35 * y + rng.normal(0.4, 0.2, n), 0, 1)
    return y, score


def test_metrics_match_sklearn():
    y, s = sample()
    pred = (s >= 0.5).astype(int)
    m = binary_metrics(y, pred)
    assert m["accuracy"] == pytest.approx(skm.accuracy_score(y, pred))
    assert m["precision"] == pytest.approx(skm.precision_score(y, pred))
    assert m["recall"] == pytest.approx(skm.recall_score(y, pred))
    assert m["f1"] == pytest.approx(skm.f1_score(y, pred))
    assert m["mcc"] == pytest.approx(skm.matthews_corrcoef(y, pred))
    assert m["balanced_accuracy"] == pytest.approx(skm.balanced_accuracy_score(y, pred))
    tn, fp, fn, tp = skm.confusion_matrix(y, pred).ravel()
    assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (tp, fp, tn, fn)
    assert m["fpr"] == pytest.approx(fp / (fp + tn))


def test_degenerate_counts_give_zero_not_nan():
    m = binary_metrics(np.zeros(5, int), np.zeros(5, int))
    assert m["precision"] == 0.0
    assert m["mcc"] == 0.0
    assert m["accuracy"] == 1.0


def test_threshold_of_first_stage_never_beats_its_envelope():
    y, s = sample(seed=1)
    s = np.round(s, 2)  # many tied scores, so some thresholds sit below the envelope
    for t in np.unique(s):
        g = frontier_gap(y, s, (s >= t).astype(int))
        assert g["gap_step"] <= 1e-12
        assert g["gap_interp"] <= 1e-12


def test_better_operating_point_has_positive_gap():
    y, s = sample(seed=2)
    oracle = y.copy()
    oracle[np.flatnonzero(y == 0)[:3]] = 1
    g = frontier_gap(y, s, oracle)
    assert g["gap_interp"] > 0.2
    ci = frontier_ci(y, s, oracle, n=50, seed=0)
    assert ci is not None
    assert ci[0] > 0


def test_interpolation_and_step_on_known_curve():
    fpr = np.array([0.0, 0.0, 0.5, 0.5, 1.0])
    tpr = np.array([0.0, 0.4, 0.6, 0.9, 1.0])
    interp, step = first_stage_tpr_at(fpr, tpr, 0.25)
    assert interp == pytest.approx(0.65)
    assert step == pytest.approx(0.4)
    interp, step = first_stage_tpr_at(fpr, tpr, 0.5)
    assert interp == pytest.approx(0.9)
    assert step == pytest.approx(0.9)


def test_gap_undefined_without_both_classes():
    g = frontier_gap(np.ones(4, int), np.linspace(0, 1, 4), np.ones(4, int))
    assert g["defined"] is False


def test_identical_predictions_have_zero_interval():
    y, s = sample(seed=3)
    pred = (s >= 0.5).astype(int)
    out = paired_bootstrap(y, pred, pred, n=200, seed=0)
    assert out["accuracy"]["diff"] == 0
    assert out["accuracy"]["ci95"] == [0.0, 0.0]


def test_multinomial_bootstrap_matches_index_bootstrap():
    y, s = sample(n=2000, seed=4)
    a = (s >= 0.5).astype(int)
    b = (s >= 0.6).astype(int)
    fast = paired_bootstrap(y, a, b, n=4000, seed=1, names=("accuracy", "recall"))
    rng = np.random.default_rng(2)
    pos, neg = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    slow = []
    for _ in range(1000):
        idx = np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])
        slow.append((a[idx] == y[idx]).mean() - (b[idx] == y[idx]).mean())
    lo, hi = np.percentile(slow, [2.5, 97.5])
    assert fast["accuracy"]["ci95"][0] == pytest.approx(lo, abs=0.004)
    assert fast["accuracy"]["ci95"][1] == pytest.approx(hi, abs=0.004)
    assert fast["recall"]["diff"] == pytest.approx(a[y == 1].mean() - b[y == 1].mean())


def test_em_recovers_shifted_prior():
    rng = np.random.default_rng(5)
    n, train_prior, test_prior = 20000, 0.5, 0.15
    y = (rng.random(n) < test_prior).astype(int)
    x = rng.normal(loc=np.where(y == 1, 1.5, -1.5), scale=1.0)
    # exact posterior under the training prior for two unit-variance normals at +-1.5
    p_train = 1 / (1 + np.exp(-3.0 * x) * (1 - train_prior) / train_prior)
    assert em_prior(p_train, train_prior) == pytest.approx(test_prior, abs=0.01)


def test_sorted_roc_with_unit_weights_matches_frontier_gap():
    y, s = sample(n=700, seed=6)
    s = np.round(s, 2)  # ties
    pred = ((s >= 0.55) | (np.arange(len(y)) % 7 == 0)).astype(int)
    roc = _SortedROC(y, s, pred)
    assert roc.gap(np.ones(len(y))) == pytest.approx(frontier_gap(y, s, pred)["gap_interp"], abs=1e-12)


def test_weighted_bootstrap_matches_index_resampling():
    y, s = sample(n=600, seed=7)
    pred = ((s >= 0.5) | (y == 1) & (np.arange(len(y)) % 3 == 0)).astype(int)
    fast = frontier_ci(y, s, pred, n=1500, seed=0)
    rng = np.random.default_rng(1)
    pos, neg = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    slow = []
    for _ in range(600):
        idx = np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])
        slow.append(frontier_gap(y[idx], s[idx], pred[idx])["gap_interp"])
    lo, hi = np.percentile(slow, [2.5, 97.5])
    assert fast is not None
    assert fast[0] == pytest.approx(lo, abs=0.015)
    assert fast[1] == pytest.approx(hi, abs=0.015)


def test_interpolation_uses_the_convex_hull_not_adjacent_points():
    # ROC with a dip: (0,0) (0.1,0.6) (0.2,0.62) (0.3,0.9) (1,1)
    fpr = np.array([0.0, 0.1, 0.2, 0.3, 1.0])
    tpr = np.array([0.0, 0.6, 0.62, 0.9, 1.0])
    interp, step = first_stage_tpr_at(fpr, tpr, 0.2)
    # randomising between thresholds at 0.1 and 0.3 reaches (0.6 + 0.9) / 2 = 0.75 at FPR 0.2
    assert interp == pytest.approx(0.75)
    assert step == pytest.approx(0.62)
    # a cascade at (0.2, 0.7) beats the adjacent-point line (0.62) but not the hull (0.75)
    assert 0.7 - interp < 0
