# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import copy
import importlib.util

import pytest

from idslab.config import ConfigError, from_dict, parse_yaml, topo_order

RF = "sklearn.ensemble.RandomForestClassifier"
IFOREST = "sklearn.ensemble.IsolationForest"


def base():
    return {
        "name": "t",
        "dataset": {"train": "train.parquet", "test": "test.parquet", "label": "label", "type": "attack_cat"},
        "entry": "binary",
        "stages": {
            "binary": {
                "kind": "classifier",
                "calibrate": "sigmoid",
                "candidates": [{"class": RF, "params": {"n_estimators": 10}}],
                "decision": {"threshold": 0.5, "off": "benign"},
                "routes": {"attack": "router", "benign": "BENIGN"},
            },
            "router": {
                "kind": "classifier",
                "candidates": [{"class": RF}],
                "decision": {"grid": [0.6, 0.8], "off": "attack"},
                "routes": {"attack": "ATTACK", "benign": "gate"},
            },
            "gate": {
                "kind": "detector",
                "fit_on": "benign",
                "candidates": [{"class": IFOREST}],
                "decision": {"budget": [0.01, 0.05], "off": "normal"},
                "routes": {"anomalous": "ATTACK", "normal": "BENIGN"},
            },
        },
    }


def errors(raw):
    with pytest.raises(ConfigError) as e:
        from_dict(raw)
    return str(e.value)


def test_valid_config_loads_with_defaults():
    cfg = from_dict(base())
    assert cfg.order() == ["binary", "router", "gate"]
    assert cfg.controls.first_stage == "binary"
    assert cfg.protocol.objective == "accuracy"
    assert cfg.stages["router"].decision.tunable
    assert not cfg.stages["binary"].decision.tunable
    assert cfg.stages["gate"].select_by == "objective"
    assert cfg.stages["binary"].select_by == "auc"


def test_fingerprint_changes_with_content():
    a, b = base(), base()
    b["stages"]["router"]["decision"]["grid"] = [0.7]
    assert from_dict(a).fingerprint() == from_dict(copy.deepcopy(a)).fingerprint()
    assert from_dict(a).fingerprint() != from_dict(b).fingerprint()


def test_unknown_keys_are_rejected():
    raw = base()
    raw["stages"]["binary"]["calibrte"] = "sigmoid"
    assert "unknown keys ['calibrte']" in errors(raw)


def test_off_is_required():
    raw = base()
    del raw["stages"]["router"]["decision"]["off"]
    assert "'off' is required" in errors(raw)


def test_cycle_and_unreachable_stage():
    raw = base()
    raw["stages"]["gate"]["routes"]["normal"] = "router"
    assert "routing cycle" in errors(raw)
    raw = base()
    raw["stages"]["spare"] = copy.deepcopy(raw["stages"]["gate"])
    assert "not reachable" in errors(raw)


def test_unknown_route_target():
    raw = base()
    raw["stages"]["router"]["routes"]["attack"] = "typer"
    assert "'typer' is neither a stage" in errors(raw)


def test_routes_must_cover_outputs():
    raw = base()
    del raw["stages"]["router"]["routes"]["benign"]
    assert "routes must cover exactly" in errors(raw)


@pytest.mark.parametrize(
    ("change", "phase"),
    [
        (lambda r: r["stages"]["binary"].update(kind="multiclass"), "phase 2"),
        (lambda r: r["stages"]["router"].update(extra_features=["binary"]), "phase 2"),
        (lambda r: r["stages"]["gate"].update(fit_on="pool"), "phase 2"),
        (lambda r: r["stages"]["gate"]["decision"].update(top_fraction=[0.05]), "phase 2"),
        (lambda r: r.setdefault("protocol", {}).update(mode="heldout_family"), "phase 3"),
        (lambda r: r.setdefault("protocol", {}).update(search="joint"), "phase 4"),
    ],
)
def test_later_phase_features_are_named(change, phase):
    raw = base()
    change(raw)
    assert phase in errors(raw)


def test_made_up_class_and_parameter_are_caught():
    raw = base()
    raw["stages"]["router"]["candidates"] = [{"class": "sklearn.ensemble.RandomForestClassifer"}]
    assert "has no attribute 'RandomForestClassifer'" in errors(raw)
    raw = base()
    raw["stages"]["router"]["candidates"] = [{"class": RF, "search": {"space": {"max_deep": [3, 5]}}}]
    assert "has no parameters ['max_deep']" in errors(raw)
    raw = base()
    raw["stages"]["router"]["candidates"] = [{"class": RF, "params": {"n_estimator": 5}}]
    assert "failed" in errors(raw)


