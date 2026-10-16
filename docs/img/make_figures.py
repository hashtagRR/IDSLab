# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Generate the README and guide figures (SVG) for IDSLab.

Usage:

    idslab example fig
    idslab run fig/r1_router_gate.yaml --out fig/runs
    python docs/img/make_figures.py docs/img --run fig/runs/r1-router-gate

The cascade diagrams are drawn from the shipped reference configs. The overview, selection,
systems and frontier figures use the numbers of the run given with ``--run`` (the synthetic R1
example), so every value in them is a measured one.
"""

from __future__ import annotations

import argparse
import json
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

from idslab.config import TERMINALS, Config, load

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"

C = {
    "bg": "#ffffff",
    "card": "#f6f8fa",
    "line": "#d0d7de",
    "fg": "#1f2328",
    "muted": "#59636e",
    "faint": "#8c959f",
    "blue": "#0969da",
    "blue_bg": "#ddf4ff",
    "purple": "#8250df",
    "purple_bg": "#fbefff",
    "teal": "#1b7c83",
    "teal_bg": "#d8f3f6",
    "orange": "#bc4c00",
    "orange_bg": "#fff1e5",
    "green": "#1a7f37",
    "green_bg": "#dafbe1",
    "red": "#cf222e",
    "red_bg": "#ffebe9",
    "gray": "#8c959f",
    "code": "#24292f",
    "code_fg": "#f6f8fa",
}
KIND_COLOR = {"classifier": "blue", "multiclass": "purple", "detector": "teal"}
SYSTEM_LABEL = {
    "cascade": "cascade",
    "B1_first_stage": "B1: first stage, own threshold",
    "B2_first_stage_val_cutoff": "B2: first stage, best VAL cutoff",
    "budget_matched_first_stage": "first stage at the cascade's VAL FPR",
}


def esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Svg:
    def __init__(self, w: float, h: float, label: str):
        self.w, self.h, self.label, self.parts = w, h, label, []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def rect(self, x, y, w, h, *, fill="none", stroke="none", rx: float = 0, sw: float = 1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"{d}/>'
        )

    def text(
        self, x, y, s, *, size: float = 14, weight=400, fill=None, anchor="start", mono=False, ls: float = 0
    ):
        fam = MONO if mono else FONT
        spacing = f' letter-spacing="{ls}"' if ls else ""
        self.add(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill or C["fg"]}" text-anchor="{anchor}"{spacing}>{esc(s)}</text>'
        )

    def line(self, x1, y1, x2, y2, *, stroke=None, sw: float = 1.5, dash=None, arrow=False):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        m = ' marker-end="url(#arrow)"' if arrow else ""
        self.add(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke or C["gray"]}" '
            f'stroke-width="{sw}"{d}{m}/>'
        )

    def path(self, d, *, stroke=None, sw: float = 1.5, fill="none", dash=None, arrow=False):
        da = f' stroke-dasharray="{dash}"' if dash else ""
        m = ' marker-end="url(#arrow)"' if arrow else ""
        self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke or C["gray"]}" stroke-width="{sw}"{da}{m}/>')

    def circle(self, x, y, r, *, fill, stroke="none", sw: float = 1):
        self.add(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )

    def pill(self, x, y, w, h, s, *, fg, bg, size: float = 12, weight=600, stroke=None):
        self.rect(x, y, w, h, fill=bg, stroke=stroke or fg, rx=h / 2, sw=1)
        self.text(x + w / 2, y + h / 2 + size * 0.36, s, size=size, weight=weight, fill=fg, anchor="middle")

    def save(self, path: Path) -> None:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w:.0f}" height="{self.h:.0f}" '
            f'viewBox="0 0 {self.w:.0f} {self.h:.0f}" role="img" aria-label="{esc(self.label)}">'
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
            f'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{C["gray"]}"/>'
            "</marker></defs>"
            f'<rect width="100%" height="100%" fill="{C["bg"]}"/>'
        )
        path.write_text(head + "\n".join(self.parts) + "</svg>\n", encoding="utf-8")


def header(svg: Svg, eyebrow: str, title: str, sub: str, *, x=48, y=50, size: float = 30):
    svg.text(x, y, eyebrow, size=13, weight=700, fill=C["blue"], ls=1.5)
    svg.text(x, y + 38, title, size=size, weight=700)
    if sub:
        svg.text(x, y + 68, sub, size=16, fill=C["muted"])


def card(svg: Svg, x, y, w, h, accent, num, title, desc_lines):
    svg.rect(x, y, w, h, fill=C["card"], stroke=C["line"], rx=10)
    svg.rect(x, y, w, 4, fill=accent, rx=2)
    svg.circle(x + 32, y + 34, 11, fill=accent)
    svg.text(x + 32, y + 38.5, num, size=12, weight=700, fill="#fff", anchor="middle")
    svg.text(x + 52, y + 39, title, size=13, weight=700, ls=1.2)
    for i, line in enumerate(desc_lines):
        svg.text(x + 22, y + 66 + i * 19, line, size=13.5, fill=C["muted"])


# ---------- cascade diagram from a config ----------


def layers(cfg: Config) -> dict[str, int]:
    col = dict.fromkeys(cfg.order(), 0)
    for s in cfg.order():
        for t in cfg.stages[s].routes.values():
            if t in col:
                col[t] = max(col[t], col[s] + 1)
    return col


def stage_lines(cfg: Config, name: str) -> list[str]:
    st = cfg.stages[name]
    kind = "pool detector" if st.transductive else st.kind
    role = ", annotate" if st.role == "annotate" else ""
    vals = ", ".join(f"{v:g}" for v in st.decision.values[:4]) + (
        " ..." if len(st.decision.values) > 4 else ""
    )
    tuned = "chosen on VAL" if st.decision.tunable else "fixed"
    models = ", ".join(c.label.split("#")[0].replace("Classifier", "") for c in st.candidates)
    return [f"{kind}{role}", f"{st.decision.mode} {vals} ({tuned})", models]


def cascade(cfg: Config, title: str, path: Path) -> None:
    nw, nh, gx, chip_w, chip_h, drop, gy = 272, 86, 84, 96, 28, 70, 34
    order = cfg.order()
    col = layers(cfg)
    rows: dict[int, list[str]] = {}
    for s in order:
        rows.setdefault(col[s], []).append(s)
    slot = nh + drop + gy
    top = 150
    body_h = max(len(v) for v in rows.values()) * slot
    ncol = max(col.values()) + 1
    w = 48 * 2 + ncol * nw + (ncol - 1) * gx
    h = top + body_h + 56
    svg = Svg(max(w, 720), h, f"Cascade map of the reference design {cfg.name}")
    header(svg, "REFERENCE DESIGN", title, "", size=24)
    svg.text(48, 118, f"{cfg.name}: stages in selection order {', '.join(order)}", size=14, fill=C["muted"])
    pos = {}
    for c, names in rows.items():
        y0 = top + (body_h - len(names) * slot) / 2 + gy / 2
        for i, s in enumerate(names):
            pos[s] = (48 + c * (nw + gx), y0 + i * slot)
    for s in order:
        x, y = pos[s]
        routes = list(cfg.stages[s].routes.items())
        to_stage = [(o, t) for o, t in routes if t not in TERMINALS]
        to_term = [(o, t) for o, t in routes if t in TERMINALS]
        for i, (out, t) in enumerate(to_stage):
            tx, ty = pos[t]
            x1, y1 = x + nw, y + nh * (i + 1) / (len(to_stage) + 1)
            x2, y2 = tx - 3, ty + nh / 2
            mid = (x1 + x2) / 2
            svg.path(
                f"M{x1:.0f},{y1:.0f} C{mid:.0f},{y1:.0f} {mid:.0f},{y2:.0f} {x2:.0f},{y2:.0f}", arrow=True
            )
            svg.text(x1 + 8, y1 - 6, out, size=12, fill=C["muted"])
        for i, (out, t) in enumerate(to_term):
            cx = x + nw / 2 + (i - (len(to_term) - 1) / 2) * (chip_w + 16)
            cy = y + nh + drop - chip_h
            svg.line(cx, y + nh, cx, cy - 3, arrow=True)
            svg.text(cx + 6, y + nh + 22, out, size=12, fill=C["muted"])
            fg, bg = (C["red"], C["red_bg"]) if t == "ATTACK" else (C["green"], C["green_bg"])
            svg.pill(cx - chip_w / 2, cy, chip_w, chip_h, t, fg=fg, bg=bg, size=12)
        for src in cfg.sources(s):
            sx, sy = pos[src]
            a, b = (sx + nw - 30, sy), (x + 30, y)
            lift = min(a[1], b[1]) - 30
            svg.path(
                f"M{a[0]:.0f},{a[1]:.0f} C{a[0]:.0f},{lift:.0f} {b[0]:.0f},{lift:.0f} {b[0]:.0f},{b[1] - 3:.0f}",
                stroke=C["orange"],
                dash="5 4",
                arrow=True,
            )
            svg.text((a[0] + b[0]) / 2, lift + 8, "score feeds", size=11, fill=C["orange"], anchor="middle")
    for s in order:
        x, y = pos[s]
        st = cfg.stages[s]
        color = C[KIND_COLOR[st.kind]]
        entry = s == cfg.entry
        svg.rect(x, y, nw, nh, fill="#fff", stroke=color, rx=10, sw=2.2 if entry else 1.4)
        svg.rect(x, y + 10, 4, nh - 20, fill=color, rx=2)
        svg.text(x + 16, y + 24, s + ("  (entry)" if entry else ""), size=15, weight=700)
        for i, line in enumerate(stage_lines(cfg, s)):
            svg.text(x + 16, y + 44 + i * 16, line, size=12, fill=C["muted"] if i else color)
    legend_y = h - 22
    lx = 48
    for kind, name in (("classifier", "classifier"), ("detector", "detector"), ("multiclass", "type stage")):
        svg.rect(lx, legend_y - 10, 12, 12, fill=C[KIND_COLOR[kind]], rx=3)
        svg.text(lx + 18, legend_y, name, size=12, fill=C["muted"])
        lx += 110
    svg.save(path)


# ---------- overview ----------


def overview(run: dict, path: Path) -> None:
    W, H = 1280, 760
    svg = Svg(W, H, OVERVIEW_ALT)
    header(
        svg,
        "IDSLAB",
        "Does the cascade beat its own first stage?",
        "Describe a staged IDS in one YAML file. IDSLab fits it, chooses every setting on validation data,"
        " and reports it on the test set next to its first stage alone.",
    )
    top, ch = 168, 492
    widths = [262, 300, 290, 240]
    gap = (W - 96 - sum(widths)) / 3
    xs = [48.0]
    for wd in widths[:-1]:
        xs.append(xs[-1] + wd + gap)
    for i in range(3):
        a = xs[i] + widths[i] + 6
        svg.line(a, top + ch / 2, a + gap - 12, top + ch / 2, arrow=True)

    # 1 configure
    x, w = xs[0], widths[0]
    card(
        svg,
        x,
        top,
        w,
        ch,
        C["gray"],
        "1",
        "CONFIGURE",
        ["Data, stages, models and", "routes in one YAML file."],
    )
    yaml_lines = [
        ("entry", ": binary"),
        ("stages", ":"),
        ("  binary", ":"),
        ("    kind", ": classifier"),
        ("    decision", ": {threshold: .5}"),
        ("    routes", ": {attack: router,"),
        ("", "             benign: BENIGN}"),
        ("  router", ":"),
        ("    kind", ": classifier"),
        ("    decision", ": {grid: [.5, .6, .7]}"),
        ("    routes", ": {benign: gate, ...}"),
        ("  gate", ":"),
        ("    kind", ": detector"),
        ("    fit_on", ": benign"),
        ("    decision", ": {budget: [.01, .05]}"),
    ]
    bx, by, bw = x + 18, top + 112, w - 36
    svg.rect(bx, by, bw, 300, fill="#fff", stroke=C["line"], rx=6)
    svg.text(bx + 10, by + 18, "cfg.yaml", size=11, mono=True, fill=C["muted"])
    svg.line(bx, by + 27, bx + bw, by + 27, stroke=C["line"], sw=1)
    for i, (k, v) in enumerate(yaml_lines):
        yy = by + 46 + i * 17.2
        svg.add(
            f'<text x="{bx + 10:.1f}" y="{yy:.1f}" font-family="{MONO}" font-size="10.6" xml:space="preserve">'
            f'<tspan fill="{C["blue"]}">{esc(k)}</tspan><tspan fill="{C["fg"]}">{esc(v)}</tspan></text>'
        )
    svg.rect(bx, top + ch - 62, bw, 36, fill=C["code"], rx=6)
    svg.text(bx + 12, top + ch - 39, "$ idslab run cfg.yaml", size=12.5, mono=True, fill=C["code_fg"])

    # 2 fit and select on VAL
    x, w = xs[1], widths[1]
    card(
        svg,
        x,
        top,
        w,
        ch,
        C["blue"],
        "2",
        "FIT, THEN CHOOSE ON VAL",
        [
            "Stages fit on their own rows. Settings",
            "are chosen on VAL, one stage at a time,",
            "with off always an option.",
        ],
    )
    # splits bar
    sx, sy, sw_ = x + 22, top + 138, w - 44
    parts = [
        ("FIT-base", 0.48, C["blue"]),
        ("CAL", 0.12, C["purple"]),
        ("VAL", 0.15, C["orange"]),
        ("TEST", 0.25, C["gray"]),
    ]
    cx = sx
    for name, frac, color in parts:
        ww = sw_ * frac
        svg.rect(cx, sy, ww - 2, 22, fill=color, rx=4)
        svg.text(cx + (ww - 2) / 2, sy + 15, name, size=11, weight=700, fill="#fff", anchor="middle")
        cx += ww
    svg.text(sx, sy + 40, "TEST stays locked until every choice is frozen", size=11.5, fill=C["muted"])
    # mini cascade
    my = top + 214
    names = ["binary", "router", "gate"]
    kinds = ["classifier", "classifier", "detector"]
    nw = 74
    step = (w - 44 - nw) / 2
    for i, (n, k) in enumerate(zip(names, kinds, strict=True)):
        nx = x + 22 + i * step
        svg.rect(nx, my, nw, 34, fill="#fff", stroke=C[KIND_COLOR[k]], rx=7, sw=1.6)
        svg.text(nx + nw / 2, my + 22, n, size=12.5, weight=700, anchor="middle")
        if i < 2:
            svg.line(nx + nw + 2, my + 17, nx + step - 4, my + 17, arrow=True)
    # router option bars (real VAL accuracy)
    trace = run["router_options"]
    by0 = top + 280
    svg.text(x + 22, by0, "router cutoff: VAL accuracy of the", size=12, weight=600)
    svg.text(x + 22, by0 + 16, "whole cascade for each option", size=12, weight=600)
    lo, hi = 0.76, 0.845
    bar_w = (w - 44) / len(trace)
    base = by0 + 140
    for i, (label, acc, chosen) in enumerate(trace):
        bx_ = x + 22 + i * bar_w + 5
        hh = (acc - lo) / (hi - lo) * 110
        color = C["blue"] if chosen else ("#afb8c1" if label != "off" else C["faint"])
        svg.rect(bx_, base - hh, bar_w - 10, hh, fill=color, rx=3)
        svg.text(
            bx_ + (bar_w - 10) / 2, base - hh - 5, f"{acc:.3f}", size=10, fill=C["muted"], anchor="middle"
        )
        svg.text(
            bx_ + (bar_w - 10) / 2,
            base + 15,
            label,
            size=11.5,
            weight=700 if chosen else 400,
            anchor="middle",
        )
    svg.line(x + 22, base, x + w - 22, base, stroke=C["line"], sw=1)
    svg.text(x + 22, top + ch - 26, "chosen: 0.6. Ties go to off.", size=12, fill=C["blue"], weight=600)

    # 3 test
    x, w = xs[2], widths[2]
    card(
        svg,
        x,
        top,
        w,
        ch,
        C["orange"],
        "3",
        "TEST, AFTER THE FREEZE",
        ["The frozen cascade against its first", "stage alone, all on the same fitted", "first-stage model."],
    )
    sysv = run["systems"]
    svg.text(x + 22, top + 138, "TEST accuracy, mean of 3 seeds", size=12, weight=600)
    lo, hi = 0.83, 0.845
    for i, (key, val) in enumerate(sysv):
        yy = top + 152 + i * 34
        label = SYSTEM_LABEL[key]
        svg.text(x + 22, yy + 13, label, size=11.5, fill=C["fg"] if key == "cascade" else C["muted"])
        ww = (val - lo) / (hi - lo) * (w - 110)
        svg.rect(x + 22, yy + 18, ww, 9, fill=C["orange"] if key == "cascade" else "#afb8c1", rx=3)
        svg.text(x + 28 + ww, yy + 27, f"{val:.3f}", size=11, fill=C["muted"])
    cy0 = top + 300
    svg.text(x + 22, cy0, "Controls in every report", size=12, weight=600)
    for i, item in enumerate(
        [
            "paired bootstrap: cascade minus each control",
            "frontier gap against the first stage's ROC",
            "effect of switching each stage off",
            "step each stage adds, in order",
            "prevalence check, spread over seeds",
        ]
    ):
        yy = cy0 + 24 + i * 25
        svg.circle(x + 28, yy - 4, 4, fill=C["orange"])
        svg.text(x + 40, yy, item, size=12, fill=C["fg"])

    # 4 report
    x, w = xs[3], widths[3]
    card(svg, x, top, w, ch, C["green"], "4", "REPORT", ["The answer first, then every", "number behind it."])
    systems = dict(run["systems"])
    tiles = [
        ("cascade accuracy", f"{systems['cascade']:.4f}", C["fg"]),
        ("first stage alone (B1)", f"{systems['B1_first_stage']:.4f}", C["fg"]),
        ("cascade minus first stage", f"{run['diff']:+.4f}", C["red"]),
        ("frontier gap", f"{run['gap']:+.4f}", C["red"]),
    ]
    for i, (lab, val, color) in enumerate(tiles):
        yy = top + 106 + i * 58
        svg.rect(x + 18, yy, w - 36, 50, fill="#fff", stroke=C["line"], rx=8)
        svg.text(x + 30, yy + 18, lab, size=11.5, fill=C["muted"])
        svg.text(x + 30, yy + 41, val, size=18, weight=700, fill=color)
    note = [
        "Synthetic example: router and gate",
        "improve VAL, yet the cascade ends",
        "below its first stage on TEST.",
        "The report says so.",
    ]
    for i, line in enumerate(note):
        svg.text(x + 22, top + 352 + i * 18, line, size=12, fill=C["muted"])
    files = ["report.html", "report.md", "report.json"]
    for i, f in enumerate(files):
        svg.pill(x + 16 + i * 70, top + ch - 64, 66, 24, f, fg=C["green"], bg=C["green_bg"], size=10)
    svg.text(x + 22, top + ch - 20, "or: idslab dashboard", size=12, mono=True, fill=C["muted"])

    # bottom strip
    by = top + ch + 28
    half = (W - 96 - 24) / 2
    for i, (title, text) in enumerate(
        [
            ("Unseen attacks", "hold out each attack family, or test types never trained on"),
            ("Models trained elsewhere", "export splits, train on a GPU, import checked scores"),
        ]
    ):
        bx = 48 + i * (half + 24)
        svg.rect(bx, by, half, 44, fill="#fff", stroke=C["line"], rx=22)
        svg.text(bx + 22, by + 27, title + ": ", size=13, weight=700)
        svg.text(bx + 22 + len(title) * 8.2 + 14, by + 27, text, size=13, fill=C["muted"])
    svg.save(path)


OVERVIEW_ALT = (
    "IDSLab overview in four steps. 1 Configure: data, stages, models and routes in one YAML file, run "
    "with idslab run. 2 Fit, then choose on VAL: splits FIT-base, CAL, VAL and a locked TEST; stages "
    "binary, router and gate; the router cutoff is chosen by the VAL accuracy of the whole cascade, with "
    "off as an option. 3 TEST after the freeze: the cascade against three versions of its first stage "
    "alone, plus the controls. 4 Report: cascade minus first stage, frontier gap and stage use; on the "
    "synthetic example the cascade ends below its first stage, and the report says so."
)


# ---------- splits ----------


def splits(path: Path) -> None:
    W, H = 1100, 360
    svg = Svg(
        W, H, "Splits per seed: FIT-base, CAL and VAL come from the train partition, TEST from the test file"
    )
    header(svg, "SPLITS PER SEED", "Every choice is made before TEST is read", "")
    y = 150
    total = W - 96
    parts = [
        ("FIT-base", 0.48, C["blue"], ["fits every stage", "(calibrated: model only)"]),
        ("CAL", 0.12, C["purple"], ["calibrates", "calibrated stages"]),
        ("VAL", 0.15, C["orange"], ["chooses candidates,", "thresholds, off switches"]),
        ("TEST", 0.25, C["gray"], ["read once, after", "the configuration is frozen"]),
    ]
    x = 48.0
    for name, frac, color, lines in parts:
        w = total * frac
        svg.rect(x, y, w - 4, 40, fill=color, rx=6)
        svg.text(x + (w - 4) / 2, y + 26, name, size=15, weight=700, fill="#fff", anchor="middle")
        for i, line in enumerate(lines):
            svg.text(x + 4, y + 68 + i * 19, line, size=13, fill=C["muted"])
        x += w
    # braces
    fit_w = total * 0.60
    svg.path(
        f"M48,{y - 10} L48,{y - 18} L{48 + fit_w - 4:.0f},{y - 18} L{48 + fit_w - 4:.0f},{y - 10}",
        stroke=C["faint"],
    )
    svg.text(
        48 + fit_w / 2,
        y - 24,
        "FIT: uncalibrated stages fit on all of it",
        size=12.5,
        fill=C["muted"],
        anchor="middle",
    )
    tr_w = total * 0.75
    svg.path(
        f"M48,{y + 120} L48,{y + 128} L{48 + tr_w - 4:.0f},{y + 128} L{48 + tr_w - 4:.0f},{y + 120}",
        stroke=C["faint"],
    )
    svg.text(
        48 + tr_w / 2,
        y + 148,
        "train file (or the part left after test_fraction)",
        size=12.5,
        fill=C["muted"],
        anchor="middle",
    )
    svg.path(
        f"M{48 + tr_w:.0f},{y + 120} L{48 + tr_w:.0f},{y + 128} L{48 + total - 4:.0f},{y + 128} L{48 + total - 4:.0f},{y + 120}",
        stroke=C["faint"],
    )
    svg.text(48 + tr_w + total * 0.125, y + 148, "test file", size=12.5, fill=C["muted"], anchor="middle")
    svg.text(
        48,
        H - 18,
        "Cuts are stratified by type, fixed per seed. Defaults: val_fraction 0.2 of the train partition, cal_fraction 0.2 of FIT.",
        size=12.5,
        fill=C["muted"],
    )
    svg.save(path)


# ---------- selection ----------


def selection(run: dict, path: Path) -> None:
    W = 1100
    rows = run["trace"]
    H = 150 + sum(70 + 26 * len(r["options"]) for r in rows)
    svg = Svg(W, H, "Greedy selection on VAL for the R1 example, seed 0")
    header(
        svg,
        "GREEDY SELECTION ON VAL",
        "One stage at a time, later stages off",
        "Synthetic R1 example, seed 0. Each option is scored by the VAL accuracy of the whole cascade.",
    )
    y = 150
    lo, hi = 0.76, 0.845
    bar_x, bar_w = 360, W - 360 - 120
    for r in rows:
        svg.text(48, y + 20, r["stage"], size=17, weight=700)
        svg.text(48, y + 40, f"{r['reach']} VAL rows reach it. {r['note']}", size=12.5, fill=C["muted"])
        for i, (label, acc, chosen) in enumerate(r["options"]):
            yy = y + 56 + i * 26
            svg.text(bar_x - 14, yy + 13, label, size=12.5, anchor="end", weight=700 if chosen else 400)
            ww = max(2.0, (acc - lo) / (hi - lo) * bar_w)
            color = C["blue"] if chosen else (C["faint"] if label == "off" else "#afb8c1")
            svg.rect(bar_x, yy + 2, ww, 16, fill=color, rx=3)
            svg.text(
                bar_x + ww + 8,
                yy + 14,
                f"{acc:.4f}" + ("  chosen" if chosen else ""),
                size=12,
                fill=C["blue"] if chosen else C["muted"],
                weight=700 if chosen else 400,
            )
        y += 70 + 26 * len(r["options"])
        svg.line(48, y - 8, W - 48, y - 8, stroke=C["line"], sw=1)
    svg.save(path)


# ---------- frontier ----------


def frontier(run_dir: Path, run: dict, path: Path) -> None:
    flow = pd.read_parquet(run_dir / "standard" / "seed_0" / "per_flow.parquet")
    y = flow["y"].to_numpy()
    s = flow["score_binary"].to_numpy()
    order = np.argsort(-s, kind="mergesort")
    ys, ss = y[order], s[order]
    tps, fps = np.cumsum(ys), np.cumsum(1 - ys)
    last = np.r_[np.diff(ss) != 0, True]
    tpr = np.r_[0, tps[last] / ys.sum()]
    fpr = np.r_[0, fps[last] / (1 - ys).sum()]
    hull = upper_hull(fpr, tpr)
    gap = run["fg"]
    W, H = 760, 540
    svg = Svg(
        W,
        H,
        "Frontier gap for the R1 example, seed 0: the cascade lies just below the first stage's ROC hull",
    )
    header(svg, "FRONTIER GAP", "Can a threshold reach the cascade's point?", "")
    px, py, pw, ph = 90, 110, 600, 360
    xmax, ymin, ymax = 0.12, 0.40, 0.80

    def fx(v):
        return px + v / xmax * pw

    def fy(v):
        return py + ph - (v - ymin) / (ymax - ymin) * ph

    svg.rect(px, py, pw, ph, fill="#fff", stroke=C["line"])
    for t in np.linspace(0, xmax, 7):
        svg.line(fx(t), py, fx(t), py + ph, stroke="#eaeef2", sw=1)
        svg.text(fx(t), py + ph + 18, f"{t:.2f}", size=11, fill=C["muted"], anchor="middle")
    for t in np.linspace(ymin, ymax, 5):
        svg.line(px, fy(t), px + pw, fy(t), stroke="#eaeef2", sw=1)
        svg.text(px - 8, fy(t) + 4, f"{t:.1f}", size=11, fill=C["muted"], anchor="end")
    svg.text(px + pw / 2, py + ph + 40, "false positive rate", size=13, fill=C["muted"], anchor="middle")
    svg.add(
        f'<text transform="translate({px - 52},{py + ph / 2}) rotate(-90)" font-family="{FONT}" font-size="13" '
        f'fill="{C["muted"]}" text-anchor="middle">true positive rate</text>'
    )
    svg.add(f'<clipPath id="plot"><rect x="{px}" y="{py}" width="{pw}" height="{ph}"/></clipPath>')
    step = "M" + " L".join(
        f"{fx(a):.1f},{fy(b):.1f}" for a, b in zip(fpr, tpr, strict=True) if a <= xmax * 1.3
    )
    hull_d = "M" + " L".join(f"{fx(a):.1f},{fy(b):.1f}" for a, b in hull if a <= xmax * 1.6)
    svg.add('<g clip-path="url(#plot)">')
    svg.path(step, stroke="#afb8c1", sw=1.4)
    svg.path(hull_d, stroke=C["blue"], sw=2.2)
    svg.add("</g>")
    cf, ct = gap["fpr"], gap["tpr"]
    hull_t = gap["first_tpr_interp"]
    svg.line(fx(cf), fy(ct), fx(cf), fy(hull_t), stroke=C["red"], sw=2)
    svg.circle(fx(cf), fy(hull_t), 4.5, fill=C["blue"])
    svg.circle(fx(cf), fy(ct), 6, fill=C["orange"], stroke="#fff", sw=1.5)
    b1f, b1t = run["b1_point"]
    svg.circle(fx(b1f), fy(b1t), 5, fill="#fff", stroke=C["fg"], sw=1.6)
    svg.text(fx(b1f) + 10, fy(b1t) + 16, "B1: first stage at 0.5", size=12, fill=C["fg"])
    svg.text(fx(cf) + 12, fy(ct) + 18, "cascade", size=12.5, weight=700, fill=C["orange"])
    svg.text(
        fx(cf) - 12,
        fy(ct) + 4,
        f"gap {gap['gap_interp']:+.4f}",
        size=12.5,
        weight=700,
        fill=C["red"],
        anchor="end",
    )
    lx, ly = px + pw - 250, py + ph - 70
    svg.rect(lx, ly, 236, 58, fill="#fff", stroke=C["line"], rx=6)
    svg.line(lx + 12, ly + 20, lx + 40, ly + 20, stroke=C["blue"], sw=2.2)
    svg.text(lx + 48, ly + 24, "first stage: ROC upper hull", size=12)
    svg.line(lx + 12, ly + 42, lx + 40, ly + 42, stroke="#afb8c1", sw=1.4)
    svg.text(lx + 48, ly + 46, "first stage: single thresholds", size=12)
    svg.text(
        48,
        H - 16,
        "A positive gap means the cascade reaches a point no threshold of its first stage reaches. Here it is just below.",
        size=12.5,
        fill=C["muted"],
    )
    svg.save(path)


def upper_hull(fpr: np.ndarray, tpr: np.ndarray) -> list[tuple[float, float]]:
    pts = sorted(set(zip(fpr.tolist(), tpr.tolist(), strict=True)))
    hull: list[tuple[float, float]] = []
    for p in pts:
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) >= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    return hull


# ---------- run numbers ----------


SHORT = {
    "RandomForestClassifier": "random forest",
    "HistGradientBoostingClassifier": "gradient boosting",
    "IsolationForest": "isolation forest",
    "LocalOutlierFactor": "local outlier factor",
}


def short(label: str) -> str:
    name = label.split("#", maxsplit=1)[0]
    return SHORT.get(name, name)


def run_numbers(run_dir: Path) -> dict:
    rep = json.loads((run_dir / "report.json").read_text())
    s0 = json.loads((run_dir / "standard" / "seed_0" / "summary.json").read_text())
    goal = rep["objective"]
    systems = [(k, rep["systems_test"][k][goal]["mean"]) for k in SYSTEM_LABEL]
    trace = []
    notes = {
        "binary": "Fixed threshold; the candidate with the best VAL AUC is kept (RF 0.882, HGB 0.865).",
        "router": "Cutoff chosen on VAL; off is the first option.",
        "gate": "Candidate and budget chosen together; ties go to off, then to the first value.",
    }
    router_options = []
    for t in s0["selection_trace"]:
        chosen = t["chosen"]
        opts = []
        for o in t["options"]:
            label = "off" if o.get("candidate") is None else f"{short(o['candidate'])}, {o['value']:g}"
            hit = (
                not chosen["off"]
                and o.get("candidate") == chosen["candidate"]
                and o.get("value") == chosen["value"]
            )
            opts.append((label, o[goal], hit))
            if t["stage"] == "router":
                router_options.append(
                    ("off" if o.get("candidate") is None else f"{o['value']:g}", o[goal], hit)
                )
        trace.append(
            {
                "stage": t["stage"],
                "reach": t["val_rows_reaching"],
                "note": notes.get(t["stage"], ""),
                "options": opts,
            }
        )
    flow = pd.read_parquet(run_dir / "standard" / "seed_0" / "per_flow.parquet")
    yv = flow["y"].to_numpy()
    pb = flow["pred_B1_first_stage"].to_numpy()
    b1 = (
        float(((pb == 1) & (yv == 0)).sum() / (yv == 0).sum()),
        float(((pb == 1) & (yv == 1)).sum() / (yv == 1).sum()),
    )
    return {
        "systems": systems,
        "router_options": router_options,
        "trace": trace,
        "diff": rep["cascade_minus_control"]["B1_first_stage"]["diff"]["mean"],
        "gap": rep["frontier_gap"]["interp"]["mean"],
        "fg": s0["controls"]["frontier_gap_test"],
        "b1_point": b1,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Generate the IDSLab documentation figures.")
    p.add_argument("out", type=Path)
    p.add_argument("--run", type=Path, help="run folder of the synthetic R1 example (runs/r1-router-gate)")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    designs = {
        "r1_router_gate.yaml": "R1: entry classifier, router, anomaly gate",
        "r2_anomaly_first.yaml": "R2: anomaly detector first, classifier clears alarms",
        "r1_four_layer.yaml": "R1, four layers: router score feeds a type stage, gate refits per batch",
    }
    for name, title in designs.items():
        with resources.as_file(resources.files("idslab.examples") / name) as f:
            cfg = load(str(f))
        cascade(cfg, title, args.out / f"design-{name.removesuffix('.yaml').replace('_', '-')}.svg")
    splits(args.out / "splits.svg")
    if args.run is not None:
        run = run_numbers(args.run)
        overview(run, args.out / "idslab-overview.svg")
        selection(run, args.out / "selection.svg")
        frontier(args.run, run, args.out / "frontier-gap.svg")
    print(f"figures written to {args.out}")


if __name__ == "__main__":
    main()
