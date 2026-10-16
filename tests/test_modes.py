# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import copy
import os

import numpy as np
import pandas as pd
import pytest

from idslab.config import from_dict
from idslab.data import DataError, load_table, make_splits
from idslab.report import write_report
from idslab.runner import family_mask, families, run, unit_dir
from idslab.synthetic import make_frame


def heldout(r1, data_dir, fams):
    r1["protocol"] = {"seeds": [0], "mode": "heldout_family", "families": fams}
    return from_dict(r1, base_dir=str(data_dir))


def test_held_out_family_is_absent_from_training_and_present_in_test(data_dir, r1):
    cfg = heldout(r1, data_dir, ["Worms"])
    table = load_table(cfg)
    mask = family_mask(cfg, table, "Worms")
    split = make_splits(table, cfg.protocol, None, seed=0, exclude=mask)
    for part in (split.fit_base, split.cal, split.val):
        assert not mask[part].any()
    assert mask[split.test].any()
    plain = make_splits(table, cfg.protocol, None, seed=0)
    np.testing.assert_array_equal(plain.test, split.test)


def test_test_fraction_keeps_test_identical_across_families(tmp_path, r1):
    make_frame(1500, 7).to_parquet(tmp_path / "all.parquet")
    r1["dataset"] = {"train": "all.parquet", "test_fraction": 0.3, "label": "label", "type": "attack_cat"}
    cfg = heldout(r1, tmp_path, ["DoS", "Scan"])
    table = load_table(cfg)
    a = make_splits(table, cfg.protocol, 0.3, seed=0, exclude=family_mask(cfg, table, "DoS"))
    b = make_splits(table, cfg.protocol, 0.3, seed=0, exclude=family_mask(cfg, table, "Scan"))
    np.testing.assert_array_equal(a.test, b.test)
    assert family_mask(cfg, table, "DoS")[a.test].any()


def test_families_default_to_every_attack_type(data_dir, r1):
    r1["protocol"] = {"seeds": [0], "mode": "heldout_family"}
    cfg = from_dict(r1, base_dir=str(data_dir))
    assert families(cfg, load_table(cfg)) == ["DoS", "Exploit", "Scan", "Worms"]


def test_held_out_run_reports_unseen_attacks_per_family(data_dir, r4, tmp_path):
    r4["protocol"] = {"seeds": [0], "mode": "heldout_family", "families": ["Worms", "DoS"]}
    cfg = from_dict(r4, base_dir=str(data_dir))
    out = str(tmp_path / "runs")
    results = run(cfg, out_root=out)
    assert sorted(r["family"] for r in results) == ["DoS", "Worms"]
    for r in results:
        s = r["summary"]
        assert os.path.isfile(os.path.join(unit_dir(out, cfg, 0, r["family"]), "DONE"))
        u = s["controls"]["unseen_test"]
        assert u["n_unseen"] > 0
        assert set(u["systems"]) >= {"cascade", "B1_first_stage", "budget_matched_first_stage"}
        assert u["frontier_gap_unseen"]["defined"] is True
        types = set(s["candidates"]["type"][0]["classes"])
        assert r["family"] not in types  # the type stage never saw the held-out family
    md, _ = write_report(out, cfg)
    with open(md, encoding="utf-8") as f:
        text = f.read()
    assert "## Unseen attacks on TEST" in text
    assert "| DoS | 1 |" in text
    assert "| Worms | 1 |" in text


@pytest.fixture
def novel_dir(tmp_path):
    train = make_frame(1500, 11)
    train = train[train["attack_cat"] != "Worms"]
    test = make_frame(800, 12)
    test.loc[test["attack_cat"] == "Worms", "attack_cat"] = "Zeroday"
    train.to_parquet(tmp_path / "train.parquet")
    test.to_parquet(tmp_path / "test.parquet")
    return tmp_path


def test_natural_novelty_reports_test_only_types(novel_dir, r1):
    r1["protocol"] = {"seeds": [0], "mode": "natural_novelty", "novel_types": ["Zeroday"]}
    cfg = from_dict(r1, base_dir=str(novel_dir))
    [res] = run(cfg, out_root=str(novel_dir / "runs"))
    u = res["summary"]["controls"]["unseen_test"]
    test = pd.read_parquet(novel_dir / "test.parquet")
    assert u["n_unseen"] == int((test["attack_cat"] == "Zeroday").sum())
    assert u["systems"]["cascade"]["recall_unseen"] is not None


def test_natural_novelty_rejects_types_seen_in_training(novel_dir, r1):
    r1["protocol"] = {"seeds": [0], "mode": "natural_novelty", "novel_types": ["DoS"]}
    cfg = from_dict(r1, base_dir=str(novel_dir))
    with pytest.raises(DataError, match="occur in the train partition"):
        run(cfg, out_root=str(novel_dir / "runs"))


def test_raw_family_level_uses_types_before_mapping(data_dir, r1):
    raw = copy.deepcopy(r1)
    raw["dataset"]["type_map"] = {"DoS": "Flood", "Scan": "Flood"}
    raw["protocol"] = {"seeds": [0], "mode": "heldout_family", "family_level": "raw", "families": ["Scan"]}
    cfg = from_dict(raw, base_dir=str(data_dir))
    table = load_table(cfg)
    mask = family_mask(cfg, table, "Scan")
    assert table.types is not None
    assert set(table.types[mask].tolist()) == {"Flood"}  # mapped type of the raw family
    assert mask.sum() < (table.types == "Flood").sum()  # DoS rows stay in training
