# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Scores of upstream stages used as features by later stages (DESIGN.md section 4.2).

The store keeps, for every score source and every row of the table, the score a
dependent stage may see: an out-of-fold score for FIT rows (from a model that
never saw the row) and the fitted model's score for VAL and TEST rows. TEST rows
are filled only after the configuration is frozen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold

RARE = "__rare__"


class FeatureError(RuntimeError):
    """A stage asked for a source score that has not been computed for those rows."""


def fold_ids(labels: np.ndarray, k: int, seed: int) -> np.ndarray:
    """Fold index (0..k-1) per row, stratified on ``labels``.

    Labels with fewer than ``k`` rows are merged into one rare stratum; if that is
    still smaller than ``k`` its rows join the largest stratum.
    """
    lab = np.asarray(labels).astype(str).astype(object)
    values, counts = np.unique(lab.astype(str), return_counts=True)
    small = values[counts < k]
    if len(small):
        lab[np.isin(lab.astype(str), small)] = RARE
        if int((lab == RARE).sum()) < k:
            lab[lab == RARE] = values[np.argmax(counts)]
    out = np.empty(len(lab), dtype=int)
    for f, (_, held) in enumerate(
        StratifiedKFold(k, shuffle=True, random_state=seed).split(lab, lab.astype(str))
    ):
        out[held] = f
    return out


class FeatureStore:
    """Per source stage: one score column (or one per type for a multiclass source) for every row."""

    def __init__(self, X: pd.DataFrame):
        self.X = X
        self.n = len(X)
        self.columns: dict[str, list[str]] = {}
        self.values: dict[str, np.ndarray] = {}

    def has(self, src: str) -> bool:
        return src in self.values

    def declare(self, src: str, names: list[str]) -> None:
        if src not in self.values:
            self.columns[src] = names
            self.values[src] = np.full((self.n, len(names)), np.nan)

    def put(self, src: str, rows: np.ndarray, values: np.ndarray) -> None:
        v = np.asarray(values, dtype=np.float64)
        self.values[src][rows] = v.reshape(len(rows), -1)

    def scalar(self, src: str, rows: np.ndarray) -> np.ndarray:
        """One-column source score for ``rows`` (used for hard-row bands)."""
        v = self.values[src][rows]
        if v.shape[1] != 1:
            raise FeatureError(f"source {src} gives several columns; a band needs a single score")
        if np.isnan(v).any():
            raise FeatureError(f"source {src} has no score yet for {int(np.isnan(v).any(axis=1).sum())} rows")
        return v[:, 0]

    def frame(self, sources: tuple[str, ...], rows: np.ndarray) -> pd.DataFrame:
        """Feature rows for a stage: the raw columns plus each source's score columns."""
        X = self.X.iloc[rows]
        if not sources:
            return X
        extra = {}
        for src in sources:
            if src not in self.values:
                raise FeatureError(f"source {src} has not been scored yet")
            block = self.values[src][rows]
            if np.isnan(block).any():
                missing = int(np.isnan(block).any(axis=1).sum())
                raise FeatureError(f"source {src} has no score for {missing} of the requested rows")
            for j, name in enumerate(self.columns[src]):
                extra[name] = block[:, j]
        return X.assign(**extra)


def column_names(src: str, classes: tuple[str, ...] | None) -> list[str]:
    if classes is None:
        return [f"score_{src}"]
    return [f"score_{src}__{c}" for c in classes]
