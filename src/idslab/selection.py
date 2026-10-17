# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Greedy forward selection on VAL (DESIGN.md section 5).

Stages are visited in a fixed order (routes and score dependencies). While a stage
is chosen, every later stage is off. Candidates are picked by AUC on the VAL rows
reaching the stage (``select_by: auc``) or jointly with the decision value
(``select_by: objective``). Decide stages are judged by the cascade objective on
VAL; annotate stages by the type objective on the VAL rows reaching them. Decision
values come from a grid that holds ``off`` (except for the control first stage and
for fixed decisions); ties go to ``off``, then to the first value, then to the
first candidate. Scores are asked for stage by stage, so a stage that uses another
stage's scores is fitted only after that source has been chosen. VAL only.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from sklearn.metrics import f1_score

from idslab.adapters import ClassScores
from idslab.config import Config, Stage
from idslab.graph import OFF, StageState, describe, off_state, route
from idslab.metrics import auc, multiclass_auc, objective


@dataclass
class Selection:
    order: list[str]
    state: dict[str, StageState]
    trace: list[dict]


# per stage, candidate index -> score (a list of all candidates, or a dict of the chosen ones)
Scores = Mapping[str, Sequence[Any] | Mapping[int, Any]]


class ScoreSource(Protocol):
    """Gives VAL scores per candidate on demand and learns which candidate each stage keeps."""

    def val(self, stage: str) -> Sequence[Any]: ...

    def chosen(self, stage: str, candidate: int) -> None: ...


class FixedScores:
    """A ScoreSource over scores computed beforehand."""

    def __init__(self, scores: Scores):
        self.scores = scores

    def val(self, stage: str) -> Sequence[Any]:
        return list(self.scores[stage]) if isinstance(self.scores[stage], Sequence) else []

    def chosen(self, stage: str, candidate: int) -> None:
        del stage, candidate


def active_scores(scores: Scores, state: Mapping[str, StageState]) -> dict[str, Any]:
    """Score of each stage's active candidate (stages that are off are left out)."""
    return {s: scores[s][st.candidate] for s, st in state.items() if not st.off and st.candidate is not None}


def budget_threshold(scores: np.ndarray, benign: np.ndarray, budget: float) -> tuple[float, float]:
    """Threshold flagging about ``budget`` of the benign rows; returns (threshold, achieved share)."""
    ref = scores[benign]
    t = float(np.quantile(ref, 1 - budget, method="higher"))
    return t, float((ref >= t).mean())


def _state(
    mode: str, i: int, value: float, *, score: Any, reach: np.ndarray, y: np.ndarray, note: list
) -> tuple[StageState | None, float | None]:
    """The trial state for one option; for a budget also the benign VAL share it really flags."""
    if mode == "top_fraction":
        return StageState(off=False, candidate=i, threshold=None, value=value), None
    if mode != "budget":
        return StageState(off=False, candidate=i, threshold=value, value=value), None
    benign = reach & (y == 0)
    if not benign.any():
        benign = y == 0
        note.append("no benign VAL rows reach the stage; budget set on all benign VAL rows")
    if not benign.any():
        note.append("VAL holds no benign rows; an alarm budget cannot be set")
        return None, None
    t, achieved = budget_threshold(score, benign, value)
    if achieved > 1.5 * value:
        note.append(
            f"budget {value}: {int(benign.sum())} benign VAL rows reach the stage, so the threshold "
            f"flags {achieved:.4f} of them"
        )
    return StageState(off=False, candidate=i, threshold=t, value=value), achieved


def _candidate_auc(st: Stage, score: Any, reach: np.ndarray, y: np.ndarray, types: np.ndarray | None):
    if st.kind == "multiclass":
        if types is None:
            return None
        cs: ClassScores = score
        return multiclass_auc(types[reach], cs.proba[reach], cs.classes)
    if st.transductive:
        return auc(y[reach], score(reach))
    return auc(y[reach], score[reach])


