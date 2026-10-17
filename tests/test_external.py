# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import json

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier

from idslab.config import from_dict
from idslab.data import load_table, make_splits
from idslab.external import ExternalScoreError
from idslab.report import write_report
from idslab.runner import export_splits, run


def outside_job(
    data_dir, export_path, out_path, *, trained_on="fit", oof=True, meta_changes=None, mutate=None
):
    """What a GPU job would do: read the exported splits, train on them, write a score file."""
    splits = pd.read_parquet(export_path)
    with open(export_path + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    train = pd.read_parquet(data_dir / "train.parquet")
    test = pd.read_parquet(data_dir / "test.parquet")
    train.index = [f"train:{i}" for i in range(len(train))]
    test.index = [f"test:{i}" for i in range(len(test))]
    frame = pd.concat([train, test])
    X = frame[[c for c in frame.columns if c.startswith("x")]]
    y = frame["label"]
    fit_names = ("FIT-base",) if trained_on == "fit_base" else ("FIT-base", "CAL")
    fit_ids = splits.loc[splits["split"].isin(fit_names), "row_id"]
    model = RandomForestClassifier(n_estimators=30, random_state=0).fit(X.loc[fit_ids], y.loc[fit_ids])
    rows = []
    scored = ("VAL", "TEST") if trained_on == "fit" else ("CAL", "VAL", "TEST")
    part = splits[splits["split"].isin(scored)]
    fitted = model.predict_proba(X.loc[part["row_id"]])[:, 1]
    rows.append(part.assign(score_kind="fitted", score=fitted))
    if oof:
        fit_part = splits[splits["split"].isin(("FIT-base", "CAL"))]
        oof_scores = np.zeros(len(fit_part))
        for f in sorted(set(fit_part["oof_fold"].tolist())):
            held = (fit_part["oof_fold"] == f).to_numpy()
            ids_train = fit_part.loc[~held, "row_id"]
            m = RandomForestClassifier(n_estimators=30, random_state=0).fit(
                X.loc[ids_train], y.loc[ids_train]
            )
            oof_scores[held] = m.predict_proba(X.loc[fit_part.loc[held, "row_id"]])[:, 1]
        rows.append(fit_part.assign(score_kind="oof", score=oof_scores))
    out = pd.concat(rows, ignore_index=True)[["row_id", "split", "score_kind", "score"]]
    if mutate:
        out = mutate(out)
    out.to_parquet(out_path, index=False)
    side = {
        "seed": meta["seed"],
        "family": meta["family"],
        "split_fingerprint": meta["split_fingerprint"],
        "producer": {"config_hash": "abc", "code_version": "cnn-1", "trained_on": trained_on},
    }
    side.update(meta_changes or {})
    with open(out_path + ".json", "w", encoding="utf-8") as f:
        json.dump(side, f)


@pytest.fixture
def ext_cfg(data_dir, r1, tmp_path):
    r1["stages"]["router"]["candidates"] = [{"external": {"path": str(tmp_path / "router_{seed}.parquet")}}]
    return from_dict(r1, base_dir=str(data_dir))


def test_export_matches_the_run_splits(data_dir, ext_cfg, tmp_path):
    [path] = export_splits(ext_cfg, str(tmp_path / "splits"))
    frame = pd.read_parquet(path)
    with open(path + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    table = load_table(ext_cfg)
    split = make_splits(table, ext_cfg.protocol, None, seed=0)
    assert meta["split_fingerprint"] == split.fingerprint(table.row_id)
    assert set(frame["split"]) == {"FIT-base", "CAL", "VAL", "TEST"}
    assert (frame.loc[frame["split"].isin(["VAL", "TEST"]), "oof_fold"] == -1).all()
    assert set(frame.loc[frame["split"].isin(["FIT-base", "CAL"]), "oof_fold"]) == {0, 1, 2}
    assert len(frame) == len(table)


def test_external_scores_run_and_carry_provenance(data_dir, ext_cfg, tmp_path):
    [path] = export_splits(ext_cfg, str(tmp_path / "splits"))
    outside_job(data_dir, path, str(tmp_path / "router_0.parquet"), oof=False)
    out = str(tmp_path / "runs")
    [res] = run(ext_cfg, out_root=out)
    info = res["summary"]["candidates"]["router"][0]
    assert info["class"] == "external"
    assert info["external_producer"]["code_version"] == "cnn-1"
    [again] = run(ext_cfg, out_root=out)
    assert again["skipped"] is True
    outside_job(data_dir, path, str(tmp_path / "router_0.parquet"), oof=False, mutate=lambda d: d.iloc[::-1])
    [changed] = run(ext_cfg, out_root=out)
    assert changed["skipped"] is False  # a changed score file reruns the unit


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"meta_changes": {"seed": 5}}, "sidecar seed"),
        ({"meta_changes": {"split_fingerprint": "x"}}, "sidecar split_fingerprint"),
        ({"meta_changes": {"producer": {"code_version": "1"}}}, "producer needs"),
        ({"mutate": lambda d: d[d["split"] != "VAL"]}, "missing for VAL rows"),
        ({"mutate": lambda d: d.assign(score=d["score"] + 2)}, "lie in [0, 1]"),
        ({"mutate": lambda d: pd.concat([d, d.iloc[:1]])}, "appears twice"),
        ({"mutate": lambda d: d.assign(split="VAL")}, "split column disagrees"),
        ({"mutate": lambda d: d.drop(columns=["score_kind"])}, "'score_kind' is missing"),
    ],
)
def test_contract_violations_are_rejected(data_dir, ext_cfg, tmp_path, kwargs, message):
    [path] = export_splits(ext_cfg, str(tmp_path / "splits"))
    outside_job(data_dir, path, str(tmp_path / "router_0.parquet"), oof=False, **kwargs)
    with pytest.raises(ExternalScoreError, match=message.replace("[", r"\[").replace("]", r"\]")):
        run(ext_cfg, out_root=str(tmp_path / "runs"))


