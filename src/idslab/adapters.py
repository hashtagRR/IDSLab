# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Model adapters: build estimators and read their output as a score contract.

Contracts (DESIGN.md section 0.1):

* AttackScore: one value per row, P(attack), read from ``predict_proba`` at the
  column of class 1.
* AnomalyScore: one value per row, higher means more anomalous. scikit-learn
  detectors use ``score_samples`` (higher means more normal, so it is negated);
  PyOD detectors use ``decision_function`` (already higher means more anomalous);
  a candidate may override both with ``score: {method, higher_is_anomalous}``.

A class that is not a scikit-learn estimator can still be used: give it a class
attribute ``idslab_contract = "AttackScore"`` with ``fit(X, y)`` and
``attack_score(X)``, or ``idslab_contract = "AnomalyScore"`` with ``fit(X)`` and
``anomaly_score(X)``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from idslab.config import Candidate, import_class

ATTACK = 1


class ContractError(RuntimeError):
    """A model's output does not meet its score contract."""


def is_custom(model: Any) -> bool:
    return getattr(model, "idslab_contract", None) is not None


def build_estimator(cand: Candidate, seed: int, n_jobs: int) -> Any:
    cls = import_class(cand.cls)
    params = dict(cand.params)
    if getattr(cls, "idslab_contract", None) is not None:
        return cls(**params)
    est = cls(**params)
    known = est.get_params()
    extra = {}
    if "random_state" in known and "random_state" not in params:
        extra["random_state"] = seed
    if "n_jobs" in known and "n_jobs" not in params and n_jobs != 1:
        extra["n_jobs"] = n_jobs
    if "novelty" in known:
        extra["novelty"] = True
    if extra:
        est.set_params(**extra)
    return est


def build_pipeline(steps: tuple[dict, ...], estimator: Any, seed: int) -> Any:
    """Pipeline of the stage's steps with the model last, named ``model``.

    An imbalanced-learn sampler in the steps switches to its Pipeline, which
    resamples during fit only.
    """
    built = []
    for i, step in enumerate(steps):
        obj = import_class(step["class"])(**(step.get("params") or {}))
        if "random_state" in obj.get_params() and "random_state" not in (step.get("params") or {}):
            obj.set_params(random_state=seed)
        built.append((f"step{i}", obj))
    if is_custom(estimator):
        if built:
            raise ContractError("a custom adapter class cannot be combined with pipeline steps")
        return estimator
    if any(hasattr(obj, "fit_resample") for _, obj in built):
        from imblearn.pipeline import Pipeline as ImbPipeline  # noqa: PLC0415  (optional extra)

        return ImbPipeline([*built, ("model", estimator)])
    from sklearn.pipeline import Pipeline  # noqa: PLC0415

    return Pipeline([*built, ("model", estimator)])


def attack_score(model: Any, X: Any) -> np.ndarray:
    """P(attack) per row (AttackScore contract)."""
    if is_custom(model):
        out = np.asarray(model.attack_score(X), dtype=np.float64)
    else:
        classes = list(np.asarray(model.classes_).tolist())
        if ATTACK not in classes:
            raise ContractError(f"classifier classes {classes} do not include the attack class 1")
        out = np.asarray(model.predict_proba(X), dtype=np.float64)[:, classes.index(ATTACK)]
    _check(out, X, "AttackScore")
    if out.min() < 0 or out.max() > 1:
        raise ContractError("AttackScore must be a probability in [0, 1]")
    return out


def anomaly_score(model: Any, X: Any, cls_path: str, override: dict | None) -> np.ndarray:
    """Anomaly score per row, higher = more anomalous (AnomalyScore contract)."""
    if is_custom(model):
        out = np.asarray(model.anomaly_score(X), dtype=np.float64)
    else:
        detector, data = final_step(model, X)
        if override is not None:
            method, higher_is_anomalous = override["method"], bool(override["higher_is_anomalous"])
        elif cls_path.startswith("pyod."):
            method, higher_is_anomalous = "decision_function", True
        elif hasattr(detector, "score_samples"):
            method, higher_is_anomalous = "score_samples", False
        else:
            method, higher_is_anomalous = "decision_function", False
        if not hasattr(detector, method):
            raise ContractError(f"{cls_path} has no method {method!r}")
        raw = np.asarray(getattr(detector, method)(data), dtype=np.float64)
        out = raw if higher_is_anomalous else -raw
    _check(out, X, "AnomalyScore")
    return out


def final_step(model: Any, X: Any) -> tuple[Any, Any]:
    """The last estimator of a pipeline and ``X`` passed through the steps before it.

    Scoring methods are called on the detector itself, so any method it has can be
    named in a ``score`` override (a Pipeline passes only a fixed set through).
    """
    steps = getattr(model, "steps", None)
    if not steps:
        return model, X
    data = X
    for _, step in steps[:-1]:
        if hasattr(step, "transform"):
            data = step.transform(data)
    return steps[-1][1], data


def _check(out: np.ndarray, X: Any, contract: str) -> None:
    if out.ndim != 1 or len(out) != len(X):
        raise ContractError(
            f"{contract} must give one value per row; got shape {out.shape} for {len(X)} rows"
        )
    if not np.isfinite(out).all():
        raise ContractError(f"{contract} has {int((~np.isfinite(out)).sum())} non-finite values")
