# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Preprocessing steps that can appear in a stage pipeline.

Every step here is a scikit-learn transformer, so it is fitted on the stage's own
training rows and refitted inside every search fold (DESIGN.md section 2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

UNKNOWN_CODE = -1.0


class Encode(TransformerMixin, BaseEstimator):
    """Ordinal-encode the named categorical columns and pass numeric columns through.

    Categories are learned on the rows passed to ``fit``. A category first seen at
    transform time gets ``UNKNOWN_CODE``. Missing values in a categorical column are
    treated as their own category. Any non-numeric column that is not listed in
    ``categorical`` is an error, so a forgotten column fails at fit time instead of
    being cast silently.
    """

    def __init__(self, categorical=()):
        self.categorical = categorical

    def fit(self, X, y=None):  # noqa: ARG002  (scikit-learn signature)
        frame = _as_frame(X)
        cats = list(self.categorical)
        missing = [c for c in cats if c not in frame.columns]
        if missing:
            raise ValueError(f"Encode: categorical columns not in the data: {missing}")
        other = [c for c in frame.columns if c not in cats]
        bad = [c for c in other if not pd.api.types.is_numeric_dtype(frame[c])]
        if bad:
            raise ValueError(f"Encode: non-numeric columns not listed in 'categorical': {bad}")
        self.columns_ = list(frame.columns)
        self.categories_ = {c: _categories(frame[c]) for c in cats}
        self.codes_ = {c: {v: float(i) for i, v in enumerate(vals)} for c, vals in self.categories_.items()}
        self.n_features_in_ = len(self.columns_)
        return self

    def transform(self, X):
        frame = _as_frame(X)
        if list(frame.columns) != self.columns_:
            raise ValueError("Encode: columns differ from the ones seen in fit")
        out = np.empty((len(frame), len(self.columns_)), dtype=np.float64)
        for j, col in enumerate(self.columns_):
            if col in self.categories_:
                codes = self.codes_[col]
                keys = frame[col].astype(object).where(frame[col].notna(), "__missing__").astype(str)
                out[:, j] = keys.map(codes).fillna(UNKNOWN_CODE).to_numpy(dtype=np.float64)
            else:
                out[:, j] = frame[col].to_numpy(dtype=np.float64, na_value=np.nan)
        return out

    def get_feature_names_out(self, input_features=None):
        del input_features  # scikit-learn interface; the names come from fit
        return np.asarray(self.columns_, dtype=object)


def _as_frame(X) -> pd.DataFrame:
    if isinstance(X, pd.DataFrame):
        return X
    raise TypeError("Encode needs a pandas DataFrame with column names")


def _categories(col: pd.Series) -> list[str]:
    keys = col.astype(object).where(col.notna(), "__missing__").astype(str)
    return sorted(keys.unique().tolist())
