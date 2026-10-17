# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Compare finished experiments side by side.

An experiment is a run folder ``<out>/<name>`` holding ``report.json`` and its run units. The
comparison lists each experiment's headline figures from its report, and, for experiments that ran
on the same data with the same splits, a paired comparison against the first experiment: per unit,
the TEST objective of one cascade minus the other on the very same flows, with a paired bootstrap
interval. Only units the report included (fresh fingerprints) take part.
"""

from __future__ import annotations

import glob
import json
import os
from html import escape
from typing import Any

import numpy as np
import pandas as pd

from idslab.metrics import paired_bootstrap
from idslab.report import unit

STAGE_SHOWN = 6


class CompareError(ValueError):
    """An experiment folder that cannot be compared (missing report, no units)."""


def _read(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_experiment(path: str) -> dict:
    """An experiment's report and its included units, each with its folder and fingerprints."""
    path = os.path.normpath(path)
    report_path = os.path.join(path, "report.json")
    if not os.path.isfile(report_path):
        raise CompareError(f"{path}: no report.json (run the experiment or `idslab report` first)")
    rep = _read(report_path)
    wanted = set(rep.get("units") or [])
    units = {}
    pattern = os.path.join(path, rep["mode"], "seed_*", "**", "summary.json")
    for summary_path in sorted(glob.glob(pattern, recursive=True)):
        d = os.path.dirname(summary_path)
        done = os.path.join(d, "DONE")
        if not os.path.isfile(done):
            continue
        s = _read(summary_path)
        label = unit(s)
        if label in wanted:
            units[label] = {"dir": d, "fingerprints": _read(done).get("fingerprints", {})}
    if not units:
        raise CompareError(f"{path}: the report lists no units that are still on disk")
    return {"path": path, "report": rep, "units": units}


def _mean(d: dict | None) -> float | None:
    return None if not d else d.get("mean")


def _headline(e: dict) -> dict:
    rep = e["report"]
    goal = rep["objective"]
    st = rep["systems_test"]
    frozen = rep.get("frozen_per_seed") or {}
    stages = {}
    for st_name in rep.get("stage_removal", {}) | {s: None for f in frozen.values() for s in f}:
        on = sum(1 for f in frozen.values() if st_name in f and not f[st_name].get("off", True))
        stages[st_name] = {
            "on": on,
            "units": len(frozen),
            "removal": rep.get("stage_removal", {}).get(st_name),
        }
    sc = rep.get("stage_cost")
    return {
        "path": e["path"],
        "name": rep["config_name"],
        "mode": rep["mode"],
        "objective": goal,
        "idslab_version": rep.get("idslab_version"),
        "units": sorted(e["units"]),
        "cascade": st["cascade"].get(goal),
        "first_stage": st["B1_first_stage"].get(goal),
        "minus_first": (rep.get("cascade_minus_control", {}).get("B1_first_stage") or {}).get("diff"),
        "frontier_gap": (rep.get("frontier_gap") or {}).get("interp"),
        "stages": stages,
        "expected_cost": None if not sc else sc.get("cascade_cost"),
        "first_stage_cost": None if not sc else sc.get("first_stage_cost"),
    }


