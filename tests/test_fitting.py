# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import copy

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from idslab.adapters import ClassScores
from idslab.config import from_dict
from idslab.data import load_table, make_splits
from idslab.features import FeatureStore, column_names, fold_ids
from idslab.fitting import FitError, calibration_rows, fit_candidate, oof_scores, training_rows


@pytest.fixture
def setup(data_dir, r1):
    cfg = from_dict(r1, base_dir=str(data_dir))
    table = load_table(cfg)
    split = make_splits(table, cfg.protocol, None, seed=0)
    return cfg, table, split, FeatureStore(table.X)


def test_rows_follow_the_split_rules(setup):
    cfg, table, split, store = setup
    binary, router, gate = (cfg.stages[s] for s in ("binary", "router", "gate"))
    np.testing.assert_array_equal(training_rows(binary, table, split, store), split.fit_base)
    np.testing.assert_array_equal(calibration_rows(binary, table, split, store), split.cal)
    np.testing.assert_array_equal(training_rows(router, table, split, store), split.fit)
    g = training_rows(gate, table, split, store)
    assert set(g.tolist()) <= set(split.fit.tolist())
    assert (table.y[g] == 0).all()
    for rows in (training_rows(binary, table, split, store), training_rows(router, table, split, store), g):
        assert not set(rows.tolist()) & set(split.val.tolist())
        assert not set(rows.tolist()) & set(split.test.tolist())


def test_calibrated_classifier_scores_are_probabilities(setup):
    cfg, table, split, store = setup
    f = fit_candidate(cfg.stages["binary"], 0, table, split, store, seed=0, n_jobs=1)
    s = f.score(table.X.iloc[split.val])
    assert isinstance(s, np.ndarray)
    assert s.shape == (len(split.val),)
    assert 0 <= s.min() <= s.max() <= 1
    assert f.n_cal == len(split.cal)
    assert f.attack_share == pytest.approx(table.y[split.fit_base].mean())


def test_search_stays_inside_training_rows(setup, r1, data_dir):
    r1["stages"]["router"]["candidates"][0]["search"] = {"space": {"max_depth": [2, 4]}, "cv": 3}
    cfg = from_dict(r1, base_dir=str(data_dir))
    _, table, split, store = setup
    f = fit_candidate(cfg.stages["router"], 0, table, split, store, seed=0, n_jobs=1)
    assert f.best_params["max_depth"] in (2, 4)
    assert f.cv_score is not None
    assert 0.5 < f.cv_score <= 1
    assert f.n_train == len(split.fit)


def test_detector_scores_attacks_higher(setup):
    cfg, table, split, store = setup
    f = fit_candidate(cfg.stages["gate"], 0, table, split, store, seed=0, n_jobs=1)
    s = f.score(table.X.iloc[split.val])
    assert isinstance(s, np.ndarray)
    yv = table.y[split.val]
    assert s[yv == 1].mean() > s[yv == 0].mean()


def test_train_on_types_filters_rows(setup, r1, data_dir):
    r1["stages"]["router"]["train_on"] = ["Normal", "DoS"]
    cfg = from_dict(r1, base_dir=str(data_dir))
    _, table, split, store = setup
    rows = training_rows(cfg.stages["router"], table, split, store)
    assert table.types is not None
    assert set(np.unique(table.types[rows])) == {"Normal", "DoS"}
    r1["stages"]["router"]["train_on"] = ["Normal", "Unicorn"]
    cfg = from_dict(r1, base_dir=str(data_dir))
    with pytest.raises(FitError, match="one class"):
        fit_candidate(cfg.stages["router"], 0, table, split, store, seed=0, n_jobs=1)


def test_fold_ids_are_stratified_and_cover_rows():
    labels = np.array(["a"] * 30 + ["b"] * 12 + ["c"] * 2)
    f = fold_ids(labels, 3, seed=0)
    assert set(f.tolist()) == {0, 1, 2}
    for k in range(3):
        assert (labels[f == k] == "b").sum() == 4
        # "c" has fewer rows than folds, so it joins the largest stratum: 32 rows over 3 folds
        assert np.isin(labels[f == k], ["a", "c"]).sum() in (10, 11)


