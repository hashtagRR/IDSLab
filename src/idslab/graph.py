# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Routing engine: send every flow through the stage graph to a verdict.

A stage with state ``off`` sends every flow that reaches it to its ``off`` output.
Otherwise a flow takes the positive output (``attack`` or ``anomalous``) when the
stage's score is at least the stage's threshold, and the other output if not.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from idslab.config import OUTPUTS, TERMINALS, Stage


@dataclass(frozen=True)
class StageState:
    off: bool
    candidate: int | None = None
    threshold: float | None = None
    value: float | None = None  # the configured value (threshold, grid value or budget fraction)


OFF = StageState(off=True)


def describe(stage: Stage, state: StageState) -> dict:
    """The JSON form of a stage's state, used by the selection trace and the frozen configuration."""
    if state.off:
        return {"off": True, "off_output": stage.decision.off}
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


def route(
    order: list[str],
    stages: dict[str, Stage],
    scores: dict[str, np.ndarray],
    state: dict[str, StageState],
    n: int,
) -> Routing:
    """Route ``n`` flows. ``scores[s]`` is the active candidate's score for stage s."""
    reach = {s: np.zeros(n, dtype=bool) for s in order}
    reach[order[0]][:] = True
    verdict = np.full(n, -1, dtype=np.int8)
    exit_stage = np.empty(n, dtype=object)
    for s in order:
        st, rows = stages[s], reach[s]
        if not rows.any():
            continue
        positive, negative = OUTPUTS[st.kind]
        state_s = state[s]
        if state_s.off:
            masks = {st.decision.off: rows}
        else:
            if state_s.threshold is None:
                raise ValueError(f"stage {s} is on but has no threshold")
            hit = scores[s] >= state_s.threshold
            masks = {positive: rows & hit, negative: rows & ~hit}
        for out, mask in masks.items():
            target = st.routes[out]
            if target in TERMINALS:
                verdict[mask] = 1 if target == "ATTACK" else 0
                exit_stage[mask] = s
            else:
                reach[target] |= mask
    if (verdict < 0).any():
        raise RuntimeError("some flows reached no terminal; the routing graph is inconsistent")
    return Routing(verdict=verdict, reach=reach, exit_stage=exit_stage)


def with_off(state: dict[str, StageState], names) -> dict[str, StageState]:
    """Copy of ``state`` with the named stages switched off."""
    out = dict(state)
    for s in names:
        out[s] = OFF
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
