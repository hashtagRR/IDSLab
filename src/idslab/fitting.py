# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Fit a stage's candidates on their training rows (DESIGN.md sections 4.1 to 4.3).

Classifiers and multiclass stages: calibrated stages fit on FIT-base and calibrate
on CAL; uncalibrated stages fit on all of FIT. ``train_on`` filters those rows by
type, by attack label, or by a band of a source stage's out-of-fold score
(``hard``). Detectors fitted once use FIT or its benign rows; pool detectors fit
only their preprocessing here and refit the detector on every batch. A search,
when given, runs on the training rows only with the whole pipeline refitted in
every fold. Upstream scores (``extra_features``) come from the feature store.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.model_selection import GridSearchCV, KFold, RandomizedSearchCV, StratifiedKFold

from idslab.adapters import (
    ClassScores,
    PoolDetector,
    anomaly_score,
    attack_score,
    build_estimator,
    build_pipeline,
    class_proba,
    is_custom,
)
from idslab.config import Candidate, Stage
from idslab.data import Splits, Table
from idslab.external import ExternalContext, ExternalModel, load_external
from idslab.features import FeatureStore, fold_ids


class FitError(RuntimeError):
    """A candidate cannot be fitted on the rows the protocol gives it."""


@dataclass
class Fitted:
    stage: str
    index: int
    label: str
    kind: str
    cls: str
    model: Any = field(repr=False)  # what the stage scores with (calibrated if the stage calibrates)
    raw: Any = field(repr=False)  # the uncalibrated pipeline: feature scores and out-of-fold refits
    score_override: dict | None
    transductive: bool
    n_train: int
    n_cal: int
    attack_share: float | None
    prior_share: float | None  # attack share the output probabilities encode (CAL if calibrated)
    best_params: dict
    cv_score: float | None
    seconds: float
    producer: dict | None = None  # provenance block of an external score file

    def score(self, X: Any) -> np.ndarray | ClassScores:
        """The stage's score: P(attack), class probabilities or an anomaly score."""
        if self.transductive:
            raise FitError(f"stage {self.stage}: a pool detector scores batches; use pool_score")
        if self.kind == "classifier":
            return attack_score(self.model, X)
        if self.kind == "multiclass":
            return class_proba(self.model, X)
        return anomaly_score(self.model, X, self.cls, self.score_override)

    def raw_score(self, X: Any) -> np.ndarray:
        """Uncalibrated score as feature columns (rows x 1, or rows x types)."""
        return raw_columns(
            self.kind, self.raw, X, cls=self.cls, override=self.score_override, classes=self.classes()
        )

    def pool_score(self, X: Any) -> np.ndarray:
        return self.model.score_pool(X)

    def classes(self) -> tuple[str, ...] | None:
        if self.kind != "multiclass":
            return None
        return tuple(str(c) for c in np.asarray(self.raw.classes_).tolist())

    @property
    def external(self) -> bool:
        return isinstance(self.raw, ExternalModel)

    def info(self) -> dict:
        return {
            "external_producer": self.producer,
            "label": self.label,
            "class": self.cls,
            "n_train": self.n_train,
            "n_cal": self.n_cal,
            "attack_share_train": self.attack_share,
            "attack_share_prior": self.prior_share,
            "classes": list(self.classes() or []),
            "best_params": {k: repr(v) for k, v in self.best_params.items()},
            "cv_score": self.cv_score,
            "fit_seconds": round(self.seconds, 3),
        }


def raw_columns(
    kind: str, model: Any, X: Any, *, cls: str, override: dict | None, classes: tuple[str, ...] | None
) -> np.ndarray:
    if kind == "classifier":
        return attack_score(model, X)[:, None]
    if kind == "multiclass":
        cs = class_proba(model, X)
        out = np.zeros((len(X), len(classes or cs.classes)))
        order = {c: j for j, c in enumerate(classes or cs.classes)}
        for j, c in enumerate(cs.classes):
            if c in order:
                out[:, order[c]] = cs.proba[:, j]
        return out
    return anomaly_score(model, X, cls, override)[:, None]


