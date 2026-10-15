# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import json
import os

import numpy as np
import pytest

from idslab.config import from_dict
from idslab.report import load_summaries, write_report
from idslab.runner import run, unit_dir


@pytest.fixture(scope="module")
def two_seeds(tmp_path_factory, data_dir):
    from conftest import R1  # noqa: PLC0415

    raw = json.loads(json.dumps(R1))
    raw["protocol"]["seeds"] = [0, 1]
    cfg = from_dict(raw, base_dir=str(data_dir))
    out = str(tmp_path_factory.mktemp("runs"))
    run(cfg, out_root=out)
    return cfg, out


def test_report_aggregates_seeds(two_seeds):
    cfg, out = two_seeds
    md, js = write_report(out, cfg)
    with open(js, encoding="utf-8") as f:
        agg = json.load(f)
    summaries, _ = load_summaries(os.path.join(out, cfg.name, "standard"))
    acc = [s["controls"]["systems_test"]["cascade"]["accuracy"] for s in summaries]
    assert agg["systems_test"]["cascade"]["accuracy"]["mean"] == pytest.approx(np.mean(acc))
    assert agg["systems_test"]["cascade"]["accuracy"]["n"] == 2
    with open(md, encoding="utf-8") as f:
        text = f.read()
    for heading in (
        "## Systems on TEST",
        "## Frontier gap on TEST",
        "## Frozen configuration per seed",
        "## Stage effects",
        "## Statements",
    ):
        assert heading in text
    assert chr(0x2014) not in text
    assert chr(0x2013) not in text


def test_unfinished_seed_is_left_out(two_seeds):
    cfg, out = two_seeds
    done = os.path.join(unit_dir(out, cfg, 1), "DONE")
    os.rename(done, done + ".bak")
    summaries, _ = load_summaries(os.path.join(out, cfg.name, "standard"))
    assert [s["seed"] for s in summaries] == [0]
    os.rename(done + ".bak", done)


def test_seeds_of_another_config_are_left_out(two_seeds):
    cfg, out = two_seeds
    raw = json.loads(json.dumps(cfg.raw))
    raw["stages"]["router"]["decision"]["grid"] = [0.7]
    other = from_dict(raw, base_dir=cfg.base_dir)
    with pytest.raises(FileNotFoundError, match="no finished seeds of this config"):
        write_report(out, other)
    summaries, left_out = load_summaries(
        os.path.join(out, cfg.name, "standard"), {"config": other.fingerprint()}
    )
    assert summaries == []
    assert len(left_out) == 2
    assert all("config changed" in line for line in left_out)


def test_seed_lists_and_extra_seeds(two_seeds):
    cfg, out = two_seeds
    raw = json.loads(json.dumps(cfg.raw))
    raw["protocol"]["seeds"] = [0]
    one = from_dict(raw, base_dir=cfg.base_dir)
    assert one.fingerprint() == cfg.fingerprint()  # the seed list is not part of a seed's result
    _, js = write_report(out, one)
    with open(js, encoding="utf-8") as f:
        assert json.load(f)["seeds"] == [0]
    _, js = write_report(out, one, extra_seeds=[1])
    with open(js, encoding="utf-8") as f:
        assert json.load(f)["seeds"] == [0, 1]


def test_no_finished_seed_is_an_error(tmp_path):
    from conftest import R1  # noqa: PLC0415

    with pytest.raises(FileNotFoundError):
        write_report(str(tmp_path), from_dict(json.loads(json.dumps(R1))))