def type_objective(name: str, true: np.ndarray, pred: np.ndarray, score: ClassScores | None, rows) -> float:
    """Type objective on the given rows (annotate stages)."""
    if len(true) == 0:
        return 0.0
    if name == "accuracy":
        return float(np.mean(pred == true))
    if name == "auc":
        if score is None:
            return 0.0
        return multiclass_auc(true, score.proba[rows], score.classes) or 0.0
    labels = sorted(set(true.tolist()))
    quiet: dict[str, Any] = {"zero_division": 0.0}  # a type never predicted scores 0, without a warning
    return float(f1_score(true, pred, labels=labels, average="macro", **quiet))


def fixed_on(cfg: Config) -> dict[str, StageState]:
    """With ``search: greedy_fixed``, the stages that stay on while earlier stages are judged: a fixed
    threshold, one candidate, scores of their own (no score features or hard rows), fitted once."""
    if cfg.protocol.search != "greedy_fixed":
        return {}
    out = {}
    for name in cfg.order():
        st = cfg.stages[name]
        if (
            st.kind in ("classifier", "detector")
            and not st.transductive
            and st.decision.mode == "threshold"
            and not st.decision.tunable
            and len(st.candidates) == 1
            and not cfg.sources(name)
        ):
            v = st.decision.values[0]
            out[name] = StageState(off=False, candidate=0, threshold=v, value=v)
    return out


def select(
    cfg: Config,
    source: ScoreSource | Scores,
    y_val: np.ndarray,
    types_val: np.ndarray | None = None,
) -> Selection:
    src: ScoreSource = source if hasattr(source, "chosen") else FixedScores(source)  # type: ignore[arg-type]
    order = cfg.order()
    n = len(y_val)
    goal = cfg.protocol.objective
    state: dict[str, StageState] = dict.fromkeys(order, OFF)
    table: dict[str, Sequence[Any]] = {}
    for t, fixed in fixed_on(cfg).items():  # greedy_fixed: these stages start on, not off
        state[t] = fixed
        table[t] = src.val(t)
    trace = []
    for s in order:
        st = cfg.stages[s]
        table[s] = src.val(s)
        reach = route(order, cfg.stages, active_scores(table, state), state, n).reach[s]
        n_reach = int(reach.sum())
        entry: dict = {"stage": s, "val_rows_reaching": n_reach, "fallbacks": [], "options": []}
        pool = list(range(len(st.candidates)))
        if len(pool) > 1 and st.select_by == "auc":
            enough = n_reach >= st.min_val_rows
            aucs = [_candidate_auc(st, table[s][i], reach, y_val, types_val) for i in pool] if enough else []
            if not enough or all(a is None for a in aucs):
                entry["fallbacks"].append(
                    "too few or one-class VAL rows for AUC; candidates chosen by objective"
                )
            else:
                entry["candidate_auc"] = {st.candidates[i].label: aucs[i] for i in pool}
                best = max(pool, key=lambda i: (aucs[i] if aucs[i] is not None else -1.0, -i))
                pool = [best]
        is_first = s == cfg.controls.first_stage
        allow_off = st.decision.tunable and not is_first
        annotate = st.kind == "multiclass" and st.role == "annotate"
        if n_reach < st.min_val_rows:
            if allow_off:
                entry["fallbacks"].append(
                    f"{n_reach} VAL rows reach the stage (< {st.min_val_rows}); stage off"
                )
                state[s] = off_state(pool[0])
                src.chosen(s, pool[0])
                entry["chosen"] = describe(st, state[s])
                trace.append(entry)
                continue
            entry["fallbacks"].append(f"{n_reach} VAL rows reach the stage; kept on (fixed or first stage)")
        options: list[tuple[int, float] | None] = [None] if allow_off else []
        # tie order: off, then the first value, then the first candidate
        options += [(i, v) for v in st.decision.values for i in pool]
        best_val, best_state = -np.inf, None
        for opt in options:
            note: list[str] = []
            flagged = None
            if opt is None:
                trial_state: StageState | None = off_state(pool[0])
            else:
                trial_state, flagged = _state(
                    st.decision.mode, opt[0], opt[1], score=table[s][opt[0]], reach=reach, y=y_val, note=note
                )
            entry["fallbacks"].extend(x for x in note if x not in entry["fallbacks"])
            if trial_state is None:
                continue
            trial = {**state, s: trial_state}
            routed = route(order, cfg.stages, active_scores(table, trial), trial, n)
            if annotate:
                if types_val is None:
                    raise RuntimeError(f"stage {s}: an annotate stage needs the type column")
                true = types_val[reach].astype(str)
                pred = np.where(routed.type_pred[reach] == "", "", routed.type_pred[reach]).astype(str)
                score = None if opt is None else table[s][opt[0]]
                val = type_objective(st.type_objective, true, pred, score, reach)
                metric = f"type_{st.type_objective}"
            else:
                val = objective(y_val, routed.verdict, goal)
                metric = goal
            option = {
                "candidate": None if opt is None else st.candidates[opt[0]].label,
                "value": None if opt is None else opt[1],
                "threshold": trial_state.threshold,
                metric: val,
            }
            if flagged is not None:
                option["benign_share_flagged"] = flagged
            if s in routed.pool_off:
                option["pool_off_size"] = routed.pool_off[s]
            entry["options"].append(option)
            if val > best_val:
                best_val, best_state = val, trial_state
        if best_state is None:
            raise RuntimeError(f"stage {s}: no decision option to evaluate")
        state[s] = best_state
        src.chosen(s, best_state.candidate if best_state.candidate is not None else pool[0])
        entry["chosen"] = describe(st, best_state)
        entry["val_" + (f"type_{st.type_objective}" if annotate else goal)] = best_val
        trace.append(entry)
    return Selection(order=order, state=state, trace=trace)