def _pair(base: dict, other: dict, goal: str, *, bootstrap: int) -> dict:
    """Per unit, other's cascade minus base's cascade on the same TEST flows."""
    common = sorted(set(base["units"]) & set(other["units"]))
    rows, skipped = [], []
    for label in common:
        fa, fb = base["units"][label]["fingerprints"], other["units"][label]["fingerprints"]
        if fa.get("split") != fb.get("split") or fa.get("data") != fb.get("data"):
            skipped.append(f"{label}: different data or splits")
            continue
        pa = pd.read_parquet(os.path.join(base["units"][label]["dir"], "per_flow.parquet"))
        pb = pd.read_parquet(os.path.join(other["units"][label]["dir"], "per_flow.parquet"))
        merged = pa[["row_id", "y", "pred_cascade"]].merge(
            pb[["row_id", "pred_cascade"]], on="row_id", suffixes=("_base", "_other")
        )
        if len(merged) != len(pa) or len(merged) != len(pb):
            skipped.append(f"{label}: the TEST flows differ")
            continue
        seed = int(label.split("/")[0])
        res = paired_bootstrap(
            merged["y"].to_numpy(),
            merged["pred_cascade_other"].to_numpy(),
            merged["pred_cascade_base"].to_numpy(),
            n=bootstrap,
            seed=seed,
            names=(goal,),
        )[goal]
        rows.append({"unit": label, "diff": res["diff"], "ci95": res["ci95"]})
    diffs = [r["diff"] for r in rows]
    return {
        "base": base["report"]["config_name"],
        "other": other["report"]["config_name"],
        "objective": goal,
        "per_unit": rows,
        "mean_diff": float(np.mean(diffs)) if diffs else None,
        "units_ci_above_zero": sum(1 for r in rows if r["ci95"] and r["ci95"][0] > 0),
        "units_ci_below_zero": sum(1 for r in rows if r["ci95"] and r["ci95"][1] < 0),
        "skipped": skipped
        + [f"{u}: only in one experiment" for u in sorted(set(base["units"]) ^ set(other["units"]))],
    }


def compare(paths: list[str], *, bootstrap: int = 1000, names: dict[str, str] | None = None) -> dict:
    """Compare experiments; the first one is the baseline of the paired comparisons."""
    if len(paths) < 2:
        raise CompareError("give at least two experiments to compare")
    exps = [load_experiment(p) for p in paths]
    heads = [_headline(e) for e in exps]
    if names:
        for h in heads:
            h["label"] = names.get(h["path"], h["name"])
    warnings = []
    goals = {h["objective"] for h in heads}
    if len(goals) > 1:
        warnings.append(
            f"the experiments use different objectives ({', '.join(sorted(goals))}); "
            "paired differences are shown only for experiments with the baseline's objective"
        )
    modes = {h["mode"] for h in heads}
    if len(modes) > 1:
        warnings.append(f"the experiments use different modes ({', '.join(sorted(modes))})")
    datas = {
        json.dumps(next(iter(e["units"].values()))["fingerprints"].get("data"), sort_keys=True) for e in exps
    }
    if len(datas) > 1:
        warnings.append("the experiments ran on different data files; their numbers are not comparable")
    base = exps[0]
    goal = heads[0]["objective"]
    pairs = [
        _pair(base, e, goal, bootstrap=bootstrap)
        for e, h in zip(exps[1:], heads[1:], strict=True)
        if h["objective"] == goal
    ]
    return {
        "baseline": heads[0]["name"],
        "objective": goal,
        "experiments": heads,
        "paired": pairs,
        "warnings": warnings,
        "bootstrap": bootstrap,
    }


# ---------- output ----------


def _f(d: Any, digits: int = 4, signed: bool = False) -> str:
    v = d.get("mean") if isinstance(d, dict) else d
    if v is None:
        return "n/a"
    s = f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"
    sd = d.get("sd") if isinstance(d, dict) else None
    return s + (f" ± {sd:.{digits}f}" if sd is not None else "")


def _label(h: dict) -> str:
    return h.get("label") or h["name"]


