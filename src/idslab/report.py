# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Aggregate seed summaries into report.md and report.json."""

from __future__ import annotations

import glob
import json
import os
from collections.abc import Callable, Collection, Sequence

import numpy as np

from idslab.config import Config
from idslab.html_report import render
from idslab.metrics import shown
from idslab import __version__
from idslab.runner import data_fingerprints, external_fingerprints, write_json

SYSTEMS = ("cascade", "B1_first_stage", "B2_first_stage_val_cutoff", "budget_matched_first_stage")


def load_summaries(
    mode_dir: str,
    expected: dict | None = None,
    seeds: Collection[int] | None = None,
    external: Callable[[int, str | None], dict] | None = None,
) -> tuple[list[dict], list[str]]:
    """Finished seed summaries that match ``expected`` fingerprints and ``seeds``.

    ``expected`` holds the config, data and package-version fingerprints a seed's
    DONE file must carry; ``external`` gives the current fingerprints of a unit's external
    score files and sidecars, which must match too. The second value lists every unit left
    out and why.
    """
    kept, left_out = [], []
    for path in sorted(glob.glob(os.path.join(mode_dir, "seed_*", "**", "summary.json"), recursive=True)):
        done = os.path.join(os.path.dirname(path), "DONE")
        if not os.path.isfile(done):
            continue
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        with open(done, encoding="utf-8") as f:
            fps = json.load(f).get("fingerprints", {})
        stale = [k for k, v in (expected or {}).items() if fps.get(k) != v]
        if external is not None and fps.get("external", {}) != external(s["seed"], s.get("family")):
            stale.append("external score files")
        if seeds is not None and s["seed"] not in seeds:
            left_out.append(f"seed {s['seed']}: not in the seeds of this report")
        elif stale:
            left_out.append(
                f"seed {s['seed']}: {', '.join(stale)} changed since it ran; rerun it to include it"
            )
        else:
            kept.append(s)
    return sorted(kept, key=lambda s: (s["seed"], s.get("family") or "")), left_out


def unit(s: dict) -> str:
    """Label of a run unit: the seed, or seed/family in held-out mode."""
    return str(s["seed"]) if s.get("family") is None else f"{s['seed']}/{s['family']}"


def _ms(values: Sequence[float | None]) -> dict:
    v = np.asarray([x for x in values if x is not None], dtype=float)
    if len(v) == 0:
        return {"mean": None, "sd": None, "n": 0}
    return {"mean": float(v.mean()), "sd": float(v.std(ddof=1)) if len(v) > 1 else None, "n": len(v)}


def _fmt(d: dict, digits: int = 4) -> str:
    if d["mean"] is None:
        return "n/a"
    return f"{d['mean']:.{digits}f}" + (f" ± {d['sd']:.{digits}f}" if d["sd"] is not None else "")


def _fmt_opt(d: dict | None) -> str:
    return "n/a" if d is None else _fmt(d)


def _statement_order(text: str) -> tuple[int, str]:
    return (0 if text.startswith("TEST labels") else 1, text)


def _types(summaries: list[dict]) -> dict | None:
    typed = [s["controls"].get("types_test") for s in summaries]
    if not any(typed):
        return None
    cascade = [t["cascade"] if t else {} for t in typed]
    names = sorted({c for t in cascade for c in t.get("per_type", {})})
    return {
        "seeds_with_types": sum(1 for t in typed if t),
        "accuracy": _ms([t.get("accuracy") for t in cascade]),
        "macro_f1": _ms([t.get("macro_f1") for t in cascade]),
        "per_type_f1": {c: _ms([t.get("per_type", {}).get(c, {}).get("f1") for t in cascade]) for c in names},
        "per_type_support": {
            c: _ms([t.get("per_type", {}).get(c, {}).get("support") for t in cascade]) for c in names
        },
    }


UNSEEN_SYSTEMS = ("cascade", "B1_first_stage", "budget_matched_first_stage")