def test_out_of_fold_scores_never_come_from_a_model_that_saw_the_row(setup, r1, data_dir):
    # a 1-nearest-neighbour model scores its own training rows perfectly; out-of-fold it cannot
    r1["stages"]["router"]["candidates"] = [
        {"class": "sklearn.neighbors.KNeighborsClassifier", "params": {"n_neighbors": 1}}
    ]
    cfg = from_dict(r1, base_dir=str(data_dir))
    _, table, split, store = setup
    st = cfg.stages["router"]
    f = fit_candidate(st, 0, table, split, store, seed=0, n_jobs=1)
    y = table.y[split.fit]
    in_sample = f.raw_score(table.X.iloc[split.fit])[:, 0]
    assert roc_auc_score(y, in_sample) == pytest.approx(1.0)
    oof = oof_scores(st, f, table, split, store, folds=3, seed=0, n_jobs=1)[:, 0]
    assert not np.isnan(oof).any()
    assert roc_auc_score(y, oof) < 0.97


def test_hard_rows_come_from_the_source_band(setup, r1, data_dir):
    raw = copy.deepcopy(r1)
    raw["stages"]["router"]["train_on"] = "hard"
    raw["stages"]["router"]["hard"] = {"source": "binary", "band": [0.2, 0.8]}
    cfg = from_dict(raw, base_dir=str(data_dir))
    _, table, split, store = setup
    src = cfg.stages["binary"]
    f = fit_candidate(src, 0, table, split, store, seed=0, n_jobs=1)
    store.declare("binary", column_names("binary", None))
    store.put("binary", split.fit, oof_scores(src, f, table, split, store, folds=3, seed=0, n_jobs=1))
    rows = training_rows(cfg.stages["router"], table, split, store)
    s = store.scalar("binary", rows)
    assert len(rows) > 0
    assert ((s >= 0.2) & (s <= 0.8)).all()
    assert len(rows) < len(split.fit)


def test_multiclass_and_pool_stages_fit(setup, r1, data_dir):
    raw = copy.deepcopy(r1)
    raw["stages"]["router"]["routes"] = {"attack": "type", "benign": "gate"}
    raw["stages"]["type"] = {
        "kind": "multiclass",
        "train_on": "attack",
        "pipeline": raw["stages"]["router"]["pipeline"],
        "candidates": [{"class": "sklearn.ensemble.RandomForestClassifier", "params": {"n_estimators": 20}}],
        "decision": {"confidence": 0.0},
        "next": "ATTACK",
    }
    raw["stages"]["gate"] = {
        "kind": "detector",
        "fit_on": "pool",
        "pipeline": raw["stages"]["gate"]["pipeline"],
        "candidates": [{"class": "sklearn.neighbors.LocalOutlierFactor", "params": {"n_neighbors": 10}}],
        "decision": {"top_fraction": 0.05, "off": "normal"},
        "routes": {"anomalous": "ATTACK", "normal": "BENIGN"},
    }
    cfg = from_dict(raw, base_dir=str(data_dir))
    _, table, split, store = setup
    t = fit_candidate(cfg.stages["type"], 0, table, split, store, seed=0, n_jobs=1)
    cs = t.score(table.X.iloc[split.val])
    assert isinstance(cs, ClassScores)
    assert "Normal" not in cs.classes  # trained on attack rows only
    assert cs.proba.shape == (len(split.val), len(cs.classes))
    g = fit_candidate(cfg.stages["gate"], 0, table, split, store, seed=0, n_jobs=1)
    pool = table.X.iloc[split.val[:200]]
    s = g.pool_score(pool)
    assert s.shape == (200,)
    with pytest.raises(FitError, match="scores batches"):
        g.score(pool)
