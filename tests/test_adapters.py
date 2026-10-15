# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import numpy as np
import pandas as pd
import pytest

from idslab.adapters import (
    ContractError,
    anomaly_score,
    attack_score,
    build_estimator,
    build_pipeline,
)
from idslab.config import Candidate


def cand(cls, **params):
    return Candidate(cls=cls, params=params, search=None, score=None, label=cls)


class Reversed:
    """predict_proba with the attack class first, to check the column lookup."""

    classes_ = np.array([1, 0])

    def predict_proba(self, X):
        p = np.linspace(0.1, 0.9, len(X))
        return np.column_stack([p, 1 - p])


class CustomDetector:
    idslab_contract = "AnomalyScore"

    def fit(self, X):
        self.mu_ = X.mean(axis=0)
        return self

    def anomaly_score(self, X):
        return np.abs(X - self.mu_).sum(axis=1)


def test_attack_column_is_found_by_class_not_position():
    X = np.zeros((5, 2))
    np.testing.assert_allclose(attack_score(Reversed(), X), np.linspace(0.1, 0.9, 5))


def test_seed_and_novelty_are_set():
    rf = build_estimator(cand("sklearn.ensemble.RandomForestClassifier"), seed=7, n_jobs=1)
    assert rf.get_params()["random_state"] == 7
    lof = build_estimator(cand("sklearn.neighbors.LocalOutlierFactor"), seed=0, n_jobs=1)
    assert lof.get_params()["novelty"] is True
    pinned = build_estimator(
        cand("sklearn.ensemble.RandomForestClassifier", random_state=3), seed=7, n_jobs=1
    )
    assert pinned.get_params()["random_state"] == 3


@pytest.mark.parametrize("cls", ["sklearn.ensemble.IsolationForest", "sklearn.neighbors.LocalOutlierFactor"])
def test_anomaly_scores_point_the_right_way(cls):
    rng = np.random.default_rng(0)
    normal = rng.normal(size=(300, 3))
    test = np.vstack([np.zeros((1, 3)), np.full((1, 3), 8.0)])
    pipe = build_pipeline(
        ({"class": "sklearn.preprocessing.StandardScaler"},), build_estimator(cand(cls), 0, 1), 0
    )
    pipe.fit(normal)
    s = anomaly_score(pipe, test, cls, None)
    assert s[1] > s[0]


def test_score_override_and_custom_detector():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(100, 2))
    det = CustomDetector().fit(X)
    s = anomaly_score(det, np.array([[0.0, 0.0], [5.0, 5.0]]), "x.CustomDetector", None)
    assert s[1] > s[0]
    iso = build_estimator(cand("sklearn.ensemble.IsolationForest"), 0, 1).fit(X)
    flipped = anomaly_score(
        iso,
        X[:5],
        "sklearn.ensemble.IsolationForest",
        {"method": "score_samples", "higher_is_anomalous": True},
    )
    np.testing.assert_allclose(flipped, -anomaly_score(iso, X[:5], "sklearn.ensemble.IsolationForest", None))


def test_contract_violations_are_reported():
    class NoAttack:
        classes_ = np.array([0, 2])

        def predict_proba(self, X):
            return np.full((len(X), 2), 0.5)

    with pytest.raises(ContractError, match="attack class 1"):
        attack_score(NoAttack(), np.zeros((3, 1)))

    class NaNDetector:
        idslab_contract = "AnomalyScore"

        def anomaly_score(self, X):
            return np.full(len(X), np.nan)

    with pytest.raises(ContractError, match="non-finite"):
        anomaly_score(NaNDetector(), np.zeros((3, 1)), "x.NaNDetector", None)


def test_pipeline_uses_model_name_for_search_params():
    pipe = build_pipeline(
        ({"class": "idslab.prep.Encode", "params": {"categorical": ["p"]}},),
        build_estimator(cand("sklearn.tree.DecisionTreeClassifier"), 0, 1),
        0,
    )
    assert "model__max_depth" in pipe.get_params()
    X = pd.DataFrame({"p": ["a", "b"] * 20, "v": np.arange(40.0)})
    y = np.array([0, 1] * 20)
    pipe.fit(X, y)
    assert attack_score(pipe, X).shape == (40,)


def test_score_override_reaches_the_detector_inside_a_pipeline():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(200, 3))
    pipe = build_pipeline(
        ({"class": "sklearn.preprocessing.StandardScaler"},),
        build_estimator(cand("sklearn.ensemble.IsolationForest"), 0, 1),
        0,
    ).fit(X)
    # the override is called on the detector after the scaler, not on the Pipeline
    s = anomaly_score(
        pipe,
        X[:4],
        "sklearn.ensemble.IsolationForest",
        {"method": "decision_function", "higher_is_anomalous": False},
    )
    assert s.shape == (4,)
    with pytest.raises(ContractError, match="no method"):
        anomaly_score(pipe, X[:4], "x.Detector", {"method": "not_a_method", "higher_is_anomalous": True})
