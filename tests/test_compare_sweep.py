# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Comparisons of finished experiments, and sweeps over config variants."""

import copy
import os
import shutil
from pathlib import Path

import pytest
import yaml

from idslab.cli import main
from idslab.compare import CompareError, compare, write_comparison
from idslab.config import from_dict, load
from idslab.report import write_report
from idslab.runner import run
from idslab.sweep import SweepError, load_sweep, run_sweep, set_key


@pytest.fixture
def workspace(tmp_path, data_dir, r2):
    """A folder with the data, the R2 config and a sweep file."""
    (tmp_path / "data").mkdir()
    for name in ("train.parquet", "test.parquet"):
        shutil.copyfile(data_dir / name, tmp_path / "data" / name)
    r2["dataset"]["train"] = "data/train.parquet"
    r2["dataset"]["test"] = "data/test.parquet"
    (tmp_path / "r2.yaml").write_text(yaml.safe_dump(r2))
    return tmp_path


def run_config(raw, base_dir, out):
    cfg = from_dict(raw, base_dir=str(base_dir))
    run(cfg, out_root=str(out))
    write_report(str(out), cfg)
    return os.path.join(str(out), cfg.name)


def test_set_key_handles_mappings_and_lists():
    raw = {"stages": {"a": {"candidates": [{"class": "x"}]}}}
    set_key(raw, "stages.a.candidates.0.class", "y")
    set_key(raw, "stages.a.cost", 3)
    set_key(raw, "protocol.seeds", [0])
    assert raw["stages"]["a"] == {"candidates": [{"class": "y"}], "cost": 3}
    assert raw["protocol"] == {"seeds": [0]}
    with pytest.raises(SweepError, match="not an index"):
        set_key(raw, "stages.a.candidates.4.class", "z")


def test_compare_pairs_experiments_on_the_same_splits(workspace, r2):
    out = workspace / "runs"
    a = run_config(copy.deepcopy(r2), workspace / "data" / "..", out)
    b_raw = copy.deepcopy(r2)
    b_raw["name"] = "r2-fixed"
    b_raw["stages"]["screen"]["decision"] = {"budget": 0.2, "off": "normal"}
    b = run_config(b_raw, workspace, out)
    c = compare([a, b], bootstrap=50)
    assert c["baseline"] == "r2"
    assert c["warnings"] == []
    [pair] = c["paired"]
    assert pair["other"] == "r2-fixed"
    [row] = pair["per_unit"]
    assert row["unit"] == "0"
    assert row["ci95"] is not None
    md, js, html = write_comparison(c, str(workspace / "cmp"))
    assert "Paired against r2" in Path(md).read_text(encoding="utf-8")
    assert Path(js).is_file()
    assert "<svg" in Path(html).read_text(encoding="utf-8")


def test_compare_warns_when_objectives_differ_and_does_not_pair(workspace, r2):
    out = workspace / "runs"
    a = run_config(copy.deepcopy(r2), workspace, out)
    b_raw = copy.deepcopy(r2)
    b_raw["name"] = "r2-mcc"
    b_raw["protocol"]["objective"] = "mcc"
    b = run_config(b_raw, workspace, out)
    c = compare([a, b], bootstrap=20)
    assert any("different objectives" in w for w in c["warnings"])
    assert c["paired"] == []


def test_compare_needs_reports(tmp_path):
    with pytest.raises(CompareError, match="at least two"):
        compare([str(tmp_path)])
    with pytest.raises(CompareError, match=r"no report\.json"):
        compare([str(tmp_path), str(tmp_path)])


def test_sweep_checks_everything_before_running(workspace):
    bad = workspace / "bad.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "name": "s",
                "base": "r2.yaml",
                "variants": {"ok": {}, "broken": {"stages.screen.decision.off": "nowhere"}},
            }
        )
    )
    with pytest.raises(SweepError, match="variant broken"):
        load_sweep(str(bad))
    both = workspace / "both.yaml"
    spec = {"name": "s", "base": "r2.yaml", "variants": {"a": {}}, "grid": {"x": [1]}}
    both.write_text(yaml.safe_dump(spec))
    with pytest.raises(SweepError, match="exactly one of variants"):
        load_sweep(str(both))
    grid = workspace / "grid.yaml"
    grid.write_text(
        yaml.safe_dump(
            {
                "name": "g",
                "base": "r2.yaml",
                "grid": {
                    "stages.clear.decision.threshold": [0.4, 0.6],
                    "protocol.objective": ["accuracy", "f1"],
                },
            },
            sort_keys=False,
        )
    )
    sw = load_sweep(str(grid))
    assert [v.name for v in sw.variants] == ["g01", "g02", "g03", "g04"]
    assert sw.variants[1].raw["protocol"]["objective"] == "f1"


def test_sweep_runs_variants_and_compares_them(workspace):
    os.makedirs(workspace / "sweeps")
    path = workspace / "sweeps" / "screen.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "screen",
                "base": "../r2.yaml",
                "variants": {
                    "base": {},
                    "fixed": {"stages.screen.decision": {"budget": 0.2, "off": "normal"}},
                },
            }
        )
    )
    out = workspace / "runs"
    md, _, _ = run_sweep(str(path), out_root=str(out), bootstrap=20)
    text = Path(md).read_text(encoding="utf-8")
    assert "screen-fixed" in text
    for name in ("base", "fixed"):
        cfg = load(str(out / "screen" / "configs" / f"{name}.yaml"))  # paths still reach the data
        assert cfg.name == f"screen-{name}"
        assert (out / f"screen-{name}" / "report.json").is_file()


def test_cli_compare_and_sweep_dry_run(workspace, r2, capsys):
    path = workspace / "s.yaml"
    path.write_text(yaml.safe_dump({"name": "s", "base": "r2.yaml", "variants": {"a": {}, "b": {}}}))
    assert main(["sweep", str(path), "--dry-run"]) == 0
    assert "2 variants" in capsys.readouterr().out
    assert main(["compare", str(workspace / "nope"), str(workspace / "nope2")]) == 2
    assert "no report.json" in capsys.readouterr().err