def test_missing_optional_library_names_the_extra():
    raw = base()
    raw["stages"]["gate"]["candidates"] = [{"class": "pyod.models.iforest.IForest"}]
    if importlib.util.find_spec("pyod") is None:
        assert "pip install idslab[pyod]" in errors(raw)
    else:
        from_dict(raw)


def test_classifier_needs_both_classes_and_probabilities():
    raw = base()
    raw["stages"]["binary"]["train_on"] = "benign"
    assert "needs benign and attack rows" in errors(raw)
    raw = base()
    raw["stages"]["router"]["candidates"] = [{"class": "sklearn.svm.LinearSVC"}]
    assert "set calibrate: sigmoid" in errors(raw)
    raw["stages"]["router"]["calibrate"] = "sigmoid"
    from_dict(raw)


def test_lof_must_be_in_novelty_mode():
    raw = base()
    raw["stages"]["gate"]["candidates"] = [
        {"class": "sklearn.neighbors.LocalOutlierFactor", "params": {"novelty": False}}
    ]
    assert "novelty must be true" in errors(raw)


def test_first_stage_must_be_a_classifier():
    raw = base()
    raw["controls"] = {"first_stage": "gate"}
    assert "must be a classifier stage" in errors(raw)


def test_dataset_needs_label_or_type_and_one_test_source():
    raw = base()
    del raw["dataset"]["label"]
    del raw["dataset"]["type"]
    assert "give 'label', 'type', or both" in errors(raw)
    raw = base()
    raw["dataset"]["test_fraction"] = 0.3
    assert "exactly one of 'test'" in errors(raw)


def test_detector_search_is_rejected():
    raw = base()
    raw["stages"]["gate"]["candidates"] = [{"class": IFOREST, "search": {"space": {"n_estimators": [50]}}}]
    assert "no labels to search on" in errors(raw)


def test_topo_order_keeps_config_order_for_ties():
    routes = {
        "a": {"attack": "c", "benign": "b"},
        "b": {"anomalous": "ATTACK", "normal": "BENIGN"},
        "c": {"attack": "ATTACK", "benign": "BENIGN"},
    }
    assert topo_order("a", routes, ["a", "b", "c"]) == ["a", "b", "c"]
    assert topo_order("a", routes, ["a", "c", "b"]) == ["a", "c", "b"]


def test_yaml_off_on_yes_no_stay_words():
    raw = parse_yaml("decision: {grid: [0.5], off: attack}\nflags: [on, off, yes, no, true, False]\n")
    assert raw["decision"]["off"] == "attack"
    assert raw["flags"] == ["on", "off", "yes", "no", True, False]


def test_fingerprint_ignores_seed_list_and_parallelism():
    a, b = base(), base()
    b["protocol"] = {"seeds": [0, 1, 2, 3, 4, 5], "n_jobs": -1}
    assert from_dict(a).fingerprint() == from_dict(b).fingerprint()
    b["protocol"]["objective"] = "mcc"
    assert from_dict(a).fingerprint() != from_dict(b).fingerprint()


@pytest.mark.parametrize(
    ("section", "value"),
    [
        ("protocol", {"seeds": [True]}),
        ("protocol", {"subsample": {"train": True}}),
        ("protocol", {"n_jobs": True}),
        ("controls", {"bootstrap": True}),
    ],
)
def test_booleans_are_not_integers(section, value):
    raw = base()
    raw[section] = value
    with pytest.raises(ConfigError):
        from_dict(raw)


def test_search_n_iter_must_be_a_positive_integer():
    raw = base()
    raw["stages"]["router"]["candidates"] = [
        {"class": RF, "search": {"space": {"max_depth": [2]}, "n_iter": 0}}
    ]
    assert "n_iter must be a positive integer" in errors(raw)


class Custom:
    idslab_contract = "AttackScore"

    def fit(self, X, y):
        return None

    def attack_score(self, X):
        return [0.5] * len(X)


def test_custom_adapter_rules_are_checked_before_data(monkeypatch):
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    mod = types.ModuleType("custom_mod")
    mod.__dict__["Custom"] = Custom
    monkeypatch.setitem(sys.modules, "custom_mod", mod)
    raw = base()
    raw["stages"]["router"]["candidates"] = [{"class": "custom_mod.Custom"}]
    from_dict(raw)
    raw["stages"]["router"]["pipeline"] = [{"class": "sklearn.preprocessing.StandardScaler"}]
    assert "remove the pipeline steps" in errors(raw)
    raw = base()
    raw["stages"]["binary"]["candidates"] = [{"class": "custom_mod.Custom"}]
    assert "cannot be calibrated" in errors(raw)


def test_classifier_type_list_needs_an_attack_type():
    raw = base()
    raw["stages"]["router"]["train_on"] = ["Normal"]
    assert "at least one attack type" in errors(raw)
