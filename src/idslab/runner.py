# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Run units: one seed of one config, from splits to written results.

Order inside a unit, which is what keeps TEST labels out of every choice:
fit candidates, score VAL and TEST features, select on VAL, freeze, then read
TEST labels for the first time and evaluate the cascade and its controls.
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
from idslab.config import Config
from idslab.controls import evaluate
from idslab.data import Splits, Table, file_fingerprint, load_table, make_splits
from idslab.fitting import fit_candidate
from idslab.graph import describe, paths
from idslab.selection import Selection, active_scores, select

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
    fitted = {
        s: [
            fit_candidate(cfg.stages[s], i, table, split, seed=seed, n_jobs=n_jobs)
            for i in range(len(cfg.stages[s].candidates))
        ]
        for s in order
    }
    X_val, X_test = table.X.iloc[split.val], table.X.iloc[split.test]
    val_scores = {s: [f.score(X_val) for f in fl] for s, fl in fitted.items()}
    y_val = table.y[split.val]

    sel = select(cfg, val_scores, y_val)
    frozen = frozen_config(cfg, sel)
    # TEST features are scored only now, and only for the chosen candidates
    test_scores = {
        s: {st.candidate: fitted[s][st.candidate].score(X_test)}
        for s, st in sel.state.items()
        if not st.off and st.candidate is not None
    }

    y_test = table.y[split.test]  # first use of TEST labels, after the freeze
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
    )

    active = active_scores(test_scores, sel.state)
    flow = pd.DataFrame({"row_id": table.row_id[split.test], "y": y_test})
    if table.types is not None:
        flow["type"] = table.types[split.test]
    for s, sc in active.items():
        flow[f"score_{s}"] = sc
    flow["path"] = paths(routing, order)
    flow["exit_stage"] = routing.exit_stage
    for name, p in preds.items():
        flow[f"pred_{name}"] = p
    flow.to_parquet(os.path.join(d, "per_flow.parquet"), index=False)

    vflow = pd.DataFrame({"row_id": table.row_id[split.val], "y": y_val})
    for s, fl in fitted.items():
        for f, sc in zip(fl, val_scores[s], strict=True):
            vflow[f"score_{s}__{f.label}"] = sc
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
        "statements": STATEMENTS,
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
