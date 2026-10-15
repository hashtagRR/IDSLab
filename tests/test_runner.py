# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import copy
import json
import os

import numpy as np
import pandas as pd
import pytest

from idslab.config import from_dict
from idslab.data import load_table, make_splits
from idslab.runner import run, run_seed, unit_dir


def test_r1_run_writes_every_output(data_dir, r1, tmp_path):
    cfg = from_dict(r1, base_dir=str(data_dir))
    out = str(tmp_path / "runs")
    [res] = run(cfg, out_root=out)
    d = unit_dir(out, cfg, 0)
    for name in ("summary.json", "per_flow.parquet", "val_flow.parquet", "split_ids.parquet", "DONE"):
        assert os.path.isfile(os.path.join(d, name)), name
    s = res["summary"]
    assert set(s["frozen_configuration"]) == {"binary", "router", "gate"}
    assert s["frozen_configuration"]["binary"]["off"] is False
    flow = pd.read_parquet(os.path.join(d, "per_flow.parquet"))
    assert len(flow) == s["split_sizes"]["test"]
    assert {"pred_cascade", "pred_B1_first_stage", "path", "exit_stage"} <= set(flow.columns)
    ids = pd.read_parquet(os.path.join(d, "split_ids.parquet"))
    assert ids["row_id"].is_unique
    c = s["controls"]
    assert {"B1_first_stage", "B2_first_stage_val_cutoff", "budget_matched_first_stage"} <= set(
        c["systems_test"]
    )
    assert len(c["prefix_test"]) == 4


def test_rerun_skips_and_config_change_reruns(data_dir, r1, tmp_path):
    cfg = from_dict(r1, base_dir=str(data_dir))
    out = str(tmp_path / "runs")
    run(cfg, out_root=out)
    [again] = run(cfg, out_root=out)
    assert again["skipped"] is True
    r1b = copy.deepcopy(r1)
    r1b["stages"]["router"]["decision"]["grid"] = [0.7]
    [changed] = run(from_dict(r1b, base_dir=str(data_dir)), out_root=out)
    assert changed["skipped"] is False
    [forced] = run(cfg, out_root=out, force=True)
    assert forced["skipped"] is False


def test_test_labels_never_change_any_choice(data_dir, r1, tmp_path):
    cfg = from_dict(r1, base_dir=str(data_dir))
    table = load_table(cfg)
    split = make_splits(table, cfg.protocol, None, seed=0)
    a = run_seed(cfg, table, 0, data_fps={}, out_root=str(tmp_path / "a"), force=True)["summary"]

    shuffled = copy.deepcopy(table)
    rng = np.random.default_rng(9)
    perm = rng.permutation(split.test)
    shuffled.y[split.test] = table.y[perm]
    assert shuffled.types is not None
    assert table.types is not None
    shuffled.types[split.test] = table.types[perm]
    b = run_seed(cfg, shuffled, 0, data_fps={}, out_root=str(tmp_path / "b"), force=True)["summary"]

    assert json.dumps(a["frozen_configuration"], sort_keys=True) == json.dumps(
        b["frozen_configuration"], sort_keys=True
    )
    assert json.dumps(a["selection_trace"], sort_keys=True) == json.dumps(
        b["selection_trace"], sort_keys=True
    )
    ta = a["controls"]["systems_test"]["cascade"]
    tb = b["controls"]["systems_test"]["cascade"]
    assert ta != tb  # TEST metrics do use the TEST labels


def test_r2_anomaly_first_runs(data_dir, r2, tmp_path):
    cfg = from_dict(r2, base_dir=str(data_dir))
    [res] = run(cfg, out_root=str(tmp_path / "runs"))
    s = res["summary"]
    assert s["controls"]["first_stage"] == "clear"
    assert s["frozen_configuration"]["clear"]["off"] is False
    assert set(s["controls"]["systems_test"]) >= {"cascade", "B1_first_stage"}


@pytest.mark.parametrize("jobs", [2])
def test_parallel_seeds_match_serial(data_dir, r1, tmp_path, jobs):
    r1["protocol"]["seeds"] = [0, 1]
    cfg = from_dict(r1, base_dir=str(data_dir))
    serial = run(cfg, out_root=str(tmp_path / "s"))
    parallel = run(cfg, out_root=str(tmp_path / "p"), jobs=jobs)
    for x, y in zip(serial, parallel, strict=True):
        assert x["summary"]["frozen_configuration"] == y["summary"]["frozen_configuration"]
        assert x["summary"]["controls"]["systems_test"] == y["summary"]["controls"]["systems_test"]


def test_unreadable_summary_reruns_the_seed(data_dir, r2, tmp_path):
    cfg = from_dict(r2, base_dir=str(data_dir))
    out = str(tmp_path / "runs")
    run(cfg, out_root=out)
    os.remove(os.path.join(unit_dir(out, cfg, 0), "summary.json"))
    [again] = run(cfg, out_root=out)
    assert again["skipped"] is False
    with open(os.path.join(unit_dir(out, cfg, 0), "DONE"), "w", encoding="utf-8") as f:
        f.write("{not json")
    [third] = run(cfg, out_root=out)
    assert third["skipped"] is False
