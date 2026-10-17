# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Self-contained HTML report: cascade map, systems, unseen-attack grid, types, statements.

One file with inline CSS and SVG, no scripts and no network access, readable in
light and dark themes. Built from the same aggregate as report.md.
"""

from __future__ import annotations

from html import escape

from idslab.config import TERMINALS, Config
from idslab.metrics import shown

KIND_CLASS = {"classifier": "k-clf", "multiclass": "k-mc", "detector": "k-det"}
NODE_W, NODE_H, COL, GAP = 236, 74, 316, 26
CHIP_W, CHIP_H, CHIP_DROP = 92, 28, 66  # a route to BENIGN or ATTACK ends in a chip under its stage

CSS = """
:root { --bg:#f6f7f9; --surface:#fff; --fg:#16191d; --muted:#5a616c; --line:#d6dbe1;
  --clf:#2f6fc4; --clf-soft:#e3edfa; --mc:#7b55d6; --mc-soft:#eee8fb; --det:#1e9a6a; --det-soft:#e1f4ec;
  --attack:#c2412d; --benign:#2b7a4b; --good:#2f6fc4; --bad:#c2412d; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#121417; --surface:#1b1e22; --fg:#eceef1; --muted:#a3aab5; --line:#353b43;
  --clf:#6ea2ea; --clf-soft:#1c2b41; --mc:#a68ff0; --mc-soft:#2a2342; --det:#4cc495; --det-soft:#173528;
  --attack:#e8775f; --benign:#5fc08a; --good:#6ea2ea; --bad:#e8775f; color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#121417; --surface:#1b1e22; --fg:#eceef1; --muted:#a3aab5; --line:#353b43;
  --clf:#6ea2ea; --clf-soft:#1c2b41; --mc:#a68ff0; --mc-soft:#2a2342; --det:#4cc495; --det-soft:#173528;
  --attack:#e8775f; --benign:#5fc08a; --good:#6ea2ea; --bad:#e8775f; color-scheme:dark; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1120px; margin:0 auto; padding:32px 18px 64px; display:grid; gap:34px; }
h1 { margin:0; font-size:28px; } h2 { margin:0 0 10px; font-size:19px; }
p { margin:0; } .muted { color:var(--muted); }
section { display:grid; gap:10px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; }
.tile { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:14px; }
.tile b { display:block; font-size:24px; font-variant-numeric:tabular-nums; }
.scroll { overflow-x:auto; background:var(--surface); border:1px solid var(--line); border-radius:10px; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; font-size:14px; }
th, td { padding:7px 10px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { color:var(--muted); font-weight:600; }
.bar { display:inline-block; height:8px; border-radius:4px; background:var(--good); vertical-align:middle; margin-left:8px; }
svg text { fill:var(--fg); font:12px system-ui,sans-serif; }
svg .sub { fill:var(--muted); font-size:11px; }
.k-clf { fill:var(--clf-soft); stroke:var(--clf); } .k-mc { fill:var(--mc-soft); stroke:var(--mc); }
.k-det { fill:var(--det-soft); stroke:var(--det); }
.node { stroke-width:1.6; } .node.off { stroke-dasharray:5 4; opacity:.65; }
.term-a { fill:none; stroke:var(--attack); stroke-width:1.8; } .term-b { fill:none; stroke:var(--benign); stroke-width:1.8; }
.edge { fill:none; stroke:var(--muted); stroke-width:1.4; } .elabel { fill:var(--muted); font-size:11px; }
.arrow { fill:var(--muted); }
ul { margin:0; padding-left:20px; }
"""


def _f(d: dict | None, digits: int = 4) -> str:
    if not d or d.get("mean") is None:
        return "n/a"
    sd = f" ± {d['sd']:.{digits}f}" if d.get("sd") is not None else ""
    return f"{d['mean']:.{digits}f}{sd}"


def _layers(cfg: Config) -> dict[str, int]:
    """Column of each stage: the longest routing path from the entry stage."""
    order = cfg.order()
    col = dict.fromkeys(order, 0)
    for s in order:
        for t in cfg.stages[s].routes.values():
            if t in col:
                col[t] = max(col[t], col[s] + 1)
    return col


def cascade_map(cfg: Config, agg: dict) -> str:
    """Stages in columns by routing depth; routes to BENIGN and ATTACK end in chips under each
    stage, so no edge crosses a node."""
    order = cfg.order()
    col = _layers(cfg)
    rows: dict[int, list[str]] = {}
    for s in order:
        rows.setdefault(col[s], []).append(s)
    slot = NODE_H + CHIP_DROP + GAP
    height = max(len(v) for v in rows.values()) * slot + GAP
    pos = {}
    for c, names in rows.items():
        top = (height - len(names) * slot) / 2 + GAP / 2
        for i, s in enumerate(names):
            pos[s] = (30 + c * COL, top + i * slot)
    width = 30 + max(col.values()) * COL + NODE_W + 30
    frozen = agg.get("frozen_per_seed", {})
    runs = max(len(frozen), 1)
    removal = agg.get("stage_removal", {})
    parts = [
        (
            f'<svg viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="Stage graph of the cascade" '
            f'style="min-width:{min(width, 900):.0f}px;width:100%;height:auto;display:block">'
        ),
        (
            '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="arrow"/></marker></defs>'
        ),
    ]
    for s in order:
        x, y = pos[s]
        routes = list(cfg.stages[s].routes.items())
        to_stage = [(o, t) for o, t in routes if t not in TERMINALS]
        to_term = [(o, t) for o, t in routes if t in TERMINALS]
        for i, (out, t) in enumerate(to_stage):
            tx, ty = pos[t]
            x1, y1 = x + NODE_W, y + NODE_H * (i + 1) / (len(to_stage) + 1)
            x2, y2 = tx - 4, ty + NODE_H / 2
            mid = (x1 + x2) / 2
            parts.append(
                f'<path class="edge" d="M{x1:.0f},{y1:.0f} C{mid:.0f},{y1:.0f} {mid:.0f},{y2:.0f} '
                f'{x2:.0f},{y2:.0f}" marker-end="url(#arr)"/>'
            )
            parts.append(f'<text class="elabel" x="{x1 + 6:.0f}" y="{y1 - 5:.0f}">{escape(out)}</text>')
        for i, (out, t) in enumerate(to_term):
            cx = x + NODE_W / 2 + (i - (len(to_term) - 1) / 2) * (CHIP_W + 14)
            cy = y + NODE_H + CHIP_DROP - CHIP_H
            cls = "term-a" if t == "ATTACK" else "term-b"
            parts += [
                f'<path class="edge" d="M{cx:.0f},{y + NODE_H:.0f} L{cx:.0f},{cy - 4:.0f}" marker-end="url(#arr)"/>',
                f'<text class="elabel" x="{cx + 5:.0f}" y="{y + NODE_H + 18:.0f}">{escape(out)}</text>',
                f'<rect class="{cls}" x="{cx - CHIP_W / 2:.0f}" y="{cy:.0f}" width="{CHIP_W}" height="{CHIP_H}" rx="14"/>',
                f'<text x="{cx:.0f}" y="{cy + 18:.0f}" text-anchor="middle" font-weight="600" font-size="12">{t}</text>',
            ]
    for s in order:
        st, (x, y) = cfg.stages[s], pos[s]
        on = sum(1 for fz in frozen.values() if not fz.get(s, {}).get("off", True))
        kind = "pool detector" if st.transductive else st.kind
        role = f", {st.role}" if st.kind == "multiclass" else ""
        values = sorted(
            {fz[s].get("value") for fz in frozen.values() if s in fz and not fz[s]["off"]} - {None}
        )
        shown = ", ".join(f"{v:g}" for v in values[:4]) + (" ..." if len(values) > 4 else "")
        eff = removal.get(s)
        cls = KIND_CLASS[st.kind] + (" off" if on == 0 else "")
        parts += [
            f'<rect class="node {cls}" x="{x}" y="{y:.0f}" width="{NODE_W}" height="{NODE_H}" rx="9"/>',
            f'<text x="{x + 10}" y="{y + 19:.0f}" font-weight="600">{escape(s)}</text>',
            f'<text class="sub" x="{x + 10}" y="{y + 35:.0f}">{escape(kind + role)}; on in {on}/{runs} runs</text>',
            (
                f'<text class="sub" x="{x + 10}" y="{y + 51:.0f}">{escape(st.decision.mode)}'
                f"{': ' + escape(shown) if shown else ''}</text>"
            ),
            f'<text class="sub" x="{x + 10}" y="{y + 66:.0f}">effect when removed: {escape(_f(eff, 3))}</text>',
        ]
    parts.append("</svg>")
    return "\n".join(parts)


def _systems_table(agg: dict) -> str:
    goal = agg["objective"]
    cols = shown(goal)
    head = "".join(f"<th>{escape(c)}</th>" for c in cols)
    body = []
    for name, m in agg["systems_test"].items():
        mean = (m.get(goal) or {}).get("mean")
        bar = (
            f'<span class="bar" style="width:{max(0.0, min(1.0, mean)) * 90:.0f}px"></span>'
            if mean and mean > 0
            else ""
        )
        cells = "".join(f"<td>{escape(_f(m.get(c)))}</td>" for c in cols)
        body.append(f"<tr><td>{escape(name)}{bar}</td>{cells}</tr>")
    return f'<div class="scroll"><table><tr><th>system</th>{head}</tr>{"".join(body)}</table></div>'


def _stage_cost(agg: dict) -> str:
    sc = agg.get("stage_cost")
    if not sc:
        return ""
    rows = "".join(
        f"<tr><td>{escape(st)}</td><td>{escape(_f(r['reach_share'], 3))}</td>"
        f"<td>{escape(_f(r['ms_per_1k'], 3)) if r['ms_per_1k'] else 'n/a'}</td><td>{r['declared_cost']:g}</td></tr>"
        for st, r in sc["stages"].items()
    )
    first_ms = sc["first_stage_ms_per_1k"]
    return (
        '<section><h2>Stage cost</h2><p class="muted">Share of TEST flows reaching each stage, measured '
        "scoring time, and the declared cost (<code>cost:</code> on the stage, 1 when not set).</p>"
        '<div class="scroll"><table><tr><th>stage</th><th>flows reaching</th><th>ms per 1,000 flows</th>'
        f"<th>declared cost</th></tr>{rows}</table></div>"
        f"<p>Expected cost per flow: cascade <b>{escape(_f(sc['cascade_cost'], 3))}</b>, first stage alone "
        f"<b>{sc['first_stage_cost']:g}</b>. Scoring time per 1,000 flows: cascade "
        f"{escape(_f(sc['cascade_ms_per_1k'], 3))} ms, first stage alone "
        f"{escape(_f(first_ms, 3)) if first_ms else 'n/a'} ms.</p></section>"
    )


def _heat(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return ""
    if signed:
        hue, alpha = (215, min(1.0, abs(value) * 6)) if value >= 0 else (8, min(1.0, abs(value) * 6))
    else:
        hue, alpha = 215, max(0.0, min(1.0, value))
    return f' style="background:hsla({hue},70%,52%,{alpha * 0.45:.2f})"'


def _unseen_grid(agg: dict) -> str:
    un = agg.get("unseen")
    if not un:
        return ""
    rows = []
    for name, g in un.items():
        r = g["recall_unseen"]
        cells = []
        for k in ("cascade", "B1_first_stage", "budget_matched_first_stage"):
            v = (r.get(k) or {}).get("mean")
            cells.append(f"<td{_heat(v)}>{escape(_f(r.get(k), 3))}</td>")
        gap = (g.get("gap_interp") or {}).get("mean")
        rows.append(
            f"<tr><td>{escape(name)}</td><td>{g['runs']}</td>{''.join(cells)}"
            f"<td>{escape(_f(g.get('fpr_cascade'), 3))}</td>"
            f"<td{_heat(gap, signed=True)}>{escape(_f(g.get('gap_interp'), 3))}</td>"
            f"<td>{g['gap_ci_above_zero']}/{g['runs']}</td><td>{escape(_f(g.get('first_stage_auc'), 3))}</td></tr>"
        )
    head = (
        "<tr><th>unseen</th><th>runs</th><th>recall cascade</th><th>recall first stage</th>"
        "<th>recall budget-matched</th><th>cascade FPR</th><th>frontier gap</th><th>gap CI above 0</th>"
        "<th>first-stage AUC</th></tr>"
    )
    return (
        "<section><h2>Unseen attacks on TEST</h2>"
        '<p class="muted">Blue cells: higher recall. Frontier gap: blue above the first stage\'s ROC hull, '
        "red below it, on the benign and unseen flows only.</p>"
        f'<div class="scroll"><table>{head}{"".join(rows)}</table></div></section>'
    )


def _types(agg: dict) -> str:
    ty = agg.get("types")
    if not ty:
        return ""
    rows = "".join(
        f"<tr><td>{escape(c)}</td><td{_heat((f or {}).get('mean'))}>{escape(_f(f, 3))}</td>"
        f"<td>{escape(_f(ty['per_type_support'][c], 0))}</td></tr>"
        for c, f in ty["per_type_f1"].items()
    )
    return (
        "<section><h2>Attack types on TEST</h2>"
        f'<p class="muted">Type accuracy {escape(_f(ty["accuracy"]))}, macro F1 {escape(_f(ty["macro_f1"]))} '
        "on true attacks; untyped and uncertain flows count as wrong.</p>"
        f'<div class="scroll"><table><tr><th>type</th><th>F1</th><th>support</th></tr>{rows}</table></div>'
        "</section>"
    )


def _frozen(agg: dict, order: list[str]) -> str:
    def cell(c: dict) -> str:
        if c.get("off"):
            return f"off (to {c.get('off_output')})"
        return f"{c.get('candidate')}, {c.get('decision_mode')} {c.get('value')}"

    head = "".join(f"<th>{escape(s)}</th>" for s in order)
    rows = "".join(
        f"<tr><td>{escape(unit)}</td>"
        + "".join(f"<td>{escape(cell(fz.get(s, {})))}</td>" for s in order)
        + "</tr>"
        for unit, fz in agg.get("frozen_per_seed", {}).items()
    )
    return f'<div class="scroll"><table><tr><th>run</th>{head}</tr>{rows}</table></div>'


def render(cfg: Config, agg: dict) -> str:
    goal = agg["objective"]
    casc = agg["systems_test"]["cascade"][goal]
    first = agg["systems_test"]["B1_first_stage"][goal]
    fg = agg["frontier_gap"]
    diffs = agg["cascade_minus_control"]
    n = len(agg.get("units", agg["seeds"]))
    tiles = [
        ("cascade " + goal, _f(casc)),
        ("first stage alone", _f(first)),
        ("cascade minus first stage", _f(diffs["B1_first_stage"]["diff"])),
        ("frontier gap (interpolated)", _f(fg["interp"])),
    ]
    tiles_html = "".join(
        f'<div class="tile"><span class="muted">{escape(k)}</span><b>{escape(v)}</b></div>' for k, v in tiles
    )
    jc = agg.get("joint_check")
    joint = (
        "<section><h2>Joint search check</h2><p>Runs where a strictly better VAL combination exists: "
        f"{jc['differs']}/{jc['runs']}; skipped (too many combinations): {jc['skipped']}. "
        "The row joint_check in the systems table is that combination on TEST.</p></section>"
        if jc
        else ""
    )
    statements = "".join(f"<li>{escape(x)}</li>" for x in agg["statements"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(agg["config_name"])}: IDSLab report</title><style>{CSS}</style></head>
<body><main>
<header style="display:grid;gap:6px"><h1>{escape(agg["config_name"])}</h1>
<p class="muted">IDSLab {escape(agg["idslab_version"])}, mode {escape(agg["mode"])}, seeds {escape(str(agg["seeds"]))},
{n} runs, objective {escape(goal)}, first stage {escape(agg["first_stage"])}. Values are mean ± sd over runs on TEST.</p>
</header>
<section class="tiles">{tiles_html}</section>
<section><h2>Cascade map</h2><p class="muted">Each stage with how often selection kept it on, the values it
chose, and the change in {escape(goal)} when it is switched off. Dashed: off in every run.</p>
<div class="scroll" style="padding:10px">{cascade_map(cfg, agg)}</div></section>
<section><h2>Systems on TEST</h2>{_systems_table(agg)}</section>
{_stage_cost(agg)}
{_unseen_grid(agg)}
{_types(agg)}
{joint}
<section><h2>Frozen configuration per run</h2>{_frozen(agg, cfg.order())}</section>
<section><h2>Statements</h2><ul>{statements}</ul></section>
</main></body></html>
"""
