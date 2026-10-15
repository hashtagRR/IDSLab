# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Fit a stage's candidates on their training rows (DESIGN.md section 4.1).

Classifiers: calibrated stages fit on FIT-base and calibrate on CAL; uncalibrated
stages fit on all of FIT. ``train_on`` filters those rows by type. Detectors fit
on FIT (``fit_on: fit``) or on its benign rows (``fit_on: benign``). A search,
when given, runs on the training rows only with the whole pipeline refitted in
every fold, scored by ROC AUC.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.model_selection import GridSearchCV, RandomizedSearchCV, StratifiedKFold

from idslab.adapters import anomaly_score, attack_score, build_estimator, build_pipeline, is_custom
from idslab.config import Candidate, Stage
from idslab.data import Splits, Table


class FitError(RuntimeError):
    """A candidate cannot be fitted on the rows the protocol gives it."""


@dataclass
class Fitted:
    stage: str
    index: int
    label: str
    kind: str
    cls: str
    model: Any = field(repr=False)
    score_override: dict | None
    n_train: int
    n_cal: int
    attack_share: float | None
    prior_share: float | None  # attack share the output probabilities encode (CAL if calibrated)
    best_params: dict
    cv_auc: float | None
    seconds: float

    def score(self, X: Any) -> np.ndarray:
        if self.kind == "classifier":
            return attack_score(self.model, X)
        return anomaly_score(self.model, X, self.cls, self.score_override)

    def info(self) -> dict:
        return {
            "label": self.label,
            "class": self.cls,
            "n_train": self.n_train,
            "n_cal": self.n_cal,
            "attack_share_train": self.attack_share,
            "attack_share_prior": self.prior_share,
            "best_params": {k: repr(v) for k, v in self.best_params.items()},
            "cv_auc": self.cv_auc,
            "fit_seconds": round(self.seconds, 3),
        }


def _by_type(stage: Stage, table: Table, rows: np.ndarray) -> np.ndarray:
    if isinstance(stage.train_on, list):
        if table.types is None:
            raise FitError(f"stage {stage.name}: train_on lists types but the dataset has no type column")
        return rows[np.isin(table.types[rows], stage.train_on)]
    return rows


def training_rows(stage: Stage, table: Table, split: Splits) -> np.ndarray:
    if stage.kind == "classifier":
        base = split.fit_base if stage.calibrate != "none" else split.fit
        return _by_type(stage, table, base)
    rows = split.fit
    return rows[table.y[rows] == 0] if stage.fit_on == "benign" else rows


def calibration_rows(stage: Stage, table: Table, split: Splits) -> np.ndarray:
    return _by_type(stage, table, split.cal)


def _search(
    pipe: Any, cand: Candidate, X: Any, y: np.ndarray, *, seed: int, n_jobs: int
) -> tuple[Any, dict, float]:
    if cand.search is None:
        raise FitError(f"{cand.label}: no search settings")
    space = {f"model__{k}": list(v) for k, v in cand.search["space"].items()}
    folds = int(cand.search.get("cv", 3))
    minority = int(min((y == 1).sum(), (y == 0).sum()))
    if minority < folds:
        raise FitError(f"{cand.label}: {folds}-fold search needs {folds} rows of each class, got {minority}")
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    # a failing fold must stop the run instead of scoring NaN; scikit-learn accepts "raise" here
    strict: dict[str, Any] = {"error_score": "raise"}
    if cand.search.get("kind", "random") == "grid":
        search = GridSearchCV(pipe, space, scoring="roc_auc", cv=cv, n_jobs=n_jobs, refit=True, **strict)
    else:
        total = math.prod(len(v) for v in space.values())
        n_iter = min(int(cand.search.get("n_iter", 10)), total)
        search = RandomizedSearchCV(
            pipe,
            space,
            n_iter=n_iter,
            scoring="roc_auc",
            cv=cv,
            n_jobs=n_jobs,
            refit=True,
            random_state=seed,
            **strict,
        )
    search.fit(X, y)
    params = {k.removeprefix("model__"): v for k, v in search.best_params_.items()}
    return search.best_estimator_, params, float(search.best_score_)


def fit_candidate(stage: Stage, index: int, table: Table, split: Splits, *, seed: int, n_jobs: int) -> Fitted:
    cand = stage.candidates[index]
    t0 = time.perf_counter()
    rows = training_rows(stage, table, split)
    if len(rows) == 0:
        raise FitError(f"stage {stage.name}: no training rows")
    X, y = table.X.iloc[rows], table.y[rows]
    # one level of parallelism: a search spreads folds over the cores and fits single-threaded
    # models; without a search the model itself may use the cores
    model_jobs = 1 if cand.search is not None else n_jobs
    pipe = build_pipeline(stage.pipeline, build_estimator(cand, seed, model_jobs), seed)
    params: dict = {}
    cv_auc = None
    n_cal = 0
    attack_share = None
    prior_share = None
    if stage.kind == "classifier":
        if len(np.unique(y)) < 2:
            raise FitError(f"stage {stage.name}: training rows hold one class only")
        attack_share = float(y.mean())
        prior_share = attack_share
        if cand.search is not None:
            if is_custom(pipe):
                raise FitError(f"{cand.label}: a custom adapter cannot be searched")
            model, params, cv_auc = _search(pipe, cand, X, y, seed=seed, n_jobs=n_jobs)
        else:
            pipe.fit(X, y)  # fit may return None for a custom adapter
            model = pipe
        if stage.calibrate != "none":
            if is_custom(model):
                raise FitError(f"{cand.label}: a custom adapter cannot be calibrated")
            crows = calibration_rows(stage, table, split)
            yc = table.y[crows]
            if len(np.unique(yc)) < 2:
                raise FitError(f"stage {stage.name}: CAL rows hold one class only, calibration is impossible")
            model = CalibratedClassifierCV(FrozenEstimator(model), method=stage.calibrate).fit(
                table.X.iloc[crows], yc
            )
            n_cal = len(crows)
            prior_share = float(yc.mean())
    else:
        pipe.fit(X)
        model = pipe
    return Fitted(
        stage=stage.name,
        index=index,
        label=cand.label,
        kind=stage.kind,
        cls=cand.cls,
        model=model,
        score_override=cand.score,
        n_train=len(rows),
        n_cal=n_cal,
        attack_share=attack_share,
        prior_share=prior_share,
        best_params=params,
        cv_auc=cv_auc,
        seconds=time.perf_counter() - t0,
    )