def to_markdown(c: dict) -> str:
    goal = c["objective"]
    lines = [
        "# IDSLab comparison",
        "",
        f"Baseline: `{c['baseline']}`. TEST {goal}, mean ± sd over units.",
        "",
    ]
    lines += [f"- warning: {w}" for w in c["warnings"]]
    if c["warnings"]:
        lines.append("")
    lines += [
        "## Experiments",
        "",
        "| experiment | units | cascade | first stage alone | cascade minus first stage | frontier gap | expected cost per flow |",
        "|---|---|---|---|---|---|---|",
    ]
    for h in c["experiments"]:
        lines.append(
            f"| {_label(h)} | {len(h['units'])} | {_f(h['cascade'])} | {_f(h['first_stage'])} | "
            f"{_f(h['minus_first'], signed=True)} | {_f(h['frontier_gap'], signed=True)} | {_f(h['expected_cost'], 3)} |"
        )
    lines += ["", "## Stages", "", "| experiment | stage | kept on | lost if removed |", "|---|---|---|---|"]
    for h in c["experiments"]:
        for st, r in list(h["stages"].items())[:STAGE_SHOWN]:
            lines.append(
                f"| {_label(h)} | {st} | {r['on']} of {r['units']} | {_f(r['removal'], 3, signed=True)} |"
            )
    if c["paired"]:
        lines += [
            "",
            f"## Paired against {c['baseline']}",
            "",
            (
                "Same data and splits, so each unit compares the two cascades on the same TEST flows: "
                "experiment minus baseline, with a paired bootstrap 95% interval."
            ),
            "",
            "| experiment | mean difference | units with CI above 0 | units with CI below 0 | per unit |",
            "|---|---|---|---|---|",
        ]
        for p in c["paired"]:
            per = "; ".join(
                f"{r['unit']}: {r['diff']:+.4f}"
                + (f" [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}]" if r["ci95"] else "")
                for r in p["per_unit"]
            )
            n = len(p["per_unit"])
            lines.append(
                f"| {p['other']} | {_f(p['mean_diff'], signed=True)} | {p['units_ci_above_zero']}/{n} | "
                f"{p['units_ci_below_zero']}/{n} | {per or 'none'} |"
            )
            lines += [f"|  | skipped: {s} |  |  |  |" for s in p["skipped"]]
    return "\n".join(lines) + "\n"


CSS = """
:root{--bg:#f6f8fa;--panel:#fff;--ink:#1f2328;--muted:#59636e;--line:#d0d7de;--accent:#0969da;--good:#1a7f37;--bad:#cf222e}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--panel:#161b22;--ink:#e6edf3;--muted:#9198a1;--line:#30363d;--accent:#4493f8;--good:#3fb950;--bad:#f85149}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 20px}
h1{margin:0 0 6px;font-size:28px}h2{margin:34px 0 10px;font-size:19px}
.muted{color:var(--muted)}
.warn{border:1px solid #d4a72c;background:rgba(212,167,44,.1);border-radius:8px;padding:8px 12px;margin:8px 0}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line);border-radius:8px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}
th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
td.n{text-align:right;font-family:ui-monospace,Consolas,monospace;white-space:nowrap}
.pos{color:var(--good)}.neg{color:var(--bad)}
.bar{fill:var(--accent)}.bar.first{fill:var(--muted)}.axis{stroke:var(--line)}
svg text{fill:var(--muted);font-size:12px;font-family:inherit}
"""


def _cls(v: float | None) -> str:
    return "" if v is None else "pos" if v > 0 else "neg" if v < 0 else ""


def _chart(c: dict) -> str:
    """Cascade and first stage alone per experiment, as horizontal bars."""
    heads = c["experiments"]
    vals = [v for h in heads for v in (_mean(h["cascade"]), _mean(h["first_stage"])) if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.25 or 0.01
    lo, hi = lo - pad, hi + pad
    W, row = 760, 46
    H = 20 + row * len(heads)
    x0, x1 = 230, W - 90
    parts = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Cascade and first stage alone per experiment">'
    ]
    for i, h in enumerate(heads):
        y = 10 + i * row
        parts.append(f'<text x="0" y="{y + 20}">{escape(_label(h)[:30])}</text>')
        for j, (key, cls) in enumerate((("cascade", "bar"), ("first_stage", "bar first"))):
            v = _mean(h[key])
            if v is None:
                continue
            w = (v - lo) / (hi - lo) * (x1 - x0)
            parts.append(
                f'<rect class="{cls}" x="{x0}" y="{y + j * 16}" width="{max(w, 1):.1f}" height="13" rx="3"/>'
            )
            parts.append(
                f'<text x="{x0 + w + 6:.1f}" y="{y + j * 16 + 11}">{v:.4f}{" (first stage)" if j else ""}</text>'
            )
    parts.append(f'<line class="axis" x1="{x0}" x2="{x0}" y1="4" y2="{H - 4}"/></svg>')
    return "".join(parts)


