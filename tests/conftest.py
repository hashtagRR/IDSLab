# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import copy

import pytest

from idslab.synthetic import write_example

RF_SMALL = {
    "class": "sklearn.ensemble.RandomForestClassifier",
    "params": {"n_estimators": 20, "max_depth": 6},
}
ENCODE = {"class": "idslab.prep.Encode", "params": {"categorical": ["proto", "service"]}}

R1 = {
    "name": "r1",
    "dataset": {
        "train": "train.parquet",
        "test": "test.parquet",
        "label": "label",
        "type": "attack_cat",
        "normal_type": "Normal",
    },
    "protocol": {"seeds": [0], "objective": "accuracy"},
    "entry": "binary",
    "stages": {
        "binary": {
            "kind": "classifier",
            "calibrate": "sigmoid",
            "pipeline": [ENCODE],
            "candidates": [
                RF_SMALL,
                {"class": "sklearn.linear_model.LogisticRegression", "params": {"max_iter": 500}},
            ],
            "decision": {"threshold": 0.5, "off": "benign"},
            "routes": {"attack": "router", "benign": "BENIGN"},
        },
        "router": {
            "kind": "classifier",
            "pipeline": [ENCODE],
            "candidates": [RF_SMALL],
            "decision": {"grid": [0.6, 0.75, 0.9], "off": "attack"},
            "routes": {"attack": "ATTACK", "benign": "gate"},
        },
        "gate": {
            "kind": "detector",
            "fit_on": "benign",
            "pipeline": [ENCODE, {"class": "sklearn.preprocessing.StandardScaler"}],
            "candidates": [{"class": "sklearn.ensemble.IsolationForest", "params": {"n_estimators": 50}}],
            "decision": {"budget": [0.01, 0.05], "off": "normal"},
            "routes": {"anomalous": "ATTACK", "normal": "BENIGN"},
        },
    },
    "controls": {"bootstrap": 200, "frontier_bootstrap": 20},
}

R2 = {
    "name": "r2",
    "dataset": copy.deepcopy(R1["dataset"]),
    "protocol": {"seeds": [0], "objective": "accuracy"},
    "entry": "screen",
    "stages": {
        "screen": {
            "kind": "detector",
            "fit_on": "benign",
            "pipeline": [ENCODE, {"class": "sklearn.preprocessing.StandardScaler"}],
            "candidates": [{"class": "sklearn.ensemble.IsolationForest", "params": {"n_estimators": 50}}],
            "decision": {"budget": [0.2, 0.4], "off": "anomalous"},
            "routes": {"anomalous": "clear", "normal": "BENIGN"},
        },
        "clear": {
            "kind": "classifier",
            "calibrate": "sigmoid",
            "pipeline": [ENCODE],
            "candidates": [RF_SMALL],
            "decision": {"threshold": 0.5, "off": "attack"},
            "routes": {"attack": "ATTACK", "benign": "BENIGN"},
        },
    },
    "controls": {"first_stage": "clear", "bootstrap": 200, "frontier_bootstrap": 20},
}


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    write_example(str(d), n_train=1500, n_test=800, seed=3)
    return d


@pytest.fixture
def r1():
    return copy.deepcopy(R1)


@pytest.fixture
def r2():
    return copy.deepcopy(R2)


R4 = copy.deepcopy(R1)  # the full four-layer design: router score feeds the type stage, pool gate
R4["name"] = "r4"
R4["stages"]["router"]["routes"] = {"attack": "type", "benign": "gate"}
R4["stages"]["type"] = {
    "kind": "multiclass",
    "extra_features": ["router"],
    "pipeline": [ENCODE],
    "candidates": [{"class": "sklearn.ensemble.RandomForestClassifier", "params": {"n_estimators": 20}}],
    "decision": {"confidence": [0.0, 0.4]},
    "next": "ATTACK",
}
R4["stages"]["gate"] = {
    "kind": "detector",
    "fit_on": "pool",
    "pipeline": [ENCODE, {"class": "sklearn.preprocessing.StandardScaler"}],
    "candidates": [{"class": "sklearn.neighbors.LocalOutlierFactor", "params": {"n_neighbors": 15}}],
    "decision": {"top_fraction": [0.02, 0.1], "off": "normal"},
    "routes": {"anomalous": "ATTACK", "normal": "BENIGN"},
}


@pytest.fixture
def r4():
    return copy.deepcopy(R4)
