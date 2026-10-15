# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import numpy as np
import pytest

from idslab.config import from_dict
from idslab.selection import budget_threshold, select

N = 400


def labels():
    rng = np.random.default_rng(0)
    return (rng.random(N) < 0.4).astype(int), rng


def scores(y, rng, router="useless"):
    good = np.clip(0.75 * y + rng.normal(0.15, 0.15, N), 0, 1)
    noise = rng.random(N)
    if router == "useless":
        r = np.full(N, 0.99)
    elif router == "helpful":
        # binary has false alarms at score >= 0.5; the router knows the truth
        r = np.where(y == 1, 0.95, 0.05)
    else:
        r = np.where(y == 1, 0.05, 0.95)
    gate = rng.normal(size=N) + 3 * y
    return {"binary": [good, noise], "router": [r], "gate": [gate]}


def cfg(r1, **changes):
    for path, value in changes.items():
        stage, key = path.split("__")
        r1["stages"][stage][key] = value
    return from_dict(r1)


def test_better_auc_candidate_wins_and_useless_router_is_off(r1):
    y, rng = labels()
    sel = select(cfg(r1), scores(y, rng), y)
    binary = sel.trace[0]
    assert (
        binary["candidate_auc"]["RandomForestClassifier#0"] > binary["candidate_auc"]["LogisticRegression#1"]
    )
    assert sel.state["binary"].candidate == 0
    assert sel.state["router"].off  # every grid value ties with off -> off


def test_helpful_router_is_switched_on_and_harmful_one_off(r1):
    y, rng = labels()
    sel = select(cfg(r1), scores(y, rng, "helpful"), y)
    assert not sel.state["router"].off
    y, rng = labels()
    sel = select(cfg(r1), scores(y, rng, "harmful"), y)
    assert sel.state["router"].off


def test_first_stage_is_never_offered_off(r1):
    y, rng = labels()
    c = cfg(r1, binary__decision={"grid": [0.3, 0.5, 0.7], "off": "benign"})
    sel = select(c, scores(y, rng), y)
    assert not sel.state["binary"].off
    assert all(o["candidate"] is not None for o in sel.trace[0]["options"])


def test_sparse_reach_switches_stage_off(r1):
    y, rng = labels()
    c = cfg(r1, router__min_val_rows=10_000)
    sel = select(c, scores(y, rng, "helpful"), y)
    assert sel.state["router"].off
    assert any("reach the stage" in f for f in sel.trace[1]["fallbacks"])


def test_budget_threshold_flags_the_budget_share():
    rng = np.random.default_rng(1)
    s = rng.normal(size=5000)
    benign = np.ones(5000, bool)
    t, share = budget_threshold(s, benign, 0.05)
    assert share == pytest.approx(0.05, abs=0.002)
    assert (s >= t).mean() == pytest.approx(share)


def test_trace_records_every_option(r1):
    y, rng = labels()
    sel = select(cfg(r1), scores(y, rng, "helpful"), y)
    router = sel.trace[1]
    assert [o["value"] for o in router["options"]] == [None, 0.6, 0.75, 0.9]
    assert router["chosen"]["off"] is False
    assert "val_accuracy" in router


def test_ties_go_to_first_grid_value_before_first_candidate(r1):
    y, rng = labels()
    c = cfg(
        r1,
        router__candidates=[r1["stages"]["router"]["candidates"][0]] * 2,
        router__select_by="objective",
        router__decision={"grid": [0.3, 0.5], "off": "attack"},
    )
    s = scores(y, rng, "helpful")
    s["router"] = [s["router"][0], s["router"][0].copy()]  # identical candidates tie on every value
    sel = select(c, s, y)
    chosen = sel.trace[1]["chosen"]
    assert chosen["value"] == 0.3
    assert sel.state["router"].candidate == 0
    assert [(o["value"], o["candidate"]) for o in sel.trace[1]["options"][1:3]] == [
        (0.3, "RandomForestClassifier#0"),
        (0.3, "RandomForestClassifier#1"),
    ]
