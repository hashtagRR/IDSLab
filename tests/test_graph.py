# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import numpy as np

from idslab.config import from_dict
from idslab.graph import OFF, StageState, paths, route, with_off

RF = "sklearn.ensemble.RandomForestClassifier"


def cfg():
    return from_dict(
        {
            "name": "g",
            "dataset": {"train": "a.csv", "test": "b.csv", "label": "label"},
            "entry": "binary",
            "stages": {
                "binary": {
                    "kind": "classifier",
                    "candidates": [{"class": RF}],
                    "decision": {"threshold": 0.5, "off": "benign"},
                    "routes": {"attack": "router", "benign": "BENIGN"},
                },
                "router": {
                    "kind": "classifier",
                    "candidates": [{"class": RF}],
                    "decision": {"grid": [0.7], "off": "attack"},
                    "routes": {"attack": "ATTACK", "benign": "gate"},
                },
                "gate": {
                    "kind": "detector",
                    "candidates": [{"class": "sklearn.ensemble.IsolationForest"}],
                    "decision": {"threshold": 1.0, "off": "normal"},
                    "routes": {"anomalous": "ATTACK", "normal": "BENIGN"},
                },
            },
        }
    )


SCORES = {
    "binary": np.array([0.9, 0.9, 0.9, 0.2]),
    "router": np.array([0.8, 0.1, 0.1, 0.9]),
    "gate": np.array([0.0, 2.0, 0.5, 3.0]),
}


def on(t):
    return StageState(off=False, candidate=0, threshold=t, value=t)


def test_full_cascade_routes_each_flow():
    c = cfg()
    state = {"binary": on(0.5), "router": on(0.7), "gate": on(1.0)}
    r = route(c.order(), c.stages, SCORES, state, 4)
    np.testing.assert_array_equal(r.verdict, [1, 1, 0, 0])
    assert list(r.exit_stage) == ["router", "gate", "gate", "binary"]
    assert list(paths(r, c.order())) == [
        "binary>router",
        "binary>router>gate",
        "binary>router>gate",
        "binary",
    ]


def test_off_uses_the_declared_output():
    c = cfg()
    state = {"binary": on(0.5), "router": OFF, "gate": OFF}
    r = route(c.order(), c.stages, SCORES, state, 4)
    np.testing.assert_array_equal(r.verdict, [1, 1, 1, 0])
    state = with_off({"binary": on(0.5), "router": on(0.7), "gate": on(1.0)}, ["binary"])
    r = route(c.order(), c.stages, SCORES, state, 4)
    np.testing.assert_array_equal(r.verdict, [0, 0, 0, 0])
    assert not r.reach["router"].any()


def test_unreached_stage_needs_no_scores():
    c = cfg()
    state = {"binary": on(0.95), "router": on(0.7), "gate": on(1.0)}
    r = route(c.order(), c.stages, {"binary": SCORES["binary"]}, state, 4)
    np.testing.assert_array_equal(r.verdict, [0, 0, 0, 0])
