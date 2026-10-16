# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Scores made outside IDSLab (for example a CNN trained on a GPU), DESIGN.md section 4.4.

Round trip: ``export_splits`` writes, per run unit, the row ids of every split, the
out-of-fold fold of each FIT row and a split fingerprint. The external job trains
on exactly those rows and writes one score file per unit:

* Parquet columns ``row_id``, ``split`` (FIT-base, CAL, VAL, TEST), ``score_kind``
  (``oof`` or ``fitted``), and the scores: ``score`` (P(attack) for a classifier,
  higher = more anomalous for a detector) or ``proba__<type>`` per type.
* JSON sidecar ``<file>.json``: ``seed``, ``family`` (null outside held-out mode),
  ``split_fingerprint``, and ``producer`` with ``config_hash``, ``code_version`` and
  ``trained_on`` (``fit_base`` or ``fit``).

A file is used only if every rule holds; any mismatch is an error naming the field.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from idslab.config import Candidate, Stage
from idslab.data import Splits, Table
from idslab.features import fold_ids

SPLIT_NAMES = ("fit_base", "FIT-base"), ("cal", "CAL"), ("val", "VAL"), ("test", "TEST")


class ExternalScoreError(ValueError):
    """A score file does not meet the provenance contract."""


def split_frame(table: Table, split: Splits, *, folds: int, seed: int) -> pd.DataFrame:
    """Row ids per split, with the out-of-fold fold of every FIT row (-1 for VAL and TEST)."""
    fold = np.full(len(table), -1)
    strata = table.types[split.fit] if table.types is not None else table.y[split.fit]
    fold[split.fit] = fold_ids(strata, folds, seed)
    parts = []
    for attr, name in SPLIT_NAMES:
        rows = getattr(split, attr)
        parts.append(pd.DataFrame({"row_id": table.row_id[rows], "split": name, "oof_fold": fold[rows]}))
    return pd.concat(parts, ignore_index=True)


def write_split_export(path: str, frame: pd.DataFrame, meta: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    frame.to_parquet(path, index=False)
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


@dataclass
class ExternalContext:
    """What a score file is checked against."""

    base_dir: str
    seed: int
    family: str | None
    split_fingerprint: str
    feeds_others: bool  # the stage's scores are features or hard-row bands of another stage


def resolve(template: str, ctx: ExternalContext) -> str:
    path = template.format(seed=ctx.seed, family=ctx.family or "")
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(ctx.base_dir, path))


class ExternalModel:
    """Scores read from a checked file; looked up by table row position (the frame index)."""

    def __init__(self, kind: str, classes: tuple[str, ...] | None, fitted: np.ndarray, oof: np.ndarray):
        self.kind = kind
        self.idslab_contract = {"classifier": "AttackScore", "multiclass": "ClassProba"}.get(
            kind, "AnomalyScore"
        )
        self.classes_ = np.asarray(classes or (), dtype=object)
        self.fitted = fitted
        self.oof_values = oof

    def _take(self, values: np.ndarray, rows: np.ndarray, what: str) -> np.ndarray:
        out = values[rows]
        if np.isnan(out).any():
            raise ExternalScoreError(
                f"the score file has no {what} score for {int(np.isnan(out).any(axis=1).sum())} rows"
            )
        return out

    def attack_score(self, X: pd.DataFrame) -> np.ndarray:
        return self._take(self.fitted, X.index.to_numpy(), "fitted")[:, 0]

    def anomaly_score(self, X: pd.DataFrame) -> np.ndarray:
        return self._take(self.fitted, X.index.to_numpy(), "fitted")[:, 0]

    def class_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self._take(self.fitted, X.index.to_numpy(), "fitted")

    def oof(self, rows: np.ndarray) -> np.ndarray:
        return self._take(self.oof_values, rows, "out-of-fold")


