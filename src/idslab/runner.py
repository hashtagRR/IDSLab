# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Run units: one seed of one config, from splits to written results.

Order inside a unit, which is what keeps TEST labels out of every choice: stage
by stage, fit the candidates, score VAL and choose on VAL (a stage that feeds
others then adds its out-of-fold FIT scores and VAL scores to the feature store);
freeze; score TEST features for the chosen candidates; read TEST labels for the
first time and evaluate the cascade and its controls.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import time
from typing import Any, cast

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from idslab import __version__
from idslab.adapters import ClassScores
from idslab.config import Config, check_against_data
from idslab.controls import evaluate
from idslab.data import Splits, Table, file_fingerprint, load_table, make_splits
from idslab.features import FeatureStore, column_names
from idslab.fitting import Fitted, fit_candidate, oof_scores
from idslab.graph import PoolScorer, describe, paths
from idslab.selection import Selection, active_scores, select

POOL_STATEMENT = (
    "Pool detectors are refitted on the whole batch of flows that reaches them, so their results assume "
    "batch processing of the split; the bootstrap resamples their stored predictions without refitting."
)

STATEMENTS = [
    (
        "TEST labels did not influence fitting, selection or any configuration choice; every TEST "
        "evaluation ran after the configuration was frozen."
    ),
    (
        "Greedy forward selection: a stage that is off was not chosen by this procedure; it may still "
        "help in combination with a later stage."
    ),
    (
        "Stage removal switches the stage's decision off with everything else as selected; removal "
        "effects need not add up. Prefix steps add up to the change from no stages to the full cascade."
    ),
    (
        "Bootstrap intervals describe TEST-sample uncertainty for the fitted system; seed variation is "
        "reported separately."
    ),
]


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return to_jsonable(obj.tolist())
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(to_jsonable(obj), f, indent=2, sort_keys=False)
    os.replace(tmp, path)


def data_fingerprints(cfg: Config) -> dict[str, str]:
    """Hash of each data file, keyed by its role (train, test)."""
    files = {"train": cfg.dataset.train, "test": cfg.dataset.test}
    return {role: file_fingerprint(cfg.path(p)) for role, p in files.items() if p is not None}


def unit_dir(out_root: str, cfg: Config, seed: int) -> str:
    return os.path.join(out_root, cfg.name, cfg.protocol.mode, f"seed_{seed}")


class Provider:
    """Fits and scores stages on demand for selection, and keeps the feature store filled.

    VAL scores are given stage by stage; when selection keeps a candidate for a stage
    that feeds others, its out-of-fold FIT scores and VAL scores enter the store.
    TEST scores are produced only through ``test``, which runs after the freeze.
    """

    def __init__(self, cfg: Config, table: Table, split: Splits, *, seed: int, n_jobs: int):
        self.cfg, self.table, self.split, self.seed, self.n_jobs = cfg, table, split, seed, n_jobs
        self.store = FeatureStore(table.X)
        self.fitted: dict[str, list[Fitted]] = {}
        self.val_scores: dict[str, list[Any]] = {}
        self.feeds = {src for n in cfg.stages for src in cfg.sources(n)}
        self.choice: dict[str, int] = {}
        self.test_filled: set[str] = set()

    def _fit(self, s: str) -> list[Fitted]:
        if s not in self.fitted:
            st = self.cfg.stages[s]
            self.fitted[s] = [
                fit_candidate(st, i, self.table, self.split, self.store, seed=self.seed, n_jobs=self.n_jobs)
                for i in range(len(st.candidates))
            ]
        return self.fitted[s]

    def _pool(self, f: Fitted, rows: np.ndarray) -> PoolScorer:
        cache: dict[bytes, np.ndarray] = {}
        X = self.table.X

        def scorer(mask: np.ndarray) -> np.ndarray:
            key = np.packbits(mask).tobytes()
            if key not in cache:
                cache[key] = f.pool_score(X.iloc[rows[mask]])
            return cache[key]

        return scorer

    def val(self, stage: str) -> list[Any]:
        if stage not in self.val_scores:
            st, fl = self.cfg.stages[stage], self._fit(stage)
            if st.transductive:
                self.val_scores[stage] = [self._pool(f, self.split.val) for f in fl]
            else:
                X = self.store.frame(st.extra_features, self.split.val)
                self.val_scores[stage] = [f.score(X) for f in fl]
        return self.val_scores[stage]

    def chosen(self, stage: str, candidate: int) -> None:
        self.choice[stage] = candidate
        if stage not in self.feeds or self.store.has(stage):
            return
        st, f = self.cfg.stages[stage], self._fit(stage)[candidate]
        self.store.declare(stage, column_names(stage, f.classes()))
        oof = oof_scores(
            st,
            f,
            self.table,
            self.split,
            self.store,
            folds=self.cfg.protocol.oof_folds,
            seed=self.seed,
            n_jobs=self.n_jobs,
        )
        self.store.put(stage, self.split.fit, oof)
        self.store.put(
            stage, self.split.val, f.raw_score(self.store.frame(st.extra_features, self.split.val))
        )

    def _fill_test(self, src: str) -> None:
        """TEST feature scores of a source (after the freeze)."""
        if src in self.test_filled:
            return
        for upstream in self.cfg.sources(src):
            self._fill_test(upstream)
        st = self.cfg.stages[src]
        f = self._fit(src)[self.choice[src]]
        self.store.put(
            src, self.split.test, f.raw_score(self.store.frame(st.extra_features, self.split.test))
        )
        self.test_filled.add(src)

    def test(self, stage: str, candidate: int) -> Any:
        """The stage's TEST score for one candidate; call only after the configuration is frozen."""
        for src in self.cfg.sources(stage):
            self._fill_test(src)
        st, f = self.cfg.stages[stage], self._fit(stage)[candidate]
        if st.transductive:
            return self._pool(f, self.split.test)
        return f.score(self.store.frame(st.extra_features, self.split.test))