def test_fitted_scores_on_training_rows_are_rejected(data_dir, ext_cfg, tmp_path):
    [path] = export_splits(ext_cfg, str(tmp_path / "splits"))

    def leak(d):
        fit_rows = d[d["score_kind"] == "oof"].assign(score_kind="fitted")
        return pd.concat([d[d["score_kind"] == "fitted"], fit_rows])

    outside_job(data_dir, path, str(tmp_path / "router_0.parquet"), oof=True, mutate=leak)
    with pytest.raises(ExternalScoreError, match="rows the model was trained on"):
        run(ext_cfg, out_root=str(tmp_path / "runs"))


def test_a_feeding_external_stage_needs_oof_scores(data_dir, r4, tmp_path):
    r4["stages"]["router"]["candidates"] = [{"external": str(tmp_path / "router_{seed}.parquet")}]
    cfg = from_dict(r4, base_dir=str(data_dir))
    [path] = export_splits(cfg, str(tmp_path / "splits"))
    outside_job(data_dir, path, str(tmp_path / "router_0.parquet"), oof=False)
    with pytest.raises(ExternalScoreError, match="needs an oof score"):
        run(cfg, out_root=str(tmp_path / "runs"))
    outside_job(data_dir, path, str(tmp_path / "router_0.parquet"), oof=True)
    [res] = run(cfg, out_root=str(tmp_path / "runs2"))
    assert "type" in res["summary"]["frozen_configuration"]


def test_calibrated_external_stage_needs_fit_base_training(data_dir, r1, tmp_path):
    r1["stages"]["router"]["candidates"] = [{"external": str(tmp_path / "router_{seed}.parquet")}]
    r1["stages"]["router"]["calibrate"] = "sigmoid"
    cfg = from_dict(r1, base_dir=str(data_dir))
    [path] = export_splits(cfg, str(tmp_path / "splits"))
    outside_job(data_dir, path, str(tmp_path / "router_0.parquet"), trained_on="fit", oof=False)
    with pytest.raises(ExternalScoreError, match="trained on fit_base"):
        run(cfg, out_root=str(tmp_path / "runs"))
    outside_job(data_dir, path, str(tmp_path / "router_0.parquet"), trained_on="fit_base", oof=False)
    [res] = run(cfg, out_root=str(tmp_path / "runs2"))
    assert res["summary"]["candidates"]["router"][0]["n_cal"] > 0


def test_joint_check_finds_what_greedy_missed_in_r2(data_dir, r2, tmp_path):
    r2["protocol"]["search"] = "joint"
    cfg = from_dict(r2, base_dir=str(data_dir))
    [res] = run(cfg, out_root=str(tmp_path / "runs"))
    c = res["summary"]["controls"]
    jc = c["joint_check"]
    assert jc["val_accuracy_joint"] >= jc["val_accuracy_greedy"]
    assert jc["evaluated"] == 3  # screen: off + 2 budgets; clear is fixed
    assert "joint_check" in c["systems_test"]
    assert "joint_check" in c["bootstrap_cascade_minus"]
    # greedy judged the screen with the classifier after it off; the joint check judges them together
    if jc["differs_from_greedy"]:
        assert jc["val_accuracy_joint"] > jc["val_accuracy_greedy"]
        assert jc["configuration"]["screen"] != res["summary"]["frozen_configuration"]["screen"]


def test_a_changed_sidecar_reruns_the_unit_and_is_checked_again(data_dir, ext_cfg, tmp_path):
    [path] = export_splits(ext_cfg, str(tmp_path / "splits"))
    score_file = str(tmp_path / "router_0.parquet")
    outside_job(data_dir, path, score_file, oof=False)
    out = str(tmp_path / "runs")
    [first] = run(ext_cfg, out_root=out)
    assert first["skipped"] is False
    with open(score_file + ".json", encoding="utf-8") as f:
        side = json.load(f)
    side["seed"] = 7  # only the sidecar changes, and it no longer matches the unit
    with open(score_file + ".json", "w", encoding="utf-8") as f:
        json.dump(side, f)
    with pytest.raises(ExternalScoreError, match="sidecar seed"):
        run(ext_cfg, out_root=out)  # not skipped: the provenance checks run again


def test_reports_leave_out_units_whose_score_files_changed(data_dir, ext_cfg, tmp_path):
    [path] = export_splits(ext_cfg, str(tmp_path / "splits"))
    score_file = str(tmp_path / "router_0.parquet")
    outside_job(data_dir, path, score_file, oof=False)
    out = str(tmp_path / "runs")
    run(ext_cfg, out_root=out)
    write_report(out, ext_cfg)  # fresh: included
    frame = pd.read_parquet(score_file)
    frame["score"] = 1 - frame["score"]
    frame.to_parquet(score_file, index=False)
    with pytest.raises(FileNotFoundError, match="external score files changed"):
        write_report(out, ext_cfg)  # the only unit is stale now
