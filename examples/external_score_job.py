# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""An outside training job that produces a score file IDSLab accepts (ExternalScore).

Stand-in for a model trained elsewhere, for example a CNN on a GPU machine. The steps are the same
for any model:

1. ``idslab export-splits config.yaml --out splits`` writes the rows of every split per seed.
2. This job trains on exactly the FIT rows of one seed and scores VAL and TEST with that model.
   With ``--oof`` it also scores every FIT row from a model that did not see it, fold by fold,
   which a stage needs when its scores feed another stage (``extra_features``, ``hard``).
3. It writes ``<out>.parquet`` and the sidecar ``<out>.parquet.json``.
4. The config points a candidate at the file: ``{external: {path: scores/mlp_{seed}.parquet}}``.

Example (the synthetic data written by ``idslab example demo``):

    python examples/external_score_job.py --splits splits/r1-router-gate/splits_seed0.parquet \\
        --train demo/data/train.parquet --test demo/data/test.parquet \\
        --out scores/mlp_0.parquet --oof

Row ids follow IDSLab's default (``train:<n>``, ``test:<n>``); with ``dataset.id_column`` set,
index the data by that column instead.
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd
from sklearn.compose import make_column_selector, make_column_transformer
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

FIT = ("FIT-base", "CAL")


def load(train: str, test: str | None) -> pd.DataFrame:
    frames = []
    for prefix, path in (("train", train), ("test", test)):
        if path is None:
            continue
        f = pd.read_parquet(path) if path.endswith((".parquet", ".pq")) else pd.read_csv(path)
        f.index = [f"{prefix}:{i}" for i in range(len(f))]
        frames.append(f)
    return pd.concat(frames)


def model(categorical: list[str], seed: int):
    prep = make_column_transformer(
        (OneHotEncoder(handle_unknown="ignore"), categorical),
        (StandardScaler(), make_column_selector(dtype_include="number")),
    )
    return make_pipeline(
        prep, MLPClassifier(hidden_layer_sizes=(32,), early_stopping=True, max_iter=500, random_state=seed)
    )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Train outside IDSLab and write an external score file.")
    p.add_argument("--splits", required=True, help="a splits_seed<s>.parquet from idslab export-splits")
    p.add_argument("--train", required=True)
    p.add_argument("--test")
    p.add_argument("--out", required=True, help="score file to write (.parquet)")
    p.add_argument("--label", default="label")
    p.add_argument("--drop", nargs="*", default=["attack_cat"], help="columns that are not features")
    p.add_argument("--categorical", nargs="*", default=["proto", "service"])
    p.add_argument("--oof", action="store_true", help="also write out-of-fold scores for FIT rows")
    args = p.parse_args(argv)

    splits = pd.read_parquet(args.splits)
    with open(args.splits + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    data = load(args.train, args.test)
    X = data.drop(columns=[args.label, *args.drop])
    y = data[args.label]
    seed = int(meta["seed"])

    fit_rows = splits[splits["split"].isin(FIT)]
    fitted = model(args.categorical, seed).fit(X.loc[fit_rows["row_id"]], y.loc[fit_rows["row_id"]])
    scored = splits[splits["split"].isin(("VAL", "TEST"))]
    parts = [scored.assign(score_kind="fitted", score=fitted.predict_proba(X.loc[scored["row_id"]])[:, 1])]

    if args.oof:
        oof = np.zeros(len(fit_rows))
        for fold in sorted(set(fit_rows["oof_fold"].tolist())):
            held = (fit_rows["oof_fold"] == fold).to_numpy()
            ids = fit_rows.loc[~held, "row_id"]
            m = model(args.categorical, seed).fit(X.loc[ids], y.loc[ids])
            oof[held] = m.predict_proba(X.loc[fit_rows.loc[held, "row_id"]])[:, 1]
        parts.append(fit_rows.assign(score_kind="oof", score=oof))

    out = pd.concat(parts, ignore_index=True)[["row_id", "split", "score_kind", "score"]]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    out.to_parquet(args.out, index=False)
    sidecar = {
        "seed": seed,
        "family": meta["family"],
        "split_fingerprint": meta["split_fingerprint"],
        "producer": {"config_hash": meta["config_hash"], "code_version": "mlp-1", "trained_on": "fit"},
    }
    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)
    print(f"wrote {args.out} ({len(out)} rows) and its sidecar")


if __name__ == "__main__":
    main()