def _filter(stage: Stage, table: Table, rows: np.ndarray, store: FeatureStore) -> np.ndarray:
    if isinstance(stage.train_on, list):
        if table.types is None:
            raise FitError(f"stage {stage.name}: train_on lists types but the dataset has no type column")
        return rows[np.isin(table.types[rows], stage.train_on)]
    if stage.train_on == "attack":
        return rows[table.y[rows] == 1]
    if stage.train_on == "hard" and stage.hard is not None:
        low, high = stage.hard["band"]
        s = store.scalar(stage.hard["source"], rows)
        return rows[(s >= low) & (s <= high)]
    return rows


def training_rows(stage: Stage, table: Table, split: Splits, store: FeatureStore) -> np.ndarray:
    if stage.kind in ("classifier", "multiclass"):
        base = split.fit_base if stage.calibrate != "none" else split.fit
        return _filter(stage, table, base, store)
    rows = split.fit
    benign = stage.prep_fit_on == "benign" if stage.transductive else stage.fit_on == "benign"
    return rows[table.y[rows] == 0] if benign else rows


def calibration_rows(stage: Stage, table: Table, split: Splits, store: FeatureStore) -> np.ndarray:
    return _filter(stage, table, split.cal, store)


def labels(stage: Stage, table: Table, rows: np.ndarray) -> np.ndarray:
    if stage.kind == "multiclass":
        if table.types is None:
            raise FitError(f"stage {stage.name}: a multiclass stage needs a type column")
        return table.types[rows].astype(str)
    return table.y[rows]


def _search(
    pipe: Any, cand: Candidate, X: Any, y: np.ndarray, *, multiclass: bool, seed: int, n_jobs: int
) -> tuple[Any, dict, float]:
    if cand.search is None:
        raise FitError(f"{cand.label}: no search settings")
    space = {f"model__{k}": list(v) for k, v in cand.search["space"].items()}
    folds = int(cand.search.get("cv", 3))
    _, counts = np.unique(y, return_counts=True)
    if not multiclass and counts.min() < folds:
        raise FitError(f"{cand.label}: a {folds}-fold search needs {folds} rows of each class")
    if counts.min() >= folds:
        cv: Any = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    else:  # a rare type cannot sit in every fold
        cv = KFold(n_splits=folds, shuffle=True, random_state=seed)
    scoring = "balanced_accuracy" if multiclass else "roc_auc"
    # a failing fold must stop the run instead of scoring NaN; scikit-learn accepts "raise" here
    strict: dict[str, Any] = {"error_score": "raise"}
    if cand.search.get("kind", "random") == "grid":
        search = GridSearchCV(pipe, space, scoring=scoring, cv=cv, n_jobs=n_jobs, refit=True, **strict)
    else:
        total = math.prod(len(v) for v in space.values())
        n_iter = min(int(cand.search.get("n_iter", 10)), total)
        search = RandomizedSearchCV(
            pipe,
            space,
            n_iter=n_iter,
            scoring=scoring,
            cv=cv,
            n_jobs=n_jobs,
            refit=True,
            random_state=seed,
            **strict,
        )
    search.fit(X, y)
    params = {k.removeprefix("model__"): v for k, v in search.best_params_.items()}
    return search.best_estimator_, params, float(search.best_score_)


def build(stage: Stage, cand: Candidate, *, seed: int, n_jobs: int, params: dict | None = None) -> Any:
    """Unfitted pipeline for a candidate, with ``params`` (found by a search) applied."""
    pipe = build_pipeline(stage.pipeline, build_estimator(cand, seed, n_jobs), seed)
    if params:
        if is_custom(pipe):
            raise FitError(f"{cand.label}: a custom adapter takes no searched parameters")
        pipe.set_params(**{f"model__{k}": v for k, v in params.items()})
    return pipe


