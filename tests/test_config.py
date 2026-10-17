# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import copy
import importlib.util
from importlib import resources

import pytest

from idslab.config import ConfigError, dump_yaml, from_dict, parse_yaml, topo_order

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


def test_search_mode_and_joint_limit():
    raw = base()
    raw["protocol"] = {"search": "joint", "joint_max": 200}
    cfg = from_dict(raw)
    assert cfg.protocol.search == "joint"
    assert cfg.protocol.joint_max == 200
    raw["protocol"] = {"search": "exhaustive"}
    assert "one of greedy, joint, greedy_fixed" in errors(raw)


def test_external_candidates_are_checked():
    raw = base()
    raw["stages"]["router"]["candidates"] = [{"external": {"path": "scores/cnn_{seed}_{family}.parquet"}}]
    cand = from_dict(raw).stages["router"].candidates[0]
    assert cand.external == "scores/cnn_{seed}_{family}.parquet"
    assert cand.label == "external#0"
    raw["stages"]["router"]["candidates"] = [{"external": "scores/{fold}.parquet"}]
    assert "placeholders ['fold']" in errors(raw)
    raw = four_layer()
    raw["stages"]["gate"]["candidates"] = [{"external": "scores/lof.parquet"}]
    assert "a score file cannot do" in errors(raw)


@pytest.mark.parametrize(
    ("protocol", "message"),
    [
        ({"mode": "natural_novelty"}, "needs the raw types"),
        ({"mode": "standard", "novel_types": ["x"]}, "applies to mode natural_novelty only"),
        ({"mode": "standard", "families": ["DoS"]}, "applies to mode heldout_family only"),
        ({"mode": "heldout_family", "family_level": "deep"}, "mapped or raw"),
        ({"mode": "zeroday"}, "not in"),
    ],
)
def test_mode_settings_are_checked(protocol, message):
    raw = base()
    raw["protocol"] = protocol
    assert message in errors(raw)


def test_modes_need_a_type_column_and_known_families():
    from idslab.config import check_against_data  # noqa: PLC0415

    raw = base()
    raw["protocol"] = {"mode": "heldout_family"}
    del raw["dataset"]["type"]
    assert "needs dataset.type" in errors(raw)
    raw = base()
    raw["protocol"] = {"mode": "heldout_family", "families": ["DoS", "Ghost"]}
    with pytest.raises(ConfigError, match="Ghost"):
        check_against_data(from_dict(raw), {"Normal", "DoS"}, {"Normal", "DoS"})
    raw["protocol"] = {"mode": "heldout_family", "families": ["Normal"]}
    with pytest.raises(ConfigError, match="normal type cannot be held out"):
        check_against_data(from_dict(raw), {"Normal", "DoS"}, {"Normal", "DoS"})


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


def four_layer():
    raw = base()
    raw["stages"]["router"]["routes"] = {"attack": "type", "benign": "gate"}
    raw["stages"]["type"] = {
        "kind": "multiclass",
        "extra_features": ["router"],
        "candidates": [{"class": "sklearn.ensemble.HistGradientBoostingClassifier"}],
        "decision": {"confidence": [0.0, 0.2]},
        "next": "ATTACK",
    }
    raw["stages"]["gate"] = {
        "kind": "detector",
        "fit_on": "pool",
        "candidates": [{"class": "sklearn.neighbors.LocalOutlierFactor", "params": {"n_neighbors": 30}}],
        "decision": {"top_fraction": [0.02, 0.05], "off": "normal"},
        "routes": {"anomalous": "ATTACK", "normal": "BENIGN"},
    }
    return raw


