# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Finished units are reused across releases while the results version stays the same."""

import json
import shutil

import pytest
import yaml

from idslab import __version__
from idslab.config import load
from idslab.report import write_report
from idslab.results_version import RESULTS_VERSION, normalized, results_version
from idslab.runner import run


@pytest.fixture
def finished(tmp_path, data_dir, r2):
    (tmp_path / "data").mkdir()
    for name in ("train.parquet", "test.parquet"):
        shutil.copyfile(data_dir / name, tmp_path / "data" / name)
    r2["dataset"]["train"] = "data/train.parquet"
    r2["dataset"]["test"] = "data/test.parquet"
    (tmp_path / "r2.yaml").write_text(yaml.safe_dump(r2))
    cfg = load(str(tmp_path / "r2.yaml"))
    run(cfg, out_root=str(tmp_path / "runs"))
    return tmp_path, cfg


def _rewrite_done(root, change):
    done = root / "runs" / "r2" / "standard" / "seed_0" / "DONE"
    data = json.loads(done.read_text())
    change(data)
    done.write_text(json.dumps(data))
    return data


def test_done_records_the_results_version_and_the_release(finished):
    root, _ = finished
    data = _rewrite_done(root, lambda d: None)
    assert data["fingerprints"]["results_version"] == RESULTS_VERSION
    assert "package_version" not in data["fingerprints"]  # a release alone never invalidates a unit
    assert data["idslab_version"] == __version__


def test_a_unit_from_0_3_0_is_reused_and_reported(finished):
    root, cfg = finished

    def as_0_3_0(d):  # how 0.3.0 wrote DONE
        d["fingerprints"].pop("results_version")
        d["fingerprints"]["package_version"] = "0.3.0"
        d.pop("idslab_version")

    _rewrite_done(root, as_0_3_0)
    [r] = run(cfg, out_root=str(root / "runs"))
    assert r["skipped"]
    md, _ = write_report(str(root / "runs"), cfg)
    assert md.endswith("report.md")


def test_a_unit_of_an_older_evaluation_runs_again(finished):
    root, cfg = finished
    _rewrite_done(root, lambda d: d["fingerprints"].update(results_version="0"))
    with pytest.raises(FileNotFoundError, match="no finished seeds"):
        write_report(str(root / "runs"), cfg)
    [r] = run(cfg, out_root=str(root / "runs"))
    assert not r["skipped"]


def test_a_damaged_done_file_reruns_the_unit(finished):
    root, cfg = finished
    _rewrite_done(root, lambda d: d.update(fingerprints=["damaged"]))
    with pytest.raises(FileNotFoundError, match="no finished seeds"):  # the report skips it
        write_report(str(root / "runs"), cfg)
    [r] = run(cfg, out_root=str(root / "runs"))  # and a run redoes it instead of failing
    assert not r["skipped"]


def test_unknown_versions_never_match():
    assert results_version({"package_version": "0.1.0"}) is None
    assert results_version({}) is None
    assert normalized({"package_version": "0.2.0", "config": "x"}) == {"config": "x", "results_version": "1"}
