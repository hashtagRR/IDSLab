# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import os
from importlib import resources

import pytest
import yaml

from idslab.cli import EXAMPLES, main
from idslab.config import load


@pytest.mark.parametrize("name", EXAMPLES)
def test_shipped_examples_are_valid(name):
    with resources.as_file(resources.files("idslab.examples") / name) as path:
        cfg = load(str(path))
    assert cfg.stages


def test_validate_prints_order(tmp_path, r1, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(r1))
    assert main(["validate", str(path)]) == 0
    assert "binary > router > gate" in capsys.readouterr().out


def test_bad_config_exits_2_with_message(tmp_path, r1, capsys):
    del r1["stages"]["router"]["decision"]["off"]
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(r1))
    assert main(["validate", str(path)]) == 2
    assert "'off' is required" in capsys.readouterr().err


def test_run_and_report(tmp_path, r2, data_dir, capsys):
    r2["dataset"]["train"] = str(data_dir / "train.parquet")
    r2["dataset"]["test"] = str(data_dir / "test.parquet")
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(r2))
    out = str(tmp_path / "runs")
    assert main(["run", str(path), "--out", out]) == 0
    text = capsys.readouterr().out
    assert "seed 0:" in text
    assert os.path.isfile(os.path.join(out, "r2", "report.md"))
    assert main(["report", str(path), "--out", out]) == 0


def test_example_writes_data_and_configs(tmp_path, capsys):
    target = tmp_path / "ex"
    assert main(["example", str(target)]) == 0
    for name in EXAMPLES:
        assert (target / name).is_file()
        load(str(target / name))
    assert (target / "data" / "train.parquet").is_file()
    before = (target / "data" / "train.parquet").stat().st_mtime_ns
    (target / EXAMPLES[0]).unlink()
    assert main(["example", str(target)]) == 2  # data files exist: nothing is written
    assert "already present" in capsys.readouterr().err
    assert not (target / EXAMPLES[0]).exists()
    assert (target / "data" / "train.parquet").stat().st_mtime_ns == before
    assert main(["example", str(target), "--overwrite"]) == 0


def test_runtime_errors_are_messages_unless_debug(tmp_path, r2, data_dir, capsys, monkeypatch):
    from idslab import cli  # noqa: PLC0415

    def boom(*_args, **_kwargs):
        raise RuntimeError("stage gate: something went wrong")

    monkeypatch.setattr(cli, "run", boom)
    r2["dataset"]["train"] = str(data_dir / "train.parquet")
    r2["dataset"]["test"] = str(data_dir / "test.parquet")
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(r2))
    assert main(["run", str(path)]) == 2
    assert "something went wrong" in capsys.readouterr().err
    with pytest.raises(RuntimeError):
        main(["--debug", "run", str(path)])