def _unseen(summaries: list[dict]) -> dict | None:
    """Unseen-attack results grouped by held-out family (or one group for natural novelty)."""
    groups: dict[str, list[dict]] = {}
    for s in summaries:
        block = s["controls"].get("unseen_test")
        if block:
            groups.setdefault(s.get("family") or "novel types", []).append(block)
    if not groups:
        return None
    out = {}
    for name, blocks in groups.items():
        gaps = [b.get("frontier_gap_unseen") or {} for b in blocks]
        out[name] = {
            "runs": len(blocks),
            "n_unseen": _ms([b["n_unseen"] for b in blocks]),
            "recall_unseen": {
                k: _ms([b["systems"][k]["recall_unseen"] for b in blocks]) for k in UNSEEN_SYSTEMS
            },
            "fpr_cascade": _ms([b["systems"]["cascade"]["fpr_benign"] for b in blocks]),
            "gap_interp": _ms([g.get("gap_interp") for g in gaps]),
            "gap_ci_above_zero": sum(1 for g in gaps if g.get("ci95_interp") and g["ci95_interp"][0] > 0),
            "first_stage_auc": _ms([b.get("first_stage_auc_unseen_vs_benign") for b in blocks]),
        }
    return out


def _joint(summaries: list[dict]) -> dict | None:
    blocks = [s["controls"].get("joint_check") for s in summaries]
    if not any(blocks):
        return None
    return {
        "runs": sum(1 for b in blocks if b),
        "differs": sum(1 for b in blocks if b and b.get("differs_from_greedy")),
        "skipped": sum(1 for b in blocks if b and "skipped" in b),
    }


def aggregate(summaries: list[dict]) -> dict:
    goal = summaries[0]["controls"]["objective"]
    names = list(SYSTEMS) + [
        k for k in summaries[0]["controls"]["systems_test"] if k not in SYSTEMS and k != "type_recorded"
    ]
    names = [k for k in names if all(k in s["controls"]["systems_test"] for s in summaries)]
    systems = {
        name: {m: _ms([s["controls"]["systems_test"][name][m] for s in summaries]) for m in shown(goal)}
        for name in names
    }
    diffs = {}
    for name in names[1:]:
        per = [s["controls"]["bootstrap_cascade_minus"][name][goal] for s in summaries]
        above = sum(1 for p in per if p["ci95"] is not None and p["ci95"][0] > 0)
        below = sum(1 for p in per if p["ci95"] is not None and p["ci95"][1] < 0)
        diffs[name] = {"diff": _ms([p["diff"] for p in per]), "ci_above_zero": above, "ci_below_zero": below}
    frontier = [s["controls"]["frontier_gap_test"] for s in summaries]
    removal: dict[str, list[float]] = {}
    steps: dict[str, list[float]] = {}
    for s in summaries:
        for stage, r in s["controls"]["stage_removal_test"].items():
            removal.setdefault(stage, []).append(r[f"cascade_minus_removed_{goal}"])
        prefix = s["controls"]["prefix_test"]
        for k in range(1, len(prefix)):
            name = s["selection_trace"][k - 1]["stage"] if k - 1 < len(s["selection_trace"]) else str(k)
            steps.setdefault(name, []).append(prefix[k][f"step_{goal}"])
    return {
        "config_name": summaries[0]["config_name"],
        "mode": summaries[0]["mode"],
        "objective": goal,
        "first_stage": summaries[0]["controls"]["first_stage"],
        "seeds": sorted({s["seed"] for s in summaries}),
        "units": [unit(s) for s in summaries],
        "idslab_version": summaries[0]["idslab_version"],
        "systems_test": systems,
        "cascade_minus_control": diffs,
        "frontier_gap": {
            "interp": _ms([f.get("gap_interp") for f in frontier]),
            "step": _ms([f.get("gap_step") for f in frontier]),
            "ci_above_zero": sum(1 for f in frontier if f.get("ci95_interp") and f["ci95_interp"][0] > 0),
            "per_seed": frontier,
        },
        "stage_removal": {k: _ms(v) for k, v in removal.items()},
        "prefix_steps": {k: _ms(v) for k, v in steps.items()},
        "frozen_per_seed": {unit(s): s["frozen_configuration"] for s in summaries},
        "prevalence_per_seed": {unit(s): s["controls"]["prevalence"] for s in summaries},
        "unseen": _unseen(summaries),
        "joint_check": _joint(summaries),
        "fallbacks": {
            unit(s): [f"{t['stage']}: {f}" for t in s["selection_trace"] for f in t["fallbacks"]]
            + s["split_log"]
            for s in summaries
        },
        "statements": sorted({x for s in summaries for x in s["statements"]}, key=_statement_order),
        "types": _types(summaries),
    }