class CalibratedExternal:
    """P(attack) of an external classifier mapped by a calibrator fitted on its CAL scores."""

    idslab_contract = "AttackScore"

    def __init__(self, base: ExternalModel, method: str, scores: np.ndarray, y: np.ndarray):
        self.base, self.method = base, method
        if method == "isotonic":
            from sklearn.isotonic import IsotonicRegression  # noqa: PLC0415

            self.map: Any = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(scores, y)
        else:
            from sklearn.linear_model import LogisticRegression  # noqa: PLC0415

            self.map = LogisticRegression(C=1e6).fit(_logit(scores)[:, None], y)

    def attack_score(self, X: Any) -> np.ndarray:
        s = self.base.attack_score(X)
        if self.method == "isotonic":
            return np.asarray(self.map.predict(s), dtype=np.float64)
        return self.map.predict_proba(_logit(s)[:, None])[:, 1]


def _logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(q / (1 - q))


def _fit_external(
    stage: Stage,
    index: int,
    table: Table,
    split: Splits,
    store: FeatureStore,
    *,
    ext: ExternalContext,
    t0: float,
) -> Fitted:
    cand = stage.candidates[index]
    raw, producer = load_external(stage, cand, table, split, ext)
    model: Any = raw
    n_cal, prior = 0, None
    rows = split.fit_base if producer["trained_on"] == "fit_base" else split.fit
    if stage.kind == "classifier":
        prior = float(table.y[rows].mean())
        if stage.calibrate != "none":
            crows = calibration_rows(stage, table, split, store)
            yc = table.y[crows]
            if len(np.unique(yc)) < 2:
                raise FitError(f"stage {stage.name}: CAL rows hold one class only, calibration is impossible")
            model = CalibratedExternal(raw, stage.calibrate, raw.attack_score(table.X.iloc[crows]), yc)
            n_cal, prior = len(crows), float(yc.mean())
    return Fitted(
        stage=stage.name,
        index=index,
        label=cand.label,
        kind=stage.kind,
        cls="external",
        model=model,
        raw=raw,
        score_override=None,
        transductive=False,
        n_train=len(rows),
        n_cal=n_cal,
        attack_share=float(table.y[rows].mean()) if stage.kind == "classifier" else None,
        prior_share=prior,
        best_params={},
        cv_score=None,
        seconds=time.perf_counter() - t0,
        producer=producer,
    )


