# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Binary metrics, objectives, the frontier gap, bootstrap intervals and the EM prior.

All metrics work on confusion counts, so the same formulas serve single values and
vectorised bootstrap replicates.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import roc_auc_score, roc_curve

METRICS = ("accuracy", "precision", "recall", "fpr", "f1", "mcc", "balanced_accuracy")


def counts(y: np.ndarray, pred: np.ndarray) -> tuple[int, int, int, int]:
    y = np.asarray(y).astype(bool)
    p = np.asarray(pred).astype(bool)
    return int((p & y).sum()), int((p & ~y).sum()), int((~p & ~y).sum()), int((~p & y).sum())


def _div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return np.divide(a, b, out=np.zeros(np.broadcast(a, b).shape), where=b != 0)


def _mcc(tp, fp, tn, fn) -> np.ndarray:
    return _div(tp * tn - fp * fn, np.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)))


_FORMULAS = {
    "accuracy": lambda tp, fp, tn, fn: _div(tp + tn, tp + fp + tn + fn),
    "precision": lambda tp, fp, tn, fn: _div(tp, tp + fp),  # noqa: ARG005
    "recall": lambda tp, fp, tn, fn: _div(tp, tp + fn),  # noqa: ARG005
    "fpr": lambda tp, fp, tn, fn: _div(fp, fp + tn),  # noqa: ARG005
    "f1": lambda tp, fp, tn, fn: _div(2 * tp, 2 * tp + fp + fn),  # noqa: ARG005
    "balanced_accuracy": lambda tp, fp, tn, fn: (_div(tp, tp + fn) + _div(tn, tn + fp)) / 2,
    "mcc": _mcc,
}


def from_counts(name: str, tp, fp, tn, fn) -> np.ndarray:
    """Metric ``name`` from confusion counts; arrays broadcast (bootstrap replicates)."""
    if name not in _FORMULAS:
        raise ValueError(f"unknown metric {name}")
    tp, fp, tn, fn = (np.asarray(v, dtype=np.float64) for v in (tp, fp, tn, fn))
    return _FORMULAS[name](tp, fp, tn, fn)


def binary_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    tp, fp, tn, fn = counts(y, pred)
    out = {m: float(from_counts(m, tp, fp, tn, fn)) for m in METRICS}
    out.update(tp=tp, fp=fp, tn=tn, fn=fn, n=tp + fp + tn + fn)
    return out


def objective(y: np.ndarray, pred: np.ndarray, name: str) -> float:
    return float(from_counts(name, *counts(y, pred)))


def auc(y: np.ndarray, score: np.ndarray) -> float | None:
    y = np.asarray(y)
    if len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, score))


