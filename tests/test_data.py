# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import numpy as np
import pandas as pd
import pytest

from idslab.config import from_dict
from idslab.data import DataError, load_table, make_splits

RF = "sklearn.ensemble.RandomForestClassifier"


def frame(n, seed, rare=False):
    rng = np.random.default_rng(seed)
    types = rng.choice(["Normal", "DoS", "Fuzzers", "Recon"], size=n, p=[0.6, 0.2, 0.1, 0.1]).astype(object)
    if rare:
        types[0] = "Worms"
    return pd.DataFrame(
        {
            "id": np.arange(n) + seed * 100000,
            "x1": rng.normal(size=n),
            "proto": rng.choice(["tcp", "udp"], size=n),
            "attack_cat": types,
            "label": (types != "Normal").astype(int),
        }
    )


def config(tmp_path, **dataset):
    ds: dict[str, object] = {
        "train": "train.parquet",
        "test": "test.parquet",
        "type": "attack_cat",
        "label": "label",
    }
    ds.update(dataset)
    for k in [k for k, v in ds.items() if v is None]:
        del ds[k]
    return from_dict(
        {
            "name": "d",
            "dataset": ds,
            "entry": "b",
            "stages": {
                "b": {
                    "kind": "classifier",
                    "candidates": [{"class": RF}],
                    "decision": {"threshold": 0.5, "off": "benign"},
                    "routes": {"attack": "ATTACK", "benign": "BENIGN"},
                }
            },
        },
        base_dir=str(tmp_path),
    )


@pytest.fixture
def files(tmp_path):
    frame(400, 1, rare=True).to_parquet(tmp_path / "train.parquet")
    frame(200, 2).to_parquet(tmp_path / "test.parquet")
    return tmp_path


def test_label_type_map_and_drop(files):
    cfg = config(files, type_map={"Fuzzers": "Scanning", "Recon": "Scanning"}, drop=["id"])
    t = load_table(cfg)
    assert len(t) == 600
    assert list(t.X.columns) == ["x1", "proto"]
    assert set(np.unique(t.types)) == {"Normal", "DoS", "Scanning", "Worms"}
    assert t.from_test_file.sum() == 200
    assert t.row_id[0] == "train:0"
    assert t.row_id[400] == "test:0"


def test_label_derived_from_type(files):
    cfg = config(files, label=None)
    t = load_table(cfg)
    np.testing.assert_array_equal(t.y, (t.types != "Normal").astype(np.int8))


def test_bad_label_values_and_missing_types(tmp_path):
    df = frame(50, 3)
    df["label"] = df["label"] * 2
    df.to_parquet(tmp_path / "train.parquet")
    df.to_parquet(tmp_path / "test.parquet")
    with pytest.raises(DataError, match="must hold 0/1"):
        load_table(config(tmp_path))
    df = frame(50, 3)
    df.loc[3, "attack_cat"] = None
    df.to_parquet(tmp_path / "train.parquet")
    frame(50, 5).to_parquet(tmp_path / "test.parquet")
    with pytest.raises(DataError, match="type_fill"):
        load_table(config(tmp_path))
    t = load_table(config(tmp_path, type_fill="Normal"))
    assert t.types is not None
    assert t.types[3] == "Normal"


def test_column_mismatch_and_duplicate_ids(tmp_path):
    frame(50, 1).to_parquet(tmp_path / "train.parquet")
    frame(50, 2).drop(columns=["x1"]).to_parquet(tmp_path / "test.parquet")
    with pytest.raises(DataError, match="columns differ"):
        load_table(config(tmp_path))
    frame(50, 1).to_parquet(tmp_path / "test.parquet")
    with pytest.raises(DataError, match="not unique"):
        load_table(config(tmp_path, id_column="id"))


def test_splits_are_disjoint_complete_and_seeded(files):
    cfg = config(files, drop=["id"])
    t = load_table(cfg)
    s = make_splits(t, cfg.protocol, None, seed=0)
    parts = [s.fit_base, s.cal, s.val, s.test]
    joined = np.concatenate(parts)
    assert len(joined) == len(set(joined.tolist())) == 600
    np.testing.assert_array_equal(np.sort(s.test), np.arange(400, 600))
    assert abs(len(s.val) - 80) <= 1
    assert abs(len(s.cal) - 64) <= 1
    np.testing.assert_array_equal(s.fit, np.sort(np.concatenate([s.fit_base, s.cal])))
    again = make_splits(t, cfg.protocol, None, seed=0)
    np.testing.assert_array_equal(again.val, s.val)
    assert s.fingerprint(t.row_id) == again.fingerprint(t.row_id)
    other = make_splits(t, cfg.protocol, None, seed=1)
    assert not np.array_equal(other.val, s.val)
    assert other.fingerprint(t.row_id) != s.fingerprint(t.row_id)


def test_rare_type_is_merged_and_logged(files):
    cfg = config(files)
    s = make_splits(load_table(cfg), cfg.protocol, None, seed=0)
    assert any("Worms" in line for line in s.log)


def test_test_fraction_and_subsample(tmp_path):
    frame(500, 4).to_parquet(tmp_path / "all.parquet")
    raw = {"train": "all.parquet", "test": None, "test_fraction": 0.3}
    cfg = config(tmp_path, **raw)
    t = load_table(cfg)
    s = make_splits(t, cfg.protocol, cfg.dataset.test_fraction, seed=0)
    assert len(s.test) == 150
    assert not t.from_test_file.any()
    raw2 = {**cfg.raw, "protocol": {"subsample": {"train": 100, "test": 40}}}
    cfg2 = from_dict(raw2, base_dir=str(tmp_path))
    s2 = make_splits(t, cfg2.protocol, cfg2.dataset.test_fraction, seed=0)
    assert len(s2.test) == 40
    assert len(s2.fit_base) + len(s2.cal) + len(s2.val) == 100
    assert any("labels not used" in line for line in s2.log)


def test_normal_type_must_occur(files):
    with pytest.raises(DataError, match="normal_type 'normal'"):
        load_table(config(files, normal_type="normal"))