def fit_candidate(
    stage: Stage,
    index: int,
    table: Table,
    split: Splits,
    store: FeatureStore,
    *,
    seed: int,
    n_jobs: int,
    ext: ExternalContext | None = None,
) -> Fitted:
    cand = stage.candidates[index]
    t0 = time.perf_counter()
    if cand.external is not None:
        if ext is None:
            raise FitError(f"stage {stage.name}: {cand.label} needs the run unit's context to find its file")
        return _fit_external(stage, index, table, split, store, ext=ext, t0=t0)
    rows = training_rows(stage, table, split, store)
    if len(rows) == 0:
        raise FitError(f"stage {stage.name}: no training rows")
    X = store.frame(stage.extra_features, rows)
    if stage.transductive:
        det = PoolDetector(cand, stage.pipeline, seed, n_jobs).fit(X)
        return Fitted(
            stage=stage.name,
            index=index,
            label=cand.label,
            kind=stage.kind,
            cls=cand.cls,
            model=det,
            raw=det,
            score_override=None,
            transductive=True,
            n_train=len(rows),
            n_cal=0,
            attack_share=None,
            prior_share=None,
            best_params={},
            cv_score=None,
            seconds=time.perf_counter() - t0,
        )
    y = labels(stage, table, rows)
    # one level of parallelism: a search spreads folds over the cores and fits single-threaded
    # models; without a search the model itself may use the cores
    model_jobs = 1 if cand.search is not None else n_jobs
    pipe = build(stage, cand, seed=seed, n_jobs=model_jobs)
    params: dict = {}
    cv_score = None
    n_cal = 0
    attack_share = prior_share = None
    if stage.kind == "detector":
        pipe.fit(X)
        raw = model = pipe
    else:
        if len(np.unique(y)) < 2:
            raise FitError(f"stage {stage.name}: training rows hold one class only")
        if stage.kind == "classifier":
            attack_share = prior_share = float(np.mean(y))
        if cand.search is not None:
            if is_custom(pipe):
                raise FitError(f"{cand.label}: a custom adapter cannot be searched")
            raw, params, cv_score = _search(
                pipe, cand, X, y, multiclass=stage.kind == "multiclass", seed=seed, n_jobs=n_jobs
            )
        else:
            pipe.fit(X, y)  # fit may return None for a custom adapter
            raw = pipe
        model = raw
        if stage.calibrate != "none":
            if is_custom(raw):
                raise FitError(f"{cand.label}: a custom adapter cannot be calibrated")
            crows = calibration_rows(stage, table, split, store)
            yc = labels(stage, table, crows)
            if stage.kind == "multiclass":  # calibration only knows the types the model was trained on
                keep = np.isin(yc, np.asarray(raw.classes_).astype(str))
                crows, yc = crows[keep], yc[keep]
            if len(np.unique(yc)) < 2:
                raise FitError(f"stage {stage.name}: CAL rows hold one class only, calibration is impossible")
            model = CalibratedClassifierCV(FrozenEstimator(raw), method=stage.calibrate).fit(
                store.frame(stage.extra_features, crows), yc
            )
            n_cal = len(crows)
            if stage.kind == "classifier":
                prior_share = float(np.mean(yc))
    return Fitted(
        stage=stage.name,
        index=index,
        label=cand.label,
        kind=stage.kind,
        cls=cand.cls,
        model=model,
        raw=raw,
        score_override=cand.score,
        transductive=False,
        n_train=len(rows),
        n_cal=n_cal,
        attack_share=attack_share,
        prior_share=prior_share,
        best_params=params,
        cv_score=cv_score,
        seconds=time.perf_counter() - t0,
    )


def oof_scores(
    stage: Stage,
    fitted: Fitted,
    table: Table,
    split: Splits,
    store: FeatureStore,
    *,
    folds: int,
    seed: int,
    n_jobs: int,
) -> np.ndarray:
    """Out-of-fold uncalibrated scores of a source stage for every FIT row (rows follow ``split.fit``).

    FIT is cut into ``folds`` stratified folds. For each fold the source's whole
    procedure (its own training rows within the other folds; pipeline and model with
    the hyperparameters its search chose) is refitted and scores every row of the
    held-out fold, so no FIT row gets a score from a model that saw it.
    """
    fit = split.fit
    if isinstance(fitted.raw, ExternalModel):  # made elsewhere: its file carries the oof scores
        return fitted.raw.oof(fit)
    strata = table.types[fit] if table.types is not None else table.y[fit]
    fold = fold_ids(strata, folds, seed)
    own = training_rows(stage, table, split, store)  # the source's own rows, as in DESIGN.md 4.2
    classes = fitted.classes()
    out = np.full((len(fit), len(classes) if classes else 1), np.nan)
    cand = stage.candidates[fitted.index]
    for f in range(folds):
        held = fit[fold == f]
        train = np.intersect1d(own, fit[fold != f])
        y = labels(stage, table, train)
        if stage.kind != "detector" and len(np.unique(y)) < 2:
            raise FitError(f"stage {stage.name}: an out-of-fold training part holds one class only")
        pipe = build(stage, cand, seed=seed, n_jobs=n_jobs, params=fitted.best_params)
        Xf = store.frame(stage.extra_features, train)
        if stage.kind == "detector":
            pipe.fit(Xf)
        else:
            pipe.fit(Xf, y)
        held_X = store.frame(stage.extra_features, held)
        out[fold == f] = raw_columns(
            stage.kind, pipe, held_X, cls=cand.cls, override=cand.score, classes=classes
        )
    return out