def render_html(c: dict) -> str:
    goal = escape(c["objective"])
    warn = "".join(f'<div class="warn">{escape(w)}</div>' for w in c["warnings"])
    rows = "".join(
        f"<tr><td>{escape(_label(h))}</td><td class=n>{len(h['units'])}</td><td class=n>{escape(_f(h['cascade']))}</td>"
        f"<td class=n>{escape(_f(h['first_stage']))}</td>"
        f"<td class='n {_cls(_mean(h['minus_first']))}'>{escape(_f(h['minus_first'], signed=True))}</td>"
        f"<td class='n {_cls(_mean(h['frontier_gap']))}'>{escape(_f(h['frontier_gap'], signed=True))}</td>"
        f"<td class=n>{escape(_f(h['expected_cost'], 3))}</td></tr>"
        for h in c["experiments"]
    )
    stage_rows = "".join(
        f"<tr><td>{escape(_label(h))}</td><td>{escape(st)}</td><td class=n>{r['on']} of {r['units']}</td>"
        f"<td class='n {_cls(_mean(r['removal']))}'>{escape(_f(r['removal'], 3, signed=True))}</td></tr>"
        for h in c["experiments"]
        for st, r in list(h["stages"].items())[:STAGE_SHOWN]
    )
    paired = ""
    if c["paired"]:
        prow = []
        for p in c["paired"]:
            n = len(p["per_unit"])
            per = (
                "<br>".join(
                    escape(
                        f"{r['unit']}: {r['diff']:+.4f}"
                        + (f" [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}]" if r["ci95"] else "")
                    )
                    for r in p["per_unit"]
                )
                or "none"
            )
            skipped = "".join(f'<div class="muted">skipped {escape(s)}</div>' for s in p["skipped"])
            prow.append(
                f"<tr><td>{escape(p['other'])}</td><td class='n {_cls(p['mean_diff'])}'>{escape(_f(p['mean_diff'], signed=True))}</td>"
                f"<td class=n>{p['units_ci_above_zero']}/{n}</td><td class=n>{p['units_ci_below_zero']}/{n}</td>"
                f"<td class=n>{per}{skipped}</td></tr>"
            )
        paired = (
            f"<h2>Paired against {escape(c['baseline'])}</h2><p class=muted>Same data and splits, so each unit "
            "compares the two cascades on the same TEST flows: experiment minus baseline, with a paired bootstrap "
            "95% interval.</p><div class=scroll><table><tr><th>experiment</th><th>mean difference</th>"
            f"<th>CI above 0</th><th>CI below 0</th><th>per unit</th></tr>{''.join(prow)}</table></div>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IDSLab comparison</title><style>{CSS}</style></head><body><main>
<h1>IDSLab comparison</h1><p class=muted>Baseline {escape(c["baseline"])}. TEST {goal}, mean ± sd over units.</p>
{warn}
<h2>Cascade and first stage alone</h2>{_chart(c)}
<div class=scroll><table><tr><th>experiment</th><th>units</th><th>cascade</th><th>first stage alone</th>
<th>cascade minus first stage</th><th>frontier gap</th><th>expected cost per flow</th></tr>{rows}</table></div>
{paired}
<h2>Stages</h2><div class=scroll><table><tr><th>experiment</th><th>stage</th><th>kept on</th><th>lost if removed</th></tr>{stage_rows}</table></div>
</main></body></html>
"""


def write_comparison(c: dict, out_dir: str) -> tuple[str, str, str]:
    """Write compare.md, compare.json and compare.html into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    md, js, html = (os.path.join(out_dir, f"compare.{ext}") for ext in ("md", "json", "html"))
    with open(md, "w", encoding="utf-8") as f:
        f.write(to_markdown(c))
    with open(js, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2, default=str)
    with open(html, "w", encoding="utf-8") as f:
        f.write(render_html(c))
    return md, js, html