def roc_points(y: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """ROC over all distinct scores (tied scores form one point), ordered by FPR."""
    fpr, tpr, _ = roc_curve(np.asarray(y), np.asarray(score), drop_intermediate=False)
    return np.asarray(fpr), np.asarray(tpr)


def _upper_hull(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Upper concave hull of points sorted by x (monotone chain)."""
    hx: list[float] = []
    hy: list[float] = []
    for px, py in zip(x.tolist(), y.tolist(), strict=True):
        while len(hx) >= 2 and (hx[-1] - hx[-2]) * (py - hy[-2]) - (hy[-1] - hy[-2]) * (px - hx[-2]) >= 0:
            hx.pop()
            hy.pop()
        hx.append(px)
        hy.append(py)
    return np.asarray(hx), np.asarray(hy)


def first_stage_tpr_at(fpr_pts: np.ndarray, tpr_pts: np.ndarray, at: float) -> tuple[float, float]:
    """(interpolated, step) TPR of the first-stage ROC at FPR ``at``.

    Interpolated: the upper convex hull of the ROC points. Randomising between any
    two thresholds reaches every point on a line between them, so the hull is the
    best the first stage can do by threshold choice alone. Step: the best TPR of a
    single threshold whose FPR does not exceed ``at``.
    """
    ux, inv = np.unique(fpr_pts, return_inverse=True)
    uy = np.full(len(ux), -np.inf)
    np.maximum.at(uy, inv, tpr_pts)
    hx, hy = _upper_hull(ux, uy)
    interp = float(np.interp(at, hx, hy))
    step = float(uy[ux <= at + 1e-12].max()) if (ux <= at + 1e-12).any() else 0.0
    return interp, step


def frontier_gap(y: np.ndarray, first_score: np.ndarray, pred: np.ndarray) -> dict:
    """Cascade TPR minus the first-stage ROC TPR at the cascade's FPR."""
    y = np.asarray(y).astype(int)
    m = binary_metrics(y, pred)
    if m["tp"] + m["fn"] == 0 or m["fp"] + m["tn"] == 0:
        return {"fpr": m["fpr"], "tpr": m["recall"], "defined": False}
    fpr_pts, tpr_pts = roc_points(y, first_score)
    interp, step = first_stage_tpr_at(fpr_pts, tpr_pts, m["fpr"])
    return {
        "fpr": m["fpr"],
        "tpr": m["recall"],
        "first_tpr_interp": interp,
        "first_tpr_step": step,
        "gap_interp": m["recall"] - interp,
        "gap_step": m["recall"] - step,
        "defined": True,
    }


class _SortedROC:
    """First-stage scores sorted once, so each bootstrap replicate is a weighted linear pass."""

    def __init__(self, y: np.ndarray, score: np.ndarray, pred: np.ndarray):
        order = np.argsort(-np.asarray(score, dtype=np.float64), kind="stable")
        s = np.asarray(score, dtype=np.float64)[order]
        self.y = np.asarray(y)[order].astype(bool)
        self.pred = np.asarray(pred)[order].astype(bool)
        # last position of each run of tied scores: one ROC point per distinct score
        self.ends = np.r_[np.flatnonzero(np.diff(s) != 0), len(s) - 1]
        self.pos = np.flatnonzero(self.y)
        self.neg = np.flatnonzero(~self.y)

    def gap(self, w: np.ndarray) -> float | None:
        """Interpolated frontier gap with row weights ``w`` (all ones gives frontier_gap)."""
        wp, wn = np.where(self.y, w, 0.0), np.where(self.y, 0.0, w)
        p_tot, n_tot = wp.sum(), wn.sum()
        if p_tot == 0 or n_tot == 0:
            return None
        fpr_pts = np.r_[0.0, np.cumsum(wn)[self.ends] / n_tot]
        tpr_pts = np.r_[0.0, np.cumsum(wp)[self.ends] / p_tot]
        fpr_c = float(wn[self.pred].sum() / n_tot)
        tpr_c = float(wp[self.pred].sum() / p_tot)
        return tpr_c - first_stage_tpr_at(fpr_pts, tpr_pts, fpr_c)[0]

    def weights(self, rng: np.random.Generator) -> np.ndarray:
        w = np.zeros(len(self.y))
        for idx in (self.pos, self.neg):
            if len(idx):
                w[idx] = rng.multinomial(len(idx), np.full(len(idx), 1.0 / len(idx)))
        return w


def frontier_ci(y: np.ndarray, first_score: np.ndarray, pred: np.ndarray, n: int, seed: int) -> list | None:
    """95% percentile interval of the interpolated gap (benign and attack resampled separately).

    Resampling with replacement inside each group is drawn as multinomial row
    weights, so the scores are sorted once instead of once per replicate.
    """
    if n <= 0:
        return None
    y = np.asarray(y).astype(int)
    if len(np.unique(y)) < 2:
        return None
    roc = _SortedROC(y, first_score, pred)
    rng = np.random.default_rng(seed)
    gaps = [g for g in (roc.gap(roc.weights(rng)) for _ in range(n)) if g is not None]
    if not gaps:
        return None
    return [float(np.percentile(gaps, 2.5)), float(np.percentile(gaps, 97.5))]


def paired_bootstrap(
    y: np.ndarray, pred_a: np.ndarray, pred_b: np.ndarray, *, n: int, seed: int, names=METRICS
) -> dict:
    """Metric of ``pred_a`` minus metric of ``pred_b`` with 95% intervals.

    Flows are resampled with replacement within the attack and the benign group,
    keeping each flow's pair of predictions. Only the counts of the four (a, b)
    cells in each group matter, so the resample is drawn as two multinomials,
    which is exact and cheap for any number of flows. The interval is conditional
    on the fitted system (TEST-sample uncertainty only).
    """
    y = np.asarray(y).astype(bool)
    a = np.asarray(pred_a).astype(bool)
    b = np.asarray(pred_b).astype(bool)
    point = {m: objective(y, a, m) - objective(y, b, m) for m in names}
    if n <= 0:
        return {m: {"diff": point[m], "ci95": None} for m in names}
    rng = np.random.default_rng(seed)

    def cells(mask: np.ndarray) -> tuple[int, np.ndarray]:
        aa, bb = a[mask], b[mask]
        c = np.array([(~aa & ~bb).sum(), (~aa & bb).sum(), (aa & ~bb).sum(), (aa & bb).sum()], dtype=float)
        total = int(mask.sum())
        probs = c / total if total else np.full(4, 0.25)
        return total, probs

    n_pos, p_pos = cells(y)
    n_neg, p_neg = cells(~y)
    pos = rng.multinomial(n_pos, p_pos, size=n)
    neg = rng.multinomial(n_neg, p_neg, size=n)
    # cell order: (a0 b0, a0 b1, a1 b0, a1 b1)
    tp_a, fn_a = pos[:, 2] + pos[:, 3], pos[:, 0] + pos[:, 1]
    tp_b, fn_b = pos[:, 1] + pos[:, 3], pos[:, 0] + pos[:, 2]
    fp_a, tn_a = neg[:, 2] + neg[:, 3], neg[:, 0] + neg[:, 1]
    fp_b, tn_b = neg[:, 1] + neg[:, 3], neg[:, 0] + neg[:, 2]
    out = {}
    for m in names:
        d = from_counts(m, tp_a, fp_a, tn_a, fn_a) - from_counts(m, tp_b, fp_b, tn_b, fn_b)
        out[m] = {"diff": point[m], "ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]}
    return out


def em_prior(p: np.ndarray, train_prior: float, iters: int = 500, tol: float = 1e-8) -> float:
    """EM estimate of the attack share of unlabelled rows (Saerens et al., 2002).

    ``p`` are calibrated P(attack) values for a model whose training prior was
    ``train_prior``. Valid under label shift only.
    """
    p = np.clip(np.asarray(p, dtype=np.float64), 1e-12, 1 - 1e-12)
    pi_tr = float(np.clip(train_prior, 1e-9, 1 - 1e-9))
    pi = pi_tr
    for _ in range(iters):
        w1, w0 = pi / pi_tr, (1 - pi) / (1 - pi_tr)
        q = p * w1 / (p * w1 + (1 - p) * w0)
        new = float(q.mean())
        if abs(new - pi) < tol:
            return new
        pi = new
    return pi


def multiclass_auc(true: np.ndarray, proba: np.ndarray, classes: tuple[str, ...]) -> float | None:
    """One-vs-rest AUC (weighted by support) over the types present in ``true``.

    Rows whose type the model never saw are left out; the probabilities of the types
    present are renormalised. None when fewer than two known types are present.
    """
    true = np.asarray(true).astype(str)
    names = [str(c) for c in classes]
    present = [c for c in sorted(set(true.tolist())) if c in names]
    if len(present) < 2:
        return None
    rows = np.isin(true, present)
    P = np.asarray(proba)[rows][:, [names.index(c) for c in present]]
    total = P.sum(axis=1, keepdims=True)
    P = np.divide(P, total, out=np.full_like(P, 1.0 / len(present)), where=total > 0)
    t = true[rows]
    if len(present) == 2:
        return float(roc_auc_score(t == present[1], P[:, 1]))
    return float(roc_auc_score(t, P, multi_class="ovr", average="weighted", labels=present))


def type_metrics(true: np.ndarray, recorded: np.ndarray, attack: np.ndarray) -> dict:
    """Type metrics on the true attacks; untyped, untyped_normal and uncertain count as wrong."""
    t = np.asarray(true).astype(str)[attack]
    r = np.asarray(recorded).astype(str)[attack]
    if len(t) == 0:
        return {"n_attacks": 0}
    labels = sorted(set(t.tolist()))
    per = {}
    for c in labels:
        tp = int(((r == c) & (t == c)).sum())
        pred_c, true_c = int((r == c).sum()), int((t == c).sum())
        prec = tp / pred_c if pred_c else 0.0
        rec = tp / true_c if true_c else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per[c] = {"precision": prec, "recall": rec, "f1": f1, "support": true_c}
    return {
        "n_attacks": len(t),
        "accuracy": float((r == t).mean()),
        "macro_f1": float(np.mean([p["f1"] for p in per.values()])),
        "per_type": per,
        "share_recorded": {k: float((r == k).mean()) for k in sorted(set(r.tolist()) - set(labels))},
    }


def unseen_metrics(y: np.ndarray, unseen: np.ndarray, pred: np.ndarray) -> dict:
    """Recall on unseen attacks, recall on known attacks and benign FPR of one system."""
    y = np.asarray(y).astype(bool)
    u = np.asarray(unseen).astype(bool) & y
    p = np.asarray(pred).astype(bool)
    known = y & ~u
    return {
        "recall_unseen": float(p[u].mean()) if u.any() else None,
        "recall_known": float(p[known].mean()) if known.any() else None,
        "fpr_benign": float(p[~y].mean()) if (~y).any() else None,
        "n_unseen": int(u.sum()),
    }
