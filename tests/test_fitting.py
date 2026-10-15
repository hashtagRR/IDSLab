# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import numpy as np
import pytest

from idslab.config import from_dict
from idslab.data import load_table, make_splits
from idslab.fitting import FitError, calibration_rows, fit_candidate, training_rows


@pytest.fixture
def setup(data_dir, r1):
    cfg = from_dict(r1, base_dir=str(data_dir))
    table = load_table(cfg)
    split = make_splits(table, cfg.protocol, None, seed=0)
    return cfg, table, split


def test_rows_follow_the_split_rules(setup):
    cfg, table, split = setup
    binary, router, gate = (cfg.stages[s] for s in ("binary", "router", "gate"))
    np.testing.assert_array_equal(training_rows(binary, table, split), split.fit_base)
    np.testing.assert_array_equal(calibration_rows(binary, table, split), split.cal)
    np.testing.assert_array_equal(training_rows(router, table, split), split.fit)
    g = training_rows(gate, table, split)
    assert set(g.tolist()) <= set(split.fit.tolist())
    assert (table.y[g] == 0).all()
    for rows in (training_rows(binary, table, split), training_rows(router, table, split), g):
        assert not set(rows.tolist()) & set(split.val.tolist())
        assert not set(rows.tolist()) & set(split.test.tolist())


def test_calibrated_classifier_scores_are_probabilities(setup):
    cfg, table, split = setup
    f = fit_candidate(cfg.stages["binary"], 0, table, split, seed=0, n_jobs=1)
    s = f.score(table.X.iloc[split.val])
    assert s.shape == (len(split.val),)
    assert 0 <= s.min() <= s.max() <= 1
    assert f.n_cal == len(split.cal)
    assert f.attack_share == pytest.approx(table.y[split.fit_base].mean())


def test_search_stays_inside_training_rows(setup, r1, data_dir):
    r1["stages"]["router"]["candidates"][0]["search"] = {"space": {"max_depth": [2, 4]}, "cv": 3}
    cfg = from_dict(r1, base_dir=str(data_dir))
    _, table, split = setup
    f = fit_candidate(cfg.stages["router"], 0, table, split, seed=0, n_jobs=1)
    assert f.best_params["max_depth"] in (2, 4)
    assert f.cv_auc is not None
    assert 0.5 < f.cv_auc <= 1
    assert f.n_train == len(split.fit)


def test_detector_scores_attacks_higher(setup):
    cfg, table, split = setup
    f = fit_candidate(cfg.stages["gate"], 0, table, split, seed=0, n_jobs=1)
    s = f.score(table.X.iloc[split.val])
    yv = table.y[split.val]
    assert s[yv == 1].mean() > s[yv == 0].mean()


def test_train_on_types_filters_rows(setup, r1, data_dir):
    r1["stages"]["router"]["train_on"] = ["Normal", "DoS"]
    cfg = from_dict(r1, base_dir=str(data_dir))
    _, table, split = setup
    rows = training_rows(cfg.stages["router"], table, split)
    assert table.types is not None
    assert set(np.unique(table.types[rows])) == {"Normal", "DoS"}
    r1["stages"]["router"]["train_on"] = ["Normal", "Unicorn"]
    cfg = from_dict(r1, base_dir=str(data_dir))
    with pytest.raises(FitError, match="one class"):
        fit_candidate(cfg.stages["router"], 0, table, split, seed=0, n_jobs=1)
