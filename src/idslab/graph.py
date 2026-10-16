# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Routing engine: send every flow through the stage graph to a verdict and a type.

A stage that is off sends every flow reaching it to its ``off`` output. Otherwise:

* classifier and detector: the positive output (``attack`` or ``anomalous``) when the
  score is at least the threshold. A ``top_fraction`` decision sets the threshold per
  batch so that the given share of the flows reaching the stage is flagged.
* pool detector: its scorer is called with the batch reaching it (fitted on that
  batch); a batch smaller than ``min_pool`` makes the stage act as off.
* multiclass: the most probable type, or ``uncertain`` below the confidence value.
  An annotate stage records the type and passes every flow along ``next``; a decide
  stage routes by type (types without their own route take ``default``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from idslab.adapters import ClassScores
from idslab.config import DEFAULT, OUTPUTS, TERMINALS, UNCERTAIN, Stage

PoolScorer = Callable[[np.ndarray], np.ndarray]  # boolean mask over the split -> scores of those rows
UNTYPED, UNTYPED_NORMAL = "untyped", "untyped_normal"


@dataclass(frozen=True)
class StageState:
    off: bool
    candidate: int | None = None  # kept when off, so a score source still names its model
    threshold: float | None = None  # score threshold, or the confidence value of a multiclass stage
    value: float | None = None  # the configured value (threshold, grid value, budget or fraction)


OFF = StageState(off=True)


def off_state(candidate: int | None) -> StageState:
    return OFF if candidate is None else StageState(off=True, candidate=candidate)


def describe(stage: Stage, state: StageState) -> dict:
    """The JSON form of a stage's state, used by the selection trace and the frozen configuration."""
    if state.off:
        out: dict[str, Any] = {"off": True, "off_output": stage.decision.off}
        if state.candidate is not None:
            out["score_candidate"] = stage.candidates[state.candidate].label
        return out
    return {
        "off": False,
        "candidate": stage.candidates[state.candidate or 0].label,
        "decision_mode": stage.decision.mode,
        "value": state.value,
        "threshold": state.threshold,
    }


@dataclass
class Routing:
    verdict: np.ndarray  # 1 = attack
    reach: dict[str, np.ndarray]
    exit_stage: np.ndarray  # name of the stage whose output led to the terminal
    type_pred: np.ndarray  # last type a multiclass stage gave the flow ("" if none)
    pool_off: dict[str, int] = field(default_factory=dict)  # pool stages skipped: size of the small batch


def fraction_threshold(values: np.ndarray, fraction: float) -> float:
    """Threshold flagging about ``fraction`` of ``values`` (the top scores)."""
    return float(np.quantile(values, 1 - fraction, method="higher"))


def _scalar_masks(st: Stage, s_state: StageState, score: np.ndarray, rows: np.ndarray) -> dict:
    positive, negative = OUTPUTS[st.kind]
    if st.decision.mode == "top_fraction":
        if s_state.value is None:
            raise ValueError(f"stage {st.name}: top_fraction state without a value")
        t = fraction_threshold(score[rows], s_state.value)
    else:
        if s_state.threshold is None:
            raise ValueError(f"stage {st.name} is on but has no threshold")
        t = s_state.threshold
    hit = score >= t
    return {positive: rows & hit, negative: rows & ~hit}


def route(
    order: list[str],
    stages: Mapping[str, Stage],
    scores: Mapping[str, Any],
    state: Mapping[str, StageState],
    n: int,
) -> Routing:
    """Route ``n`` flows. ``scores[s]`` is the active candidate's score for stage s: an array,
    ClassScores for a multiclass stage, or a PoolScorer for a pool detector."""
    reach = {s: np.zeros(n, dtype=bool) for s in order}
    reach[order[0]][:] = True
    verdict = np.full(n, -1, dtype=np.int8)
    exit_stage = np.empty(n, dtype=object)
    type_pred = np.full(n, "", dtype=object)
    pool_off: dict[str, int] = {}
    for s in order:
        st, rows = stages[s], reach[s]
        if not rows.any():
            continue
        s_state = state[s]
        if s_state.off:
            masks = {st.decision.off: rows}
        elif st.kind == "multiclass":
            cs: ClassScores = scores[s]
            classes = np.asarray(cs.classes, dtype=object)
            best = cs.proba.argmax(axis=1)
            name = np.where(cs.proba.max(axis=1) < (s_state.threshold or 0.0), UNCERTAIN, classes[best])
            type_pred[rows] = name[rows]
            if st.role == "annotate":
                masks = {"next": rows}
            else:
                key = np.array([k if k in st.routes else DEFAULT for k in name], dtype=object)
                masks = {k: rows & (key == k) for k in np.unique(key[rows])}
        elif st.transductive:
            size = int(rows.sum())
            if size < st.min_pool:
                pool_off[s] = size
                masks = {st.decision.off: rows}
            else:
                full = np.full(n, np.nan)
                full[rows] = scores[s](rows)
                masks = _scalar_masks(st, s_state, full, rows)
        else:
            masks = _scalar_masks(st, s_state, scores[s], rows)
        for out, mask in masks.items():
            target = st.routes[out]
            if target in TERMINALS:
                verdict[mask] = 1 if target == "ATTACK" else 0
                exit_stage[mask] = s
            else:
                reach[target] |= mask
    if (verdict < 0).any():
        raise RuntimeError("some flows reached no terminal; the routing graph is inconsistent")
    return Routing(
        verdict=verdict, reach=reach, exit_stage=exit_stage, type_pred=type_pred, pool_off=pool_off
    )


def recorded_types(routing: Routing, normal_type: str) -> np.ndarray:
    """Type per flow as reported (DESIGN.md section 3.4)."""
    t = routing.type_pred
    out = np.where(t == "", UNTYPED, t).astype(object)
    out[out == normal_type] = UNTYPED_NORMAL
    out[routing.verdict == 0] = normal_type
    return out


def with_off(state: Mapping[str, StageState], names) -> dict[str, StageState]:
    """Copy of ``state`` with the named stages switched off (their score candidate is kept)."""
    out = dict(state)
    for s in names:
        out[s] = off_state(out[s].candidate)
    return out


def paths(routing: Routing, order: list[str]) -> np.ndarray:
    """Per flow, the stages it visited, joined with '>'."""
    n = len(routing.verdict)
    parts = [np.where(routing.reach[s], s, "") for s in order]
    joined = np.full(n, "", dtype=object)
    for p in parts:
        has = p != ""
        joined[has] = np.where(joined[has] == "", p[has], joined[has] + ">" + p[has])
    return joined
