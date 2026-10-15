# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Greedy forward selection on VAL (DESIGN.md section 5).

Stages are visited in a fixed order. While a stage is chosen, every later stage
is off. Candidates are picked by AUC on the VAL rows reaching the stage
(``select_by: auc``) or jointly with the decision value by the cascade objective
(``select_by: objective``). Decision values come from a grid that always holds
``off`` (except for the control first stage); ties go to ``off``, then to the
first grid value, then to the first candidate. This function sees VAL only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from idslab.config import Config
from idslab.graph import OFF, StageState, describe, route
from idslab.metrics import auc, objective


@dataclass
class Selection:
    order: list[str]
    state: dict[str, StageState]
    trace: list[dict]


# per stage, candidate index -> scores (a list of all candidates, or a dict of the chosen ones)
Scores = Mapping[str, Sequence[np.ndarray] | Mapping[int, np.ndarray]]


def active_scores(scores: Scores, state: dict[str, StageState]) -> dict[str, np.ndarray]:
    """Score array of each stage's active candidate (stages that are off are left out)."""
    return {s: scores[s][st.candidate] for s, st in state.items() if not st.off and st.candidate is not None}


def budget_threshold(scores: np.ndarray, benign: np.ndarray, budget: float) -> tuple[float, float]:
    """Threshold flagging about ``budget`` of the benign rows; returns (threshold, achieved share)."""
    ref = scores[benign]
    t = float(np.quantile(ref, 1 - budget, method="higher"))
    return t, float((ref >= t).mean())


def _state(
    kind_mode: str,
    i: int,
    value: float,
    *,
    s_scores: np.ndarray,
    reach: np.ndarray,
    y: np.ndarray,
    note: list,
) -> tuple[StageState | None, float | None]:
    """The trial state for one option; for a budget also the benign VAL share it really flags."""
    if kind_mode != "budget":
        return StageState(off=False, candidate=i, threshold=value, value=value), None
    benign = reach & (y == 0)
    if not benign.any():
        benign = y == 0
        note.append("no benign VAL rows reach the stage; budget set on all benign VAL rows")
    if not benign.any():
        note.append("VAL holds no benign rows; an alarm budget cannot be set")
        return None, None
    t, achieved = budget_threshold(s_scores, benign, value)
    if achieved > 1.5 * value:
        note.append(
            f"budget {value}: {int(benign.sum())} benign VAL rows reach the stage, so the threshold "
            f"flags {achieved:.4f} of them"
        )
    return StageState(off=False, candidate=i, threshold=t, value=value), achieved


def select(cfg: Config, scores: Scores, y_val: np.ndarray) -> Selection:
    order = cfg.order()
    n = len(y_val)
    goal = cfg.protocol.objective
    state = dict.fromkeys(order, OFF)
    trace = []
    for s in order:
        st = cfg.stages[s]
        reach = route(order, cfg.stages, active_scores(scores, state), state, n).reach[s]
        n_reach = int(reach.sum())
        entry: dict = {"stage": s, "val_rows_reaching": n_reach, "fallbacks": [], "options": []}
        pool = list(range(len(st.candidates)))
        if len(pool) > 1 and st.select_by == "auc":
            yr = y_val[reach]
            if n_reach < st.min_val_rows or len(np.unique(yr)) < 2:
                entry["fallbacks"].append(
                    "too few or one-class VAL rows for AUC; candidates chosen by objective"
                )
            else:
                aucs = [auc(yr, scores[s][i][reach]) for i in pool]
                entry["candidate_auc"] = {st.candidates[i].label: aucs[i] for i in pool}
                best = max(pool, key=lambda i: (aucs[i] if aucs[i] is not None else -1.0, -i))
                pool = [best]
        is_first = s == cfg.controls.first_stage
        allow_off = st.decision.tunable and not is_first
        if n_reach < st.min_val_rows:
            if allow_off:
                entry["fallbacks"].append(
                    f"{n_reach} VAL rows reach the stage (< {st.min_val_rows}); stage off"
                )
                entry["chosen"] = describe(st, OFF)
                trace.append(entry)
                continue
            entry["fallbacks"].append(f"{n_reach} VAL rows reach the stage; kept on (fixed or first stage)")
        options: list[tuple[int, float] | None] = [None] if allow_off else []
        # tie order: off, then the first grid value, then the first candidate
        options += [(i, v) for v in st.decision.values for i in pool]
        best_val, best_state = -np.inf, None
        for opt in options:
            note: list[str] = []
            flagged = None
            if opt is None:
                trial_state = OFF
            else:
                trial_state, flagged = _state(
                    st.decision.mode,
                    opt[0],
                    opt[1],
                    s_scores=scores[s][opt[0]],
                    reach=reach,
                    y=y_val,
                    note=note,
                )
            entry["fallbacks"].extend(n_ for n_ in note if n_ not in entry["fallbacks"])
            if trial_state is None:
                continue
            trial = {**state, s: trial_state}
            pred = route(order, cfg.stages, active_scores(scores, trial), trial, n).verdict
            val = objective(y_val, pred, goal)
            entry["options"].append(
                {
                    "candidate": None if opt is None else st.candidates[opt[0]].label,
                    "value": None if opt is None else opt[1],
                    "threshold": trial_state.threshold,
                    "benign_share_flagged": flagged,
                    goal: val,
                }
            )
            entry["fallbacks"].extend(n_ for n_ in note if n_ not in entry["fallbacks"])
            if val > best_val:
                best_val, best_state = val, trial_state
        if best_state is None:
            raise RuntimeError(f"stage {s}: no decision option to evaluate")
        state[s] = best_state
        entry["chosen"] = describe(st, best_state)
        entry[f"val_{goal}"] = best_val
        trace.append(entry)
    return Selection(order=order, state=state, trace=trace)
