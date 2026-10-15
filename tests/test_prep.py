# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import numpy as np
import pandas as pd
import pytest
from sklearn.base import clone
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.tree import DecisionTreeClassifier

from idslab.prep import UNKNOWN_CODE, Encode


def frame():
    return pd.DataFrame({"proto": ["tcp", "udp", "tcp", None], "bytes": [10, 20, 30, 40]})


def test_codes_are_learned_on_fit_rows_only():
    enc = Encode(categorical=["proto"]).fit(frame())
    out = enc.transform(pd.DataFrame({"proto": ["icmp", "tcp", None], "bytes": [1, 2, 3]}))
    assert out[0, 0] == UNKNOWN_CODE
    assert out[1, 0] == enc.categories_["proto"].index("tcp")
    assert out[2, 0] == enc.categories_["proto"].index("__missing__")
    np.testing.assert_array_equal(out[:, 1], [1, 2, 3])


def test_unlisted_text_column_is_an_error():
    with pytest.raises(ValueError, match="non-numeric"):
        Encode(categorical=[]).fit(frame())


def test_missing_categorical_column_is_an_error():
    with pytest.raises(ValueError, match="not in the data"):
        Encode(categorical=["service"]).fit(frame())


def test_column_order_must_match():
    enc = Encode(categorical=["proto"]).fit(frame())
    with pytest.raises(ValueError, match="columns differ"):
        enc.transform(frame()[["bytes", "proto"]])


def test_works_inside_a_pipeline_with_clone_and_cv():
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"proto": rng.choice(["a", "b", "c"], 60), "v": rng.normal(size=60)})
    y = (X["proto"] == "a").astype(int).to_numpy()
    pipe = make_pipeline(Encode(categorical=["proto"]), DecisionTreeClassifier(random_state=0))
    scores = cross_val_score(clone(pipe), X, y, cv=3)
    assert scores.mean() > 0.9
