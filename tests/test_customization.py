# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Cost objectives, stage cost, greedy_fixed and plugins."""

import copy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from idslab import plugins
from idslab.config import ConfigError, from_dict
from idslab.metrics import binary_metrics, from_counts, objective_name
from idslab.report import write_report
from idslab.runner import run


def test_cost_objective_is_negative_average_cost():
    name = objective_name({"cost": {"missed_attack": 10, "false_alarm": 1}})
    assert name == "cost(missed_attack=10,false_alarm=1)"
    # tp, fp, tn, fn = 5, 2, 10, 3: cost (10 * 3 + 1 * 2) / 20
    assert float(from_counts(name, 5, 2, 10, 3)) == pytest.approx(-32 / 20)
    y = np.array([1, 1, 0, 0])
    assert binary_metrics(y, np.array([1, 0, 0, 1]), name)[name] == pytest.approx(-(10 + 1) / 4)


def test_recall_at_fpr_prefers_options_within_the_cap():
    name = objective_name({"recall_at_fpr": 0.1})
    within = float(from_counts(name, 6, 1, 19, 4))  # fpr 0.05, recall 0.6
    over = float(from_counts(name, 9, 5, 15, 1))  # fpr 0.25: scored as minus the rate
    assert within == pytest.approx(0.6)
    assert over == pytest.approx(-0.25)
    assert within > over


@pytest.mark.parametrize(
    "spec",
    [
        {"cost": {"missed_attack": -1, "false_alarm": 1}},
        {"cost": {"missed_attack": 0, "false_alarm": 0}},
        {"cost": {"missed": 1}},
        {"recall_at_fpr": 1.5},
        {"recall_at_fpr": 0.1, "cost": {}},
        "nope",
    ],
)
def test_bad_objectives_are_config_errors(r1, spec):
    r1["protocol"]["objective"] = spec
    with pytest.raises(ConfigError, match="objective"):
        from_dict(r1)


def test_declared_cost_is_checked_and_not_in_the_fingerprint(r1):
    base = from_dict(copy.deepcopy(r1)).fingerprint()
    r1["stages"]["gate"]["cost"] = 5
    assert from_dict(copy.deepcopy(r1)).fingerprint() == base
    r1["stages"]["gate"]["cost"] = 0
    with pytest.raises(ConfigError, match="cost must be a positive number"):
        from_dict(r1)


def test_cost_objective_runs_and_reports_stage_cost(data_dir, r1, tmp_path):
    r1["protocol"]["objective"] = {"cost": {"missed_attack": 5, "false_alarm": 1}}
    r1["stages"]["gate"]["cost"] = 4
    cfg = from_dict(r1, base_dir=str(data_dir))
    out = str(tmp_path / "runs")
    [res] = run(cfg, out_root=out)
    s = res["summary"]
    goal = "cost(missed_attack=5,false_alarm=1)"
    assert s["controls"]["objective"] == goal
    assert goal in s["controls"]["systems_test"]["cascade"]
    assert goal in s["controls"]["bootstrap_cascade_minus"]["B1_first_stage"]
    sc = s["stage_cost"]
    assert sc["binary"]["reach_share"] == 1.0
    assert sc["binary"]["ms_per_1k"] > 0
    md, _ = write_report(out, cfg)
    text = Path(md).read_text(encoding="utf-8")
    assert "## Stage cost" in text
    assert "negative average cost per flow" in text
    html = Path(md).with_suffix(".html").read_text(encoding="utf-8")
    assert "Stage cost" in html


def test_greedy_fixed_keeps_later_fixed_stages_on(data_dir, r2, tmp_path):
    def val_accuracy(search):
        cfg_raw = copy.deepcopy(r2)
        cfg_raw["protocol"]["search"] = search
        cfg = from_dict(cfg_raw, base_dir=str(data_dir))
        [res] = run(cfg, out_root=str(tmp_path / search))
        return res["summary"]["controls"]["cascade_val"]["accuracy"]

    # in R2 the classifier after the screen has a fixed threshold, so greedy_fixed judges the
    # screen with it on; that can only match or beat judging the screen alone
    assert val_accuracy("greedy_fixed") >= val_accuracy("greedy")


@pytest.fixture
def _fake_plugin(monkeypatch):
    def plugin():
        return {
            "classifier": [{"class": "sklearn.linear_model.RidgeClassifier", "label": "Ridge (plugin)"}],
            "objectives": {"tp_minus_fp": lambda tp, fp, tn, fn: (tp - fp) / (tp + fp + tn + fn)},
        }

    def broken():
        raise RuntimeError("broken plugin")

    eps = [
        SimpleNamespace(name="demo", load=lambda: plugin),
        SimpleNamespace(name="bad", load=lambda: broken),
    ]
    monkeypatch.setattr(plugins, "_entry_points", lambda: eps)
    plugins.load.cache_clear()
    yield
    plugins.load.cache_clear()


@pytest.mark.usefixtures("_fake_plugin")
def test_plugins_add_objectives_and_catalog_entries(r1):
    from idslab.dashboard.catalog import catalog  # noqa: PLC0415

    loaded = plugins.load()
    assert loaded["plugins"] == ["demo"]  # the broken one is skipped
    assert float(from_counts("tp_minus_fp", 3, 1, 4, 2)) == pytest.approx(0.2)
    r1["protocol"]["objective"] = "tp_minus_fp"
    assert from_dict(r1).protocol.objective == "tp_minus_fp"
    assert any(e["label"] == "Ridge (plugin)" for e in catalog()["classifier"])
