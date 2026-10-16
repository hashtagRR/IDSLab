# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""A model that is not a scikit-learn estimator, used in a stage through a small adapter class.

The class names its score contract in ``idslab_contract`` and implements the methods that
contract needs. IDSLab checks the output (one probability per row for AttackScore) on every call.

Use it in a config (the module must be importable, e.g. ``PYTHONPATH=examples idslab run ...``):

    candidates:
      - class: custom_adapter.ZScoreRule
        params: {columns: [x0, x1, x2]}

A custom adapter does its own preprocessing, so its stage has no ``pipeline`` and no
``calibrate``; it cannot be searched either (list each setting as its own candidate).
"""

from __future__ import annotations

import numpy as np


class ZScoreRule:
    """AttackScore from a hand-written rule: how far a flow lies from the benign training mean.

    ``fit`` learns the benign mean and spread of the chosen columns; ``attack_score`` maps the
    mean absolute z-score of a flow to a probability with a logistic curve.
    """

    idslab_contract = "AttackScore"

    def __init__(self, columns=("x0",), scale=1.0):
        self.columns = list(columns)
        self.scale = float(scale)

    def fit(self, X, y):
        benign = X.loc[np.asarray(y) == 0, self.columns].to_numpy(dtype=float)
        self.mean_ = benign.mean(axis=0)
        self.std_ = benign.std(axis=0) + 1e-9
        return self

    def attack_score(self, X):
        z = np.abs((X[self.columns].to_numpy(dtype=float) - self.mean_) / self.std_).mean(axis=1)
        return 1.0 / (1.0 + np.exp(-(z - 1.0) * self.scale))
