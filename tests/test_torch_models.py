# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import numpy as np
import pytest
from sklearn.model_selection import RandomizedSearchCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from idslab.adapters import anomaly_score
from idslab.config import from_dict
from idslab.runner import run

pytest.importorskip("torch")

from idslab.torch_models import TorchAutoencoder, TorchMLPClassifier


def blobs(n=600, k=2, seed=0):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, k, n)
    X = rng.normal(size=(n, 4)) + 3.0 * np.eye(k, 4)[y]
    return X, y


def test_classifier_learns_and_is_seeded():
    X, y = blobs()
    a = TorchMLPClassifier(epochs=15, random_state=1).fit(X, y)
    p = a.predict_proba(X)
    assert p.shape == (len(X), 2)
    np.testing.assert_allclose(p.sum(axis=1), 1.0, rtol=1e-5)
    assert (a.predict(X) == y).mean() > 0.9
    b = TorchMLPClassifier(epochs=15, random_state=1).fit(X, y)
    np.testing.assert_allclose(b.predict_proba(X), p, rtol=1e-6)


def test_multiclass_with_named_types_and_search():
    X, y = blobs(k=3)
    names = np.array(["DoS", "Normal", "Scan"])[y]
    model = TorchMLPClassifier(epochs=40, batch_size=64, random_state=0).fit(X, names)
    assert list(model.classes_) == ["DoS", "Normal", "Scan"]
    assert (model.predict(X) == names).mean() > 0.85
    search = RandomizedSearchCV(
        make_pipeline(StandardScaler(), TorchMLPClassifier(epochs=40, batch_size=64, random_state=0)),
        {"torchmlpclassifier__hidden": [(16,), (32, 16)], "torchmlpclassifier__lr": [1e-3, 3e-3]},
        n_iter=2,
        cv=2,
        random_state=0,
    ).fit(X, names)
    assert search.best_score_ > 0.8


def test_autoencoder_scores_outliers_as_anomalous():
    rng = np.random.default_rng(0)
    normal = rng.normal(size=(500, 4))
    ae = TorchAutoencoder(hidden=(8, 2, 8), epochs=40, random_state=0).fit(normal)
    test = np.vstack([np.zeros((1, 4)), np.full((1, 4), 6.0)])
    s = anomaly_score(ae, test, "idslab.torch_models.TorchAutoencoder", None)
    assert s[1] > s[0]
    assert set(np.unique(ae.predict(test))) <= {-1, 1}


def test_torch_stage_runs_in_a_cascade(data_dir, r1, tmp_path):
    scaler = {"class": "sklearn.preprocessing.StandardScaler"}
    r1["stages"]["router"]["pipeline"] = [*r1["stages"]["router"]["pipeline"], scaler]
    r1["stages"]["router"]["candidates"] = [
        {"class": "idslab.torch_models.TorchMLPClassifier", "params": {"hidden": [16], "epochs": 5}}
    ]
    cfg = from_dict(r1, base_dir=str(data_dir))
    [res] = run(cfg, out_root=str(tmp_path / "runs"))
    assert res["summary"]["candidates"]["router"][0]["class"] == "idslab.torch_models.TorchMLPClassifier"
