# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""PyTorch models with a scikit-learn interface, so they fit in any stage (optional extra ``torch``).

* ``TorchMLPClassifier``: feed-forward network for classifier and multiclass stages
  (``predict_proba`` and ``classes_``, AttackScore and ClassProba contracts).
* ``TorchAutoencoder``: benign-trained detector; ``score_samples`` is the negative
  reconstruction error, so higher means more normal (scikit-learn convention) and the
  AnomalyScore adapter turns it into higher = more anomalous.

Both seed torch from ``random_state`` and train on the CPU unless ``device`` says
otherwise. Put a scaler in the stage pipeline; the networks expect scaled inputs.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, OutlierMixin


def _torch() -> Any:
    try:
        import torch  # noqa: PLC0415  (optional extra)
    except ImportError as e:  # pragma: no cover  (config load reports the missing extra first)
        raise ImportError("PyTorch models need torch: pip install idslab[torch]") from e
    return torch


def _layers(torch: Any, sizes: list[int], dropout: float, last_linear: bool = True) -> Any:
    nn = torch.nn
    mods: list[Any] = []
    for i in range(len(sizes) - 1):
        mods.append(nn.Linear(sizes[i], sizes[i + 1]))
        if i < len(sizes) - 2 or not last_linear:
            mods.append(nn.ReLU())
            if dropout > 0:
                mods.append(nn.Dropout(dropout))
    return nn.Sequential(*mods)


def _train(torch: Any, net: Any, X: Any, target: Any, *, loss_fn: Any, est: Any) -> list[float]:
    """Mini-batch Adam; returns the mean loss of each epoch."""
    opt = torch.optim.Adam(net.parameters(), lr=est.lr, weight_decay=est.weight_decay)
    gen = torch.Generator().manual_seed(int(est.random_state or 0))
    n = X.shape[0]
    history = []
    net.train()
    for _ in range(int(est.epochs)):
        order = torch.randperm(n, generator=gen)
        total = 0.0
        for start in range(0, n, int(est.batch_size)):
            idx = order[start : start + int(est.batch_size)]
            opt.zero_grad()
            loss = loss_fn(net(X[idx]), target[idx])
            loss.backward()
            opt.step()
            total += float(loss.detach()) * len(idx)
        history.append(total / n)
    net.eval()
    return history


class TorchMLPClassifier(ClassifierMixin, BaseEstimator):
    """Feed-forward classifier trained with cross-entropy."""

    idslab_requires = ("torch",)

    def __init__(
        self,
        *,
        hidden=(64, 32),
        epochs=20,
        lr=1e-3,
        batch_size=256,
        weight_decay=0.0,
        dropout=0.0,
        random_state=None,
        device="cpu",
    ):
        self.hidden = hidden
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        self.weight_decay = weight_decay
        self.dropout = dropout
        self.random_state = random_state
        self.device = device

    def fit(self, X, y):
        torch = _torch()
        torch.manual_seed(int(self.random_state or 0))
        Xa = np.asarray(X, dtype=np.float32)
        self.classes_, idx = np.unique(np.asarray(y), return_inverse=True)
        if len(self.classes_) < 2:
            raise ValueError("TorchMLPClassifier needs at least two classes")
        self.n_features_in_ = Xa.shape[1]
        sizes = [Xa.shape[1], *[int(h) for h in self.hidden], len(self.classes_)]
        self.net_ = _layers(torch, sizes, float(self.dropout)).to(self.device)
        Xt = torch.from_numpy(Xa).to(self.device)
        yt = torch.from_numpy(idx.astype(np.int64)).to(self.device)
        self.loss_curve_ = _train(torch, self.net_, Xt, yt, loss_fn=torch.nn.CrossEntropyLoss(), est=self)
        return self

    def predict_proba(self, X):
        torch = _torch()
        Xt = torch.from_numpy(np.asarray(X, dtype=np.float32)).to(self.device)
        with torch.no_grad():
            p = torch.softmax(self.net_(Xt), dim=1).cpu().numpy()
        return p.astype(np.float64)

    def predict(self, X):
        return self.classes_[self.predict_proba(X).argmax(axis=1)]


class TorchAutoencoder(OutlierMixin, BaseEstimator):
    """Autoencoder trained to reconstruct its training rows (usually benign flows)."""

    idslab_requires = ("torch",)

    def __init__(
        self,
        *,
        hidden=(32, 8, 32),
        epochs=30,
        lr=1e-3,
        batch_size=256,
        weight_decay=0.0,
        dropout=0.0,
        contamination=0.01,
        random_state=None,
        device="cpu",
    ):
        self.hidden = hidden
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        self.weight_decay = weight_decay
        self.dropout = dropout
        self.contamination = contamination
        self.random_state = random_state
        self.device = device

    def fit(self, X, y=None):
        del y  # unsupervised
        torch = _torch()
        torch.manual_seed(int(self.random_state or 0))
        Xa = np.asarray(X, dtype=np.float32)
        self.n_features_in_ = Xa.shape[1]
        sizes = [Xa.shape[1], *[int(h) for h in self.hidden], Xa.shape[1]]
        self.net_ = _layers(torch, sizes, float(self.dropout)).to(self.device)
        Xt = torch.from_numpy(Xa).to(self.device)
        self.loss_curve_ = _train(torch, self.net_, Xt, Xt, loss_fn=torch.nn.MSELoss(), est=self)
        self.offset_ = float(np.quantile(self.score_samples(Xa), float(self.contamination)))
        return self

    def score_samples(self, X):
        torch = _torch()
        Xt = torch.from_numpy(np.asarray(X, dtype=np.float32)).to(self.device)
        with torch.no_grad():
            err = ((self.net_(Xt) - Xt) ** 2).mean(dim=1).cpu().numpy()
        return -err.astype(np.float64)

    def decision_function(self, X):
        return self.score_samples(X) - self.offset_

    def predict(self, X):
        return np.where(self.decision_function(X) >= 0, 1, -1)
