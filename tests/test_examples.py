# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""The scripts in examples/ run as documented."""

import importlib.util
from pathlib import Path

from idslab.config import from_dict, parse_yaml
from idslab.runner import export_splits, run

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_custom_adapter_runs_in_a_stage(data_dir, r1, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLES))
    r1["stages"]["router"].pop("pipeline")
    r1["stages"]["router"]["candidates"] = [
        {"class": "custom_adapter.ZScoreRule", "params": {"columns": ["x0", "x1", "x2"]}}
    ]
    cfg = from_dict(r1, base_dir=str(data_dir))
    [res] = run(cfg, out_root=str(tmp_path / "runs"))
    [info] = res["summary"]["candidates"]["router"]
    assert info["class"] == "custom_adapter.ZScoreRule"


def test_external_score_job_round_trip(data_dir, r4, tmp_path):
    # the router's score feeds the type stage, so the job must also write out-of-fold scores
    r4["stages"]["router"]["candidates"] = [{"external": {"path": str(tmp_path / "mlp_{seed}.parquet")}}]
    cfg = from_dict(r4, base_dir=str(data_dir))
    [splits] = export_splits(cfg, str(tmp_path / "splits"))
    job = load_module("external_score_job")
    job.main(
        [
            "--splits", splits,
            "--train", str(data_dir / "train.parquet"),
            "--test", str(data_dir / "test.parquet"),
            "--out", str(tmp_path / "mlp_0.parquet"),
            "--oof",
        ]
    )  # fmt: skip
    [res] = run(cfg, out_root=str(tmp_path / "runs"))
    [info] = res["summary"]["candidates"]["router"]
    assert info["external_producer"]["code_version"] == "mlp-1"


def test_readme_config_is_valid():
    text = (EXAMPLES.parent / "README.md").read_text(encoding="utf-8")
    block = text.split("```yaml\n", 1)[1].split("```", 1)[0]
    cfg = from_dict(parse_yaml(block))
    assert cfg.order() == ["binary", "router", "gate"]