def joint_check(
    cfg: Config, scores: Scores, greedy: Selection, y_val: np.ndarray, *, max_combos: int
) -> dict:
    """Every combination of decision values of the decide stages chosen on VAL, as a check on greedy.

    Each stage keeps the candidate greedy selection gave it; annotate stages and fixed
    decisions keep their greedy state. Greedy's configuration wins ties. Reports whether
    a strictly better VAL combination exists (DESIGN.md section 5).
    """
    order, goal, n = greedy.order, cfg.protocol.objective, len(y_val)
    tunable = [
        s
        for s in order
        if cfg.stages[s].decision.tunable
        and not (cfg.stages[s].kind == "multiclass" and cfg.stages[s].role == "annotate")
    ]
    choices: dict[str, list[float | None]] = {}
    for s in tunable:
        allow_off = s != cfg.controls.first_stage
        choices[s] = ([None] if allow_off else []) + list(cfg.stages[s].decision.values)
    total = int(np.prod([len(v) for v in choices.values()])) if choices else 1
    base = objective(
        y_val, route(order, cfg.stages, active_scores(scores, greedy.state), greedy.state, n).verdict, goal
    )
    if total > max_combos:
        return {"evaluated": 0, "combinations": total, "skipped": f"{total} combinations exceed joint_max"}
    best_val, best_state = base, dict(greedy.state)
    for combo in itertools.product(*choices.values()):
        state = dict(greedy.state)
        ok = True
        for s, value in zip(tunable, combo, strict=True):
            kept = greedy.state[s].candidate
            cand = kept if kept is not None else 0
            if value is None:
                state[s] = off_state(cand)
                continue
            mode = cfg.stages[s].decision.mode
            reach = (
                route(order, cfg.stages, active_scores(scores, state), state, n).reach[s]
                if mode == "budget"
                else np.ones(n, bool)
            )
            trial, _ = _state(mode, cand, value, score=scores[s][cand], reach=reach, y=y_val, note=[])
            if trial is None:
                ok = False
                break
            state[s] = trial
        if not ok:
            continue
        val = objective(y_val, route(order, cfg.stages, active_scores(scores, state), state, n).verdict, goal)
        if val > best_val:
            best_val, best_state = val, state
    differs = any(best_state[s] != greedy.state[s] for s in tunable)
    return {
        "evaluated": total,
        "combinations": total,
        f"val_{goal}_greedy": base,
        f"val_{goal}_joint": best_val,
        "differs_from_greedy": differs,
        "state": best_state,
        "configuration": {s: describe(cfg.stages[s], best_state[s]) for s in order},
    }
