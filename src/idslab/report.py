# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Aggregate seed summaries into report.md and report.json."""

from __future__ import annotations

import glob
import json
import os
from collections.abc import Collection, Sequence

import numpy as np

from idslab.config import Config
from idslab import __version__
from idslab.runner import data_fingerprints, write_json

SYSTEMS = ("cascade", "B1_first_stage", "B2_first_stage_val_cutoff", "budget_matched_first_stage")
SHOW = ("accuracy", "recall", "fpr", "precision", "f1", "mcc", "balanced_accuracy")


def load_summaries(
    mode_dir: str, expected: dict | None = None, seeds: Collection[int] | None = None
) -> tuple[list[dict], list[str]]:
    """Finished seed summaries that match ``expected`` fingerprints and ``seeds``.

    ``expected`` holds the config, data and package-version fingerprints a seed's
    DONE file must carry; the second value lists every seed left out and why.
    """
    kept, left_out = [], []
    for path in sorted(glob.glob(os.path.join(mode_dir, "seed_*", "summary.json"))):
        done = os.path.join(os.path.dirname(path), "DONE")
        if not os.path.isfile(done):
            continue
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        with open(done, encoding="utf-8") as f:
            fps = json.load(f).get("fingerprints", {})
        stale = [k for k, v in (expected or {}).items() if fps.get(k) != v]
        if seeds is not None and s["seed"] not in seeds:
            left_out.append(f"seed {s['seed']}: not in the seeds of this report")
        elif stale:
            left_out.append(
                f"seed {s['seed']}: {', '.join(stale)} changed since it ran; rerun it to include it"
            )
        else:
            kept.append(s)
    return sorted(kept, key=lambda s: s["seed"]), left_out


def _ms(values: Sequence[float | None]) -> dict:
    v = np.asarray([x for x in values if x is not None], dtype=float)
    if len(v) == 0:
        return {"mean": None, "sd": None, "n": 0}
    return {"mean": float(v.mean()), "sd": float(v.std(ddof=1)) if len(v) > 1 else None, "n": len(v)}


def _fmt(d: dict, digits: int = 4) -> str:
    if d["mean"] is None:
        return "n/a"
    return f"{d['mean']:.{digits}f}" + (f" ± {d['sd']:.{digits}f}" if d["sd"] is not None else "")


def aggregate(summaries: list[dict]) -> dict:
    goal = summaries[0]["controls"]["objective"]
    systems = {
        name: {m: _ms([s["controls"]["systems_test"][name][m] for s in summaries]) for m in SHOW}
        for name in SYSTEMS
    }
    diffs = {}
    for name in SYSTEMS[1:]:
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
        "seeds": [s["seed"] for s in summaries],
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
        "frozen_per_seed": {s["seed"]: s["frozen_configuration"] for s in summaries},
        "prevalence_per_seed": {s["seed"]: s["controls"]["prevalence"] for s in summaries},
        "fallbacks": {
            s["seed"]: [f"{t['stage']}: {f}" for t in s["selection_trace"] for f in t["fallbacks"]]
            + s["split_log"]
            for s in summaries
        },
        "statements": summaries[0]["statements"],
    }


def _choice(c: dict) -> str:
    if c["off"]:
        return f"off (all to {c['off_output']})"
    return f"{c['candidate']}, {c['decision_mode']} {c['value']:g}"


def to_markdown(a: dict) -> str:
    goal = a["objective"]
    n = len(a["seeds"])
    lines = [
        f"# IDSLab report: {a['config_name']}",
        "",
        (
            f"Mode `{a['mode']}`, seeds {a['seeds']} (n = {n}), objective `{goal}`, "
            f"first stage `{a['first_stage']}`, IDSLab {a['idslab_version']}. "
            "Values are mean ± sd over seeds on TEST."
        ),
        "",
        "## Systems on TEST",
        "",
        "| system | " + " | ".join(SHOW) + " |",
        "|---|" + "---|" * len(SHOW),
    ]
    lines.extend(
        f"| {name} | " + " | ".join(_fmt(a["systems_test"][name][m]) for m in SHOW) + " |" for name in SYSTEMS
    )
    lines += [
        "",
        f"## Cascade minus each control ({goal})",
        "",
        "| control | difference | seeds with 95% CI above 0 | seeds with 95% CI below 0 |",
        "|---|---|---|---|",
    ]
    for name, d in a["cascade_minus_control"].items():
        lines.append(f"| {name} | {_fmt(d['diff'])} | {d['ci_above_zero']}/{n} | {d['ci_below_zero']}/{n} |")
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
    summaries, left_out = load_summaries(mode_dir, expected, seeds)
    if not summaries:
        raise FileNotFoundError(f"no finished seeds of this config under {mode_dir}")
    agg = aggregate(summaries)
    agg["seeds_left_out"] = left_out
    md_path = os.path.join(run_root, cfg.name, "report.md")
    json_path = os.path.join(run_root, cfg.name, "report.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(agg))
    write_json(json_path, agg)
    return md_path, json_path