def test_four_layer_design_loads():
    cfg = from_dict(four_layer())
    assert cfg.order() == ["binary", "router", "gate", "type"]  # ties keep config order
    t, g = cfg.stages["type"], cfg.stages["gate"]
    assert t.role == "annotate"
    assert t.routes == {"next": "ATTACK"}
    assert t.decision.tunable
    assert g.transductive
    assert g.min_pool == 31


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r["stages"]["type"].update(routes={"default": "ATTACK"}), "use 'next'"),
        (lambda r: r["stages"]["type"].update(role="decide", routes={"DoS": "ATTACK"}), "'default' route"),
        (lambda r: r["stages"]["type"].update(extra_features=["gate"]), "pool detector"),
        (lambda r: r["stages"]["type"].update(extra_features=["type"]), "names the stage itself"),
        (lambda r: r["stages"]["gate"].update(min_pool=10), "below the largest neighbour count"),
        (lambda r: r["stages"]["gate"]["decision"].update(budget=0.05), "exactly one of"),
        (lambda r: r["stages"]["gate"].update(decision={"budget": 0.05, "off": "normal"}), "not pool"),
        (
            lambda r: r["stages"]["gate"]["candidates"][0]["params"].update(novelty=True),
            "novelty must be false",
        ),
        (lambda r: r["stages"]["type"].update(train_on="hard"), "needs a 'hard' block"),
        (
            lambda r: r["stages"]["type"].update(
                train_on="hard", hard={"source": "router", "band": [0.7, 0.3]}
            ),
            "low < high",
        ),
    ],
)
def test_feature_and_pool_rules(change, message):
    raw = four_layer()
    change(raw)
    assert message in errors(raw)


def test_dependency_cycle_is_rejected():
    raw = four_layer()
    raw["stages"]["binary"]["extra_features"] = ["router"]  # router comes after binary in the routes
    assert "form a cycle" in errors(raw)


def test_route_names_are_checked_against_the_data():
    from idslab.config import check_against_data  # noqa: PLC0415

    raw = four_layer()
    raw["stages"]["type"].update(
        role="decide", routes={"DoS": "ATTACK", "Typo": "ATTACK", "default": "ATTACK"}
    )
    del raw["stages"]["type"]["next"]
    raw["stages"]["type"]["decision"]["off"] = "default"
    cfg = from_dict(raw)
    with pytest.raises(ConfigError, match=r"\['Typo'\] are not types"):
        check_against_data(cfg, {"Normal", "DoS", "Scan"})
    check_against_data(from_dict(four_layer()), {"Normal", "DoS"})


def test_missing_torch_names_the_extra(monkeypatch):
    import idslab.config as config_module  # noqa: PLC0415

    real = config_module.importlib.util.find_spec
    monkeypatch.setattr(
        config_module.importlib.util, "find_spec", lambda name: None if name == "torch" else real(name)
    )
    raw = base()
    raw["stages"]["router"]["candidates"] = [{"class": "idslab.torch_models.TorchMLPClassifier"}]
    assert "pip install idslab[torch]" in errors(raw)


@pytest.mark.parametrize("name", ["r1_router_gate.yaml", "r2_anomaly_first.yaml", "r1_four_layer.yaml"])
def test_dump_yaml_round_trips(name):
    raw = parse_yaml((resources.files("idslab.examples") / name).read_text(encoding="utf-8"))
    text = dump_yaml(raw)
    assert parse_yaml(text) == raw
    assert "'off'" not in text  # YAML 1.2 rules: off is a word
    assert '"off"' not in text


def test_chained_score_dependencies_are_refused(r4):
    # router feeds the type stage; letting the router use the entry stage's score as well makes a
    # chain binary -> router -> type, whose out-of-fold scores would not be out of fold
    r4["stages"]["router"]["extra_features"] = ["binary"]
    with pytest.raises(ConfigError, match="chained score dependencies are not supported"):
        from_dict(r4)
    r4["stages"]["router"].pop("extra_features")
    r4["stages"]["router"]["train_on"] = "hard"
    r4["stages"]["router"]["hard"] = {"source": "binary", "band": [0.3, 0.7]}
    with pytest.raises(ConfigError, match="chained score dependencies"):
        from_dict(r4)