def load_external(
    stage: Stage, cand: Candidate, table: Table, split: Splits, ctx: ExternalContext
) -> tuple[ExternalModel, dict]:
    """Read and check one score file; returns the model and the producer block."""
    if cand.external is None:
        raise ExternalScoreError(f"{cand.label} is not an external candidate")
    path = resolve(cand.external, ctx)
    where = f"stage {stage.name} {cand.label} ({path})"
    if not os.path.isfile(path) or not os.path.isfile(path + ".json"):
        raise ExternalScoreError(f"{where}: score file or its .json sidecar is missing")
    with open(path + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    for key, expected in (
        ("seed", ctx.seed),
        ("family", ctx.family),
        ("split_fingerprint", ctx.split_fingerprint),
    ):
        if meta.get(key) != expected:
            raise ExternalScoreError(
                f"{where}: sidecar {key} is {meta.get(key)!r}, this run unit needs {expected!r}"
            )
    producer = meta.get("producer")
    if not isinstance(producer, dict) or not {"config_hash", "code_version", "trained_on"} <= set(producer):
        raise ExternalScoreError(f"{where}: sidecar producer needs config_hash, code_version and trained_on")
    if producer["trained_on"] not in ("fit_base", "fit"):
        raise ExternalScoreError(f"{where}: producer.trained_on must be fit_base or fit")
    if stage.calibrate != "none" and producer["trained_on"] != "fit_base":
        raise ExternalScoreError(
            f"{where}: a calibrated stage needs a model trained on fit_base (CAL stays unseen)"
        )

    df = pd.read_parquet(path)
    for col in ("row_id", "split", "score_kind"):
        if col not in df.columns:
            raise ExternalScoreError(f"{where}: column {col!r} is missing")
    if stage.kind == "multiclass":
        cols = [c for c in df.columns if str(c).startswith("proba__")]
        if not cols:
            raise ExternalScoreError(f"{where}: a type stage needs proba__<type> columns")
        classes: tuple[str, ...] | None = tuple(str(c)[len("proba__") :] for c in cols)
    else:
        if "score" not in df.columns:
            raise ExternalScoreError(f"{where}: column 'score' is missing")
        cols, classes = ["score"], None
    if set(df["score_kind"]) - {"oof", "fitted"}:
        raise ExternalScoreError(f"{where}: score_kind must be oof or fitted")
    if df.duplicated(["row_id", "score_kind"]).any():
        raise ExternalScoreError(f"{where}: a row_id appears twice with the same score_kind")

    position = {rid: i for i, rid in enumerate(table.row_id.tolist())}
    expected_split = np.full(len(table), "", dtype=object)
    for attr, name in SPLIT_NAMES:
        expected_split[getattr(split, attr)] = name
    pos = df["row_id"].astype(str).map(position)
    if pos.isna().any():
        raise ExternalScoreError(f"{where}: {int(pos.isna().sum())} row_ids are not in the dataset")
    pos_arr = pos.to_numpy(dtype=int)
    if (expected_split[pos_arr] == "").any():
        raise ExternalScoreError(f"{where}: rows outside this run unit's splits")
    if (expected_split[pos_arr] != df["split"].astype(str).to_numpy()).any():
        raise ExternalScoreError(f"{where}: the split column disagrees with the exported splits")
    values = df[cols].to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ExternalScoreError(f"{where}: scores must be finite")
    if stage.kind in ("classifier", "multiclass") and (values.min() < 0 or values.max() > 1):
        raise ExternalScoreError(f"{where}: probabilities must lie in [0, 1]")

    kind = df["score_kind"].to_numpy()
    fitted = np.full((len(table), len(cols)), np.nan)
    oof = np.full((len(table), len(cols)), np.nan)
    fitted[pos_arr[kind == "fitted"]] = values[kind == "fitted"]
    oof[pos_arr[kind == "oof"]] = values[kind == "oof"]
    on_fit_rows = np.isin(
        pos_arr[kind == "fitted"], split.fit_base if producer["trained_on"] == "fit_base" else split.fit
    )
    if on_fit_rows.any():
        raise ExternalScoreError(
            f"{where}: fitted scores on rows the model was trained on (use score_kind oof)"
        )
    if (kind == "oof").any() and not np.isin(pos_arr[kind == "oof"], split.fit).all():
        raise ExternalScoreError(f"{where}: out-of-fold scores are allowed on FIT rows only")
    need = {"VAL": split.val, "TEST": split.test}
    if stage.calibrate != "none":
        need["CAL"] = split.cal
    for name, rows in need.items():
        if np.isnan(fitted[rows]).any():
            raise ExternalScoreError(f"{where}: fitted scores are missing for {name} rows")
    if ctx.feeds_others and np.isnan(oof[split.fit]).any():
        raise ExternalScoreError(f"{where}: this stage feeds others, so every FIT row needs an oof score")
    return ExternalModel(stage.kind, classes, fitted, oof), producer
