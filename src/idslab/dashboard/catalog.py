# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Models and pipeline steps the builder offers, with parameter forms read from each class.

The lists are a curated starting point, not a limit: the builder also takes any class path, and
plugins (``idslab.plugins`` entry points) add their own entries. A class from a missing optional
library is listed as not installed, with the extra that provides it.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from typing import Any

from idslab.config import EXTRAS, import_class

logger = logging.getLogger(__name__)

# (class path, short label, note)
CLASSIFIERS = [
    ("sklearn.ensemble.RandomForestClassifier", "Random forest", "strong default for flow features"),
    ("sklearn.ensemble.ExtraTreesClassifier", "Extra trees", "faster, more random random forest"),
    ("sklearn.ensemble.HistGradientBoostingClassifier", "Gradient boosting", "handles large tables fast"),
    ("sklearn.linear_model.LogisticRegression", "Logistic regression", "linear; scale the features"),
    ("sklearn.neighbors.KNeighborsClassifier", "k nearest neighbours", "scale the features"),
    ("sklearn.tree.DecisionTreeClassifier", "Decision tree", "one readable tree"),
    ("sklearn.neural_network.MLPClassifier", "MLP (scikit-learn)", "small neural network"),
    ("sklearn.naive_bayes.GaussianNB", "Gaussian naive Bayes", "very fast baseline"),
    ("xgboost.XGBClassifier", "XGBoost", "extra: xgboost"),
    ("lightgbm.LGBMClassifier", "LightGBM", "extra: lightgbm"),
    ("idslab.torch_models.TorchMLPClassifier", "PyTorch MLP", "extra: torch; scale the features"),
]
DETECTORS = [
    ("sklearn.ensemble.IsolationForest", "Isolation forest", "fit once; no scaling needed"),
    ("sklearn.neighbors.LocalOutlierFactor", "Local outlier factor", "fit once or per batch; scale"),
    ("sklearn.svm.OneClassSVM", "One-class SVM", "scale the features; slow on large data"),
    ("sklearn.covariance.EllipticEnvelope", "Elliptic envelope", "assumes roughly Gaussian features"),
    ("pyod.models.ecod.ECOD", "ECOD (PyOD)", "extra: pyod; no parameters to tune"),
    ("pyod.models.copod.COPOD", "COPOD (PyOD)", "extra: pyod"),
    ("pyod.models.hbos.HBOS", "HBOS (PyOD)", "extra: pyod; very fast"),
    ("idslab.torch_models.TorchAutoencoder", "PyTorch autoencoder", "extra: torch; scale the features"),
]
STEPS = [
    ("idslab.prep.Encode", "Encode categories", "required for text columns; list them in categorical"),
    ("sklearn.preprocessing.StandardScaler", "Standard scaler", "zero mean, unit variance"),
    ("sklearn.preprocessing.MinMaxScaler", "Min-max scaler", "scale to [0, 1]"),
    ("sklearn.preprocessing.RobustScaler", "Median and quartile scaler", "less sensitive to outliers"),
    ("sklearn.preprocessing.QuantileTransformer", "Quantile transformer", "map to a uniform or normal shape"),
    ("sklearn.decomposition.PCA", "PCA", "n_components below 1 keeps that share of variance"),
    ("sklearn.feature_selection.VarianceThreshold", "Drop constant features", ""),
    ("sklearn.feature_selection.SelectKBest", "Select k best", "univariate feature selection"),
    ("imblearn.over_sampling.SMOTE", "SMOTE", "extra: imbalance; oversample during fit only"),
    ("imblearn.under_sampling.RandomUnderSampler", "Random undersampler", "extra: imbalance"),
]
SKIP_PARAMS = {"random_state", "n_jobs", "verbose", "warm_start", "copy", "novelty", "device"}


def _installed(path: str) -> bool:
    return importlib.util.find_spec(path.split(".", 1)[0]) is not None


def params_of(path: str) -> list[dict]:
    """Constructor parameters with simple defaults: name, default and a form type."""
    try:
        cls = import_class(path)
        sig = inspect.signature(cls.__init__)
    except (ImportError, TypeError, ValueError):
        return []
    out = []
    for name, p in sig.parameters.items():
        if name == "self" or name in SKIP_PARAMS or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        d = None if p.default is inspect.Parameter.empty else p.default
        if isinstance(d, bool):
            kind = "bool"
        elif isinstance(d, int):
            kind = "int"
        elif isinstance(d, float):
            kind = "float"
        elif isinstance(d, str) or d is None:
            kind = "text"
        elif isinstance(d, tuple) and all(isinstance(x, (int, float)) for x in d):
            kind, d = "list", list(d)
        else:
            continue  # objects and callables: set them in YAML
        out.append({"name": name, "default": d, "type": kind})
    return out


def _entry(path: str, label: str, note: str) -> dict:
    installed = _installed(path)
    top = path.split(".", 1)[0]
    return {
        "class": path,
        "label": label,
        "note": note,
        "installed": installed,
        "extra": EXTRAS.get(top),
        "params": params_of(path) if installed else [],
    }


def plugin_entries() -> dict[str, list[tuple[str, str, str]]]:
    """Catalog entries from installed plugins (``idslab.plugins`` entry points), by section."""
    from importlib.metadata import entry_points  # noqa: PLC0415

    found: dict[str, list[tuple[str, str, str]]] = {"classifier": [], "detector": [], "step": []}
    for ep in entry_points(group="idslab.plugins"):
        try:
            spec = ep.load()()
        except Exception:  # a broken plugin must not break the catalog
            logger.warning("plugin %s could not be loaded", ep.name, exc_info=True)
            continue
        if not isinstance(spec, dict):
            continue
        for section, items in found.items():
            for item in spec.get(section, []):
                if isinstance(item, dict) and isinstance(item.get("class"), str):
                    items.append(
                        (item["class"], str(item.get("label", item["class"])), str(item.get("note", "")))
                    )
    return found


def catalog() -> dict[str, Any]:
    extra = plugin_entries()
    classifiers = [_entry(*c) for c in CLASSIFIERS + extra["classifier"]]
    return {
        "classifier": classifiers,
        "multiclass": classifiers,
        "detector": [_entry(*c) for c in DETECTORS + extra["detector"]],
        "step": [_entry(*c) for c in STEPS + extra["step"]],
    }