def _choice(c: dict) -> str:
    if c["off"]:
        return f"off (all to {c['off_output']})"
    return f"{c['candidate']}, {c['decision_mode']} {c['value']:g}"


def to_markdown(a: dict) -> str:
    goal = a["objective"]
    n = len(a["units"])
    lines = [
        f"# IDSLab report: {a['config_name']}",
        "",
        (
            f"Mode `{a['mode']}`, seeds {a['seeds']} (n = {n} runs), objective `{goal}`, "
            f"first stage `{a['first_stage']}`, IDSLab {a['idslab_version']}. "
            "Values are mean ± sd over seeds on TEST."
        ),
        "",
        "## Systems on TEST",
        "",
        "| system | " + " | ".join(shown(goal)) + " |",
        "|---|" + "---|" * len(shown(goal)),
    ]
    lines.extend(
        f"| {name} | " + " | ".join(_fmt(a["systems_test"][name][m]) for m in shown(goal)) + " |"
        for name in a["systems_test"]
    )
    if goal.startswith("cost("):
        lines += ["", f"`{goal}` is the negative average cost per flow, so higher is better."]
    if goal.startswith("recall_at_fpr("):
        lines += ["", f"`{goal}` is recall when the false alarm rate is within the cap, else minus the rate."]
    sc = a.get("stage_cost")
    if sc:
        lines += [
            "",
            "## Stage cost",
            "",
            (
                "Share of TEST flows reaching each stage, measured scoring time, and the declared cost "
                "(`cost:` on the stage, 1 when not set). Expected cost per flow is the sum over stages of "
                "share reaching times declared cost."
            ),
            "",
            "| stage | flows reaching | ms per 1,000 flows | declared cost |",
            "|---|---|---|---|",
        ]
        for st, row in sc["stages"].items():
            reach, ms = _fmt(row["reach_share"]), _fmt_opt(row["ms_per_1k"])
            lines.append(f"| {st} | {reach} | {ms} | {row['declared_cost']:g} |")
        lines += [
            "",
            (
                f"- expected cost per flow: cascade {_fmt(sc['cascade_cost'])}, "
                f"first stage alone {sc['first_stage_cost']:g}"
            ),
            (
                f"- expected scoring time per 1,000 flows: cascade {_fmt(sc['cascade_ms_per_1k'])} ms, "
                f"first stage alone {_fmt_opt(sc['first_stage_ms_per_1k'])} ms"
            ),
        ]
    jc = a.get("joint_check")
    if jc:
        lines += [
            "",
            "## Joint search check",
            "",
            (
                "Every combination of the decision values chosen on VAL, each stage keeping the candidate "
                "greedy selection gave it. A better VAL combination means greedy selection missed it; "
                "`joint_check` above is that combination on TEST."
            ),
            "",
            f"- runs where a strictly better VAL combination exists: {jc['differs']}/{jc['runs']}",
            f"- runs skipped because the combinations exceed joint_max: {jc['skipped']}",
        ]
    lines += [
        "",
        f"## Cascade minus each control ({goal})",
        "",
        "| control | difference | seeds with 95% CI above 0 | seeds with 95% CI below 0 |",
        "|---|---|---|---|",
    ]
    for name, d in a["cascade_minus_control"].items():
        lines.append(f"| {name} | {_fmt(d['diff'])} | {d['ci_above_zero']}/{n} | {d['ci_below_zero']}/{n} |")
    ty = a.get("types")
    if ty:
        lines += [
            "",
            "## Attack types on TEST",
            "",
            (
                f"Recorded type against the true type on true attacks ({ty['seeds_with_types']}/{n} seeds "
                "with an active type stage). Untyped and uncertain flows count as wrong."
            ),
            "",
            f"- accuracy: {_fmt(ty['accuracy'])}; macro F1: {_fmt(ty['macro_f1'])}",
            "",
            "| type | F1 | support |",
            "|---|---|---|",
        ]
        lines.extend(
            f"| {c} | {_fmt(ty['per_type_f1'][c])} | {_fmt(ty['per_type_support'][c], 0)} |"
            for c in ty["per_type_f1"]
        )
    un = a.get("unseen")
    if un:
        lines += [
            "",
            "## Unseen attacks on TEST",
            "",
            (
                "Attacks absent from every training and selection row (held-out family) or found only in "
                "TEST (natural novelty). The gap compares the cascade with the first stage's ROC hull on "
                "the benign and unseen flows only."
            ),
            "",
            (
                "| unseen | runs | recall cascade | recall first stage | recall budget-matched | cascade FPR "
                "| frontier gap | gap CI above 0 | first-stage AUC |"
            ),
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for name, g in un.items():
            r = g["recall_unseen"]
            lines.append(
                f"| {name} | {g['runs']} | {_fmt(r['cascade'])} | {_fmt(r['B1_first_stage'])} | "
                f"{_fmt(r['budget_matched_first_stage'])} | {_fmt(g['fpr_cascade'])} | "
                f"{_fmt(g['gap_interp'])} | {g['gap_ci_above_zero']}/{g['runs']} | "
                f"{_fmt(g['first_stage_auc'])} |"
            )
    fg = a["frontier_gap"]
    lines += [
        "",
        "## Frontier gap on TEST",
        "",
        (
            "Cascade TPR minus the first-stage ROC TPR at the cascade's FPR. Positive means the cascade "
            "reaches an operating point that moving the first-stage threshold does not."
        ),
        "",
        f"- interpolated: {_fmt(fg['interp'])}; single threshold (step): {_fmt(fg['step'])}",
        f"- seeds whose 95% interval of the interpolated gap is above 0: {fg['ci_above_zero']}/{n}",
        "",
        "## Frozen configuration per seed",
        "",
    ]
    stages = list(next(iter(a["frozen_per_seed"].values())))
    lines += ["| seed | " + " | ".join(stages) + " |", "|---|" + "---|" * len(stages)]
    for seed, fz in a["frozen_per_seed"].items():
        lines.append(f"| {seed} | " + " | ".join(_choice(fz[s]) for s in stages) + " |")
    lines += [
        "",
        f"## Stage effects ({goal})",
        "",
        (
            "| stage | cascade minus cascade with this stage off (seeds where it is on) "
            "| prefix step when this stage is added (0 where it is off) |"
        ),
        "|---|---|---|",
    ]
    for s in stages:
        rem = a["stage_removal"].get(s)
        step = a["prefix_steps"].get(s)
        rem_txt = f"{_fmt(rem)} ({rem['n']}/{n} seeds)" if rem else "off in every seed"
        lines.append(f"| {s} | {rem_txt} | {_fmt(step) if step else 'n/a'} |")
    lines += [
        "",
        "## Prevalence (attack share)",
        "",
        "| seed | FIT | VAL | TEST | EM estimate of TEST |",
        "|---|---|---|---|---|",
    ]
    for seed, p in a["prevalence_per_seed"].items():
        sh = p["attack_share"]
        em = p["em_estimate_TEST"]
        lines.append(
            f"| {seed} | {sh['FIT']:.4f} | {sh['VAL']:.4f} | {sh['TEST']:.4f} | "
            f"{'n/a' if em is None else f'{em:.4f}'} |"
        )
    notes = [f"- seed {seed}: {line}" for seed, fb in a["fallbacks"].items() for line in fb]
    notes += [f"- left out of this report: {line}" for line in a.get("seeds_left_out", [])]
    if notes:
        lines += ["", "## Fallbacks and split notes", "", *notes]
    lines += ["", "## Statements", "", *[f"- {s}" for s in a["statements"]], ""]
    return "\n".join(lines)


def stage_cost(summaries: list[dict], cfg: Config) -> dict | None:
    """Per stage: share of TEST flows reaching it and scoring time, mean over runs; expected cost per
    flow from the declared costs of the current config (so a cost change needs only a new report)."""
    rows: list[dict] = [s["stage_cost"] for s in summaries if s.get("stage_cost")]
    if len(rows) != len(summaries):
        return None  # runs from a version before stage cost was recorded
    first = cfg.controls.first_stage
    stages: dict[str, dict] = {}
    cascade_cost, cascade_ms = [], []
    for row in rows:
        total_cost, total_ms = 0.0, 0.0
        for st, r in row.items():
            declared = cfg.stages[st].cost if st in cfg.stages else 1.0
            total_cost += r["reach_share"] * declared
            if r["ms_per_1k"] is not None:
                total_ms += r["reach_share"] * r["ms_per_1k"]
            d = stages.setdefault(st, {"reach": [], "ms": [], "declared_cost": declared})
            d["reach"].append(r["reach_share"])
            if r["ms_per_1k"] is not None:
                d["ms"].append(r["ms_per_1k"])
        cascade_cost.append(total_cost)
        cascade_ms.append(total_ms)
    first_ms = [r[first]["ms_per_1k"] for r in rows if first in r and r[first]["ms_per_1k"] is not None]
    return {
        "stages": {
            st: {
                "reach_share": _ms(d["reach"]),
                "ms_per_1k": _ms(d["ms"]) if d["ms"] else None,
                "declared_cost": d["declared_cost"],
            }
            for st, d in stages.items()
        },
        "cascade_cost": _ms(cascade_cost),
        "first_stage_cost": cfg.stages[first].cost,
        "cascade_ms_per_1k": _ms(cascade_ms),
        "first_stage_ms_per_1k": _ms(first_ms) if first_ms else None,
    }


def write_report(
    run_root: str, cfg: Config, extra_seeds: Collection[int] = (), data_fps: dict[str, str] | None = None
) -> tuple[str, str]:
    """Write report.md and report.json for the seeds of ``cfg`` plus ``extra_seeds``.

    A seed counts only if it ran with this config, these data files and this IDSLab version.
    """
    mode_dir = os.path.join(run_root, cfg.name, cfg.protocol.mode)
    data = data_fps if data_fps is not None else data_fingerprints(cfg)
    expected = {"config": cfg.fingerprint(), "data": data, "package_version": __version__}
    seeds = set(cfg.protocol.seeds) | set(extra_seeds)
    summaries, left_out = load_summaries(
        mode_dir, expected, seeds, external=lambda seed, family: external_fingerprints(cfg, seed, family)
    )
    if not summaries:
        why = f" ({'; '.join(left_out)})" if left_out else ""
        raise FileNotFoundError(f"no finished seeds of this config under {mode_dir}{why}")
    agg = aggregate(summaries)
    agg["seeds_left_out"] = left_out
    agg["stage_cost"] = stage_cost(summaries, cfg)
    md_path = os.path.join(run_root, cfg.name, "report.md")
    json_path = os.path.join(run_root, cfg.name, "report.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(agg))
    write_json(json_path, agg)
    with open(os.path.join(run_root, cfg.name, "report.html"), "w", encoding="utf-8") as f:
        f.write(render(cfg, agg))
    return md_path, json_path