def frozen_config(cfg: Config, sel: Selection) -> dict:
    return {s: describe(cfg.stages[s], sel.state[s]) for s in sel.order}


def run_seed(
    cfg: Config, table: Table, seed: int, *, data_fps: dict, out_root: str, force: bool, n_jobs: int = 1
) -> dict:
    t0 = time.perf_counter()
    split: Splits = make_splits(table, cfg.protocol, cfg.dataset.test_fraction, seed)
    fps = {
        "config": cfg.fingerprint(),
        "data": data_fps,
        "split": split.fingerprint(table.row_id),
        "package_version": __version__,
    }
    d = unit_dir(out_root, cfg, seed)
    done = os.path.join(d, "DONE")
    if not force and os.path.isfile(done):
        try:
            with open(done, encoding="utf-8") as f:
                same = json.load(f).get("fingerprints") == fps
        except (OSError, ValueError):
            same = False  # unreadable DONE: run the seed again
        if same:
            try:
                with open(os.path.join(d, "summary.json"), encoding="utf-8") as g:
                    return {"seed": seed, "skipped": True, "summary": json.load(g)}
            except (OSError, ValueError):
                pass  # summary missing or unreadable: run the seed again
    os.makedirs(d, exist_ok=True)
    if os.path.exists(done):
        os.remove(done)

    order = cfg.order()
    provider = Provider(cfg, table, split, seed=seed, n_jobs=n_jobs)
    y_val = table.y[split.val]
    types_val = None if table.types is None else table.types[split.val].astype(str)

    sel = select(cfg, provider, y_val, types_val)
    frozen = frozen_config(cfg, sel)
    fitted, val_scores = provider.fitted, provider.val_scores
    # TEST features are scored only now, and only for the chosen candidates
    test_scores = {
        s: {st.candidate: provider.test(s, st.candidate)}
        for s, st in sel.state.items()
        if not st.off and st.candidate is not None
    }

    y_test = table.y[split.test]  # first use of TEST labels, after the freeze
    types_test = None if table.types is None else table.types[split.test].astype(str)
    first = cfg.controls.first_stage
    first_fit = fitted[first][sel.state[first].candidate or 0]
    if first_fit.prior_share is None:
        raise RuntimeError("the control first stage must be a classifier with a training prior")
    shares = {"FIT": float(table.y[split.fit].mean()), "VAL": float(y_val.mean())}
    controls, preds, routing = evaluate(
        cfg,
        sel,
        val_scores=val_scores,
        test_scores=test_scores,
        y_val=y_val,
        y_test=y_test,
        shares=shares,
        first_train_share=first_fit.prior_share,
        seed=seed,
        types_test=types_test,
    )

    active = active_scores(test_scores, sel.state)
    flow = pd.DataFrame({"row_id": table.row_id[split.test], "y": y_test})
    if table.types is not None:
        flow["type"] = table.types[split.test]
    for s, sc in active.items():
        if isinstance(sc, np.ndarray):
            flow[f"score_{s}"] = sc
        elif isinstance(sc, ClassScores):
            flow[f"confidence_{s}"] = sc.proba.max(axis=1)
    flow["path"] = paths(routing, order)
    flow["exit_stage"] = routing.exit_stage
    for name, p in preds.items():
        flow[f"pred_{name}"] = p
    flow.to_parquet(os.path.join(d, "per_flow.parquet"), index=False)

    vflow = pd.DataFrame({"row_id": table.row_id[split.val], "y": y_val})
    for s, fl in fitted.items():
        for f, sc in zip(fl, val_scores.get(s, []), strict=False):
            if isinstance(sc, np.ndarray):
                vflow[f"score_{s}__{f.label}"] = sc
            elif isinstance(sc, ClassScores):
                for j, c in enumerate(sc.classes):
                    vflow[f"proba_{s}__{f.label}__{c}"] = sc.proba[:, j]
    vflow.to_parquet(os.path.join(d, "val_flow.parquet"), index=False)

    ids = pd.concat(
        [
            pd.DataFrame({"row_id": table.row_id[getattr(split, part)], "split": name})
            for part, name in (("fit_base", "FIT-base"), ("cal", "CAL"), ("val", "VAL"), ("test", "TEST"))
        ],
        ignore_index=True,
    )
    ids.to_parquet(os.path.join(d, "split_ids.parquet"), index=False)

    summary = {
        "idslab_version": __version__,
        "config_name": cfg.name,
        "config_fingerprint": fps["config"],
        "mode": cfg.protocol.mode,
        "seed": seed,
        "split_sizes": {k: len(getattr(split, k)) for k in ("fit_base", "cal", "val", "test")},
        "split_log": split.log,
        "data_notes": table.notes,
        "candidates": {s: [f.info() for f in fl] for s, fl in fitted.items()},
        "selection_trace": sel.trace,
        "frozen_configuration": frozen,
        "controls": controls,
        "statements": STATEMENTS
        + (
            [POOL_STATEMENT]
            if any(cfg.stages[s].transductive and not sel.state[s].off for s in order)
            else []
        ),
        "runtime_seconds": round(time.perf_counter() - t0, 2),
    }
    write_json(os.path.join(d, "summary.json"), summary)
    write_json(
        done,
        {"fingerprints": fps, "finished": datetime.datetime.now(datetime.timezone.utc).isoformat()},
    )
    return {"seed": seed, "skipped": False, "summary": summary}


def run(
    cfg: Config,
    *,
    out_root: str = "runs",
    seeds: list[int] | None = None,
    jobs: int = 1,
    force: bool = False,
    data_fps: dict[str, str] | None = None,
) -> list[dict]:
    """Run every seed of ``cfg``; finished units with matching fingerprints are skipped."""
    table = load_table(cfg)
    check_against_data(cfg, None if table.types is None else set(np.unique(table.types).astype(str).tolist()))
    data_fps = data_fps if data_fps is not None else data_fingerprints(cfg)
    use = list(dict.fromkeys(seeds if seeds is not None else cfg.protocol.seeds))  # each seed once
    if jobs == 1 or len(use) == 1:
        return [
            run_seed(
                cfg, table, s, data_fps=data_fps, out_root=out_root, force=force, n_jobs=cfg.protocol.n_jobs
            )
            for s in use
        ]
    # seeds run in parallel, so each seed fits its models single-threaded (no nested parallelism)
    results = Parallel(n_jobs=jobs)(
        delayed(run_seed)(cfg, table, s, data_fps=data_fps, out_root=out_root, force=force, n_jobs=1)
        for s in use
    )
    return cast("list[dict]", list(results))  # run_seed always returns a dict; joblib's stubs allow None
