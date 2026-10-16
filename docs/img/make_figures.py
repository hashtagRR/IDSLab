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
import itertools
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
    W, H = 1280, 700
    svg = Svg(W, H, OVERVIEW_ALT)
    header(
        svg,
        "IDSLAB",
        "Does every stage of your IDS cascade earn its place?",
        "Run any staged design under one fixed protocol and compare it with its own first stage, stage by stage.",
    )
    top, ch = 160, 440
    widths = [356, 352, 428]
    gap = (W - 96 - sum(widths)) / 2
    xs = [48.0, 48 + widths[0] + gap, 48 + widths[0] + widths[1] + 2 * gap]
    for k in range(2):
        a = xs[k] + widths[k] + 8
        svg.line(a, top + ch / 2, a + gap - 16, top + ch / 2, arrow=True)

    # 1 your design
    x, w = xs[0], widths[0]
    card(
        svg,
        x,
        top,
        w,
        ch,
        C["gray"],
        "1",
        "YOUR DESIGN",
        ["Stages, the model in each, and where", "each output goes: one YAML file."],
    )
    nodes = [
        ("binary", "classifier", "random forest", "threshold 0.5"),
        ("router", "classifier", "random forest", "cutoff chosen on VAL"),
        ("gate", "detector", "isolation forest or LOF", "alarm budget chosen on VAL"),
    ]
    side = [("benign", "BENIGN"), ("attack", "ATTACK"), ("anomalous", "ATTACK")]
    down = ["attack", "benign", "normal"]
    nx, nw, nh = x + 22, 186, 62
    for i, (name, kind, model, dec) in enumerate(nodes):
        ny = top + 112 + i * 100
        color = C[KIND_COLOR[kind]]
        svg.rect(nx, ny, nw, nh, fill="#fff", stroke=color, rx=9, sw=1.6)
        svg.rect(nx, ny + 9, 4, nh - 18, fill=color, rx=2)
        svg.text(nx + 14, ny + 21, name, size=14, weight=700)
        svg.text(nx + 14 + len(name) * 8.6 + 8, ny + 21, kind, size=11.5, fill=color)
        svg.text(nx + 14, ny + 39, model, size=12, fill=C["fg"])
        svg.text(nx + 14, ny + 55, dec, size=11.5, fill=C["muted"])
        out, term = side[i]
        fg, bg = (C["red"], C["red_bg"]) if term == "ATTACK" else (C["green"], C["green_bg"])
        svg.line(nx + nw + 2, ny + 22, nx + nw + 58, ny + 22, arrow=True)
        svg.text(nx + nw + 6, ny + 16, out, size=10.5, fill=C["muted"])
        svg.pill(nx + nw + 62, ny + 10, 76, 24, term, fg=fg, bg=bg, size=11.5)
        if i < 2:
            svg.line(nx + 40, ny + nh + 2, nx + 40, ny + 98, arrow=True)
            svg.text(nx + 48, ny + nh + 22, down[i], size=11, fill=C["muted"])
        else:
            svg.line(nx + nw + 2, ny + 48, nx + nw + 58, ny + 48, arrow=True)
            svg.text(nx + nw + 6, ny + 42, "normal", size=10.5, fill=C["muted"])
            svg.pill(nx + nw + 62, ny + 36, 76, 24, "BENIGN", fg=C["green"], bg=C["green_bg"], size=11.5)
    svg.text(x + 22, top + ch - 44, "Any model per stage: scikit-learn, XGBoost,", size=12, fill=C["muted"])
    svg.text(x + 22, top + ch - 26, "PyOD, PyTorch, or scores from a GPU job.", size=12, fill=C["muted"])

    # 2 the protocol
    x, w = xs[1], widths[1]
    card(
        svg,
        x,
        top,
        w,
        ch,
        C["blue"],
        "2",
        "ONE FIXED PROTOCOL",
        ["The same rules for every design,", "so results compare."],
    )
    rules = [
        ("Split per seed", "TEST is locked until every choice is made"),
        ("Fit each stage on its own rows", "pipelines, search and calibration inside"),
        ("Choose models and thresholds on VAL", "every tunable stage may be switched off"),
        ("Read TEST once", "next to controls built from the first stage"),
    ]
    for i, (head, sub) in enumerate(rules):
        ry = top + 108 + i * 64
        svg.circle(x + 34, ry + 6, 11, fill=C["blue_bg"], stroke=C["blue"])
        svg.text(x + 34, ry + 10.5, str(i + 1), size=12, weight=700, fill=C["blue"], anchor="middle")
        svg.text(x + 54, ry + 11, head, size=13.5, weight=700)
        svg.text(x + 54, ry + 30, sub, size=12, fill=C["muted"])
    bx, by, bw = x + 22, top + 120 + 4 * 64, w - 44
    parts = [
        ("FIT-base", 0.48, C["blue"]),
        ("CAL", 0.12, C["purple"]),
        ("VAL", 0.15, C["orange"]),
        ("TEST", 0.25, C["gray"]),
    ]
    cx = bx
    for name, frac, color in parts:
        ww = bw * frac
        svg.rect(cx, by, ww - 2, 22, fill=color, rx=4)
        svg.text(cx + (ww - 2) / 2, by + 15, name, size=11, weight=700, fill="#fff", anchor="middle")
        cx += ww
    svg.text(x + 22, top + ch - 26, "Also: held-out attack families, novel types.", size=12, fill=C["muted"])

    # 3 the answer
    x, w = xs[2], widths[2]
    card(
        svg,
        x,
        top,
        w,
        ch,
        C["green"],
        "3",
        "THE ANSWER",
        [f"Example: the design on the left, synthetic data, {run['n_seeds']} seeds."],
    )
    systems = dict(run["systems"])
    lo, hi = 0.80, 0.85
    bars = [
        ("cascade", systems["cascade"], C["orange"]),
        ("first stage alone", systems["B1_first_stage"], C["blue"]),
    ]
    svg.text(x + 22, top + 108, "TEST accuracy", size=12, weight=600)
    for i, (lab, val, color) in enumerate(bars):
        yy = top + 120 + i * 30
        svg.text(x + 22, yy + 15, lab, size=12.5)
        ww = (val - lo) / (hi - lo) * (w - 210)
        svg.rect(x + 150, yy + 3, ww, 16, fill=color, rx=3)
        svg.text(x + 158 + ww, yy + 15, f"{val:.4f}", size=12, weight=700)
    svg.text(
        x + 22,
        top + 196,
        f"Difference {run['diff']:+.4f}; no seed shows a gain beyond bootstrap noise.",
        size=12,
        fill=C["muted"],
    )
    ty = top + 226
    svg.text(x + 22, ty, "stage", size=11, weight=700, fill=C["muted"], ls=0.6)
    svg.text(x + 106, ty, "kept on VAL", size=11, weight=700, fill=C["muted"], ls=0.6)
    svg.text(x + 196, ty, "lost if removed", size=11, weight=700, fill=C["muted"], ls=0.6)
    svg.text(x + 306, ty, "reading", size=11, weight=700, fill=C["muted"], ls=0.6)
    svg.line(x + 22, ty + 8, x + w - 22, ty + 8, stroke=C["line"], sw=1)
    for i, (stage, on, eff) in enumerate(run["stages"]):
        yy = ty + 32 + i * 34
        good = eff > 0.005
        svg.text(x + 22, yy, stage, size=13, weight=700)
        svg.text(x + 106, yy, on, size=12.5)
        svg.text(x + 196, yy, f"{eff:+.3f}", size=12.5, mono=True, fill=C["green"] if good else C["red"])
        reading = "carries the result" if good else "adds nothing here"
        svg.pill(
            x + 300,
            yy - 16,
            106,
            22,
            reading,
            fg=C["green"] if good else C["red"],
            bg=C["green_bg"] if good else C["red_bg"],
            size=10.5,
        )
        svg.line(x + 22, yy + 12, x + w - 22, yy + 12, stroke="#eaeef2", sw=1)
    svg.text(
        x + 22, top + ch - 44, "Plus the frontier gap, paired bootstrap intervals,", size=12, fill=C["muted"]
    )
    svg.text(
        x + 22, top + ch - 26, "selection traces, a report page and a dashboard.", size=12, fill=C["muted"]
    )

    # bottom line
    by = top + ch + 30
    svg.rect(48, by, W - 96, 46, fill="#fff", stroke=C["line"], rx=23)
    svg.text(
        W / 2,
        by + 29,
        "Here the extra stages looked better on VAL, yet the first stage alone is as good on TEST. That is the finding IDSLab exists to make visible.",
        size=13.5,
        fill=C["fg"],
        anchor="middle",
    )
    svg.save(path)


OVERVIEW_ALT = (
    "IDSLab overview. Question: does every stage of your IDS cascade earn its place? 1 Your design: a "
    "binary classifier, a router and an anomaly gate, each with its model and decision, routed to BENIGN "
    "or ATTACK, written in one YAML file. 2 One fixed protocol: splits per seed with TEST locked, each "
    "stage fitted on its own rows, models and thresholds chosen on VAL with off as an option, TEST read "
    "once next to controls. 3 The answer on synthetic data: the cascade and the first stage alone reach "
    "almost the same TEST accuracy; the entry classifier carries the result and the router and gate add "
    "nothing here."
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
        fx(cf) - 20,
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


# ---------- flow map ----------


def flow_map(run_dir: Path, path: Path) -> None:
    """Where every TEST flow of seed 0 goes, split by its true class (R1 layout)."""
    flow = pd.read_parquet(run_dir / "standard" / "seed_0" / "per_flow.parquet")
    terminal = np.where(flow["pred_cascade"] == 1, "ATTACK", "BENIGN")
    edges: dict[tuple[str, str], list[int]] = {}
    for p, term, y in zip(flow["path"], terminal, flow["y"], strict=True):
        hops = [*str(p).split(">"), str(term)]
        for a, b in itertools.pairwise(hops):
            edges.setdefault((a, b), [0, 0])[int(y)] += 1
    n = len(flow)
    n_att = int(flow["y"].sum())

    def count(a, b):
        return edges.get((a, b), [0, 0])

    W, H = 1280, 720
    svg = Svg(W, H, FLOW_ALT)
    header(
        svg,
        "WHERE EVERY TEST FLOW GOES",
        "The R1 cascade, flow by flow",
        f"Seed 0 of the synthetic example: {n:,} TEST flows, {n_att:,} of them attacks. "
        "Band width is the number of flows; red is true attacks, green true benign.",
        size=28,
    )
    sc = 330 / n
    nodes = {
        "binary": (170, 168, n),
        "router": (500, 168, sum(sum(v) for (a, _), v in edges.items() if a == "router")),
        "gate": (830, 300, sum(sum(v) for (a, _), v in edges.items() if a == "gate")),
        "ATTACK": (1110, 168, sum(sum(v) for (_, b), v in edges.items() if b == "ATTACK")),
        "BENIGN": (1110, 350, sum(sum(v) for (_, b), v in edges.items() if b == "BENIGN")),
    }
    out_cursor: dict[str, float] = {k: float(v[1]) for k, v in nodes.items()}
    in_cursor: dict[str, float] = {k: float(v[1]) for k, v in nodes.items()}
    order = [
        ("binary", "router"),
        ("binary", "BENIGN"),
        ("router", "ATTACK"),
        ("router", "gate"),
        ("gate", "ATTACK"),
        ("gate", "BENIGN"),
    ]
    # terminals take their inputs top to bottom in the order the bands arrive
    in_order = {
        "BENIGN": [("gate", "BENIGN"), ("binary", "BENIGN")],
        "ATTACK": [("router", "ATTACK"), ("gate", "ATTACK")],
    }
    in_pos: dict[tuple[str, str], float] = {}
    for term, srcs in in_order.items():
        y = nodes[term][1]
        for e in srcs:
            in_pos[e] = y
            y += sum(count(*e)) * sc
    nw = 16
    for a, b in order:
        ben, att = count(a, b)
        tot = ben + att
        if tot == 0:
            continue
        x1 = nodes[a][0] + nw
        x2 = nodes[b][0]
        y1 = out_cursor[a]
        y2 = in_pos[(a, b)] if (a, b) in in_pos else in_cursor[b]
        out_cursor[a] += tot * sc
        if (a, b) not in in_pos:
            in_cursor[b] += tot * sc
        mx = (x1 + x2) / 2
        off = 0.0
        for k, color in ((att, C["red"]), (ben, C["green"])):
            hh = k * sc
            if hh <= 0:
                continue
            ya, yb = y1 + off, y2 + off
            d = (
                f"M{x1:.1f},{ya:.1f} C{mx:.1f},{ya:.1f} {mx:.1f},{yb:.1f} {x2:.1f},{yb:.1f} "
                f"L{x2:.1f},{yb + hh:.1f} C{mx:.1f},{yb + hh:.1f} {mx:.1f},{ya + hh:.1f} {x1:.1f},{ya + hh:.1f} Z"
            )
            svg.add(f'<path d="{d}" fill="{color}" fill-opacity="0.32" stroke="none"/>')
            off += hh
    for name, (x, y, total) in nodes.items():
        hh = max(total * sc, 3)
        if name in TERMINALS:
            color = C["red"] if name == "ATTACK" else C["green"]
            svg.rect(x, y, nw, hh, fill=color, rx=3)
        else:
            svg.rect(x, y, nw, hh, fill=C["fg"], rx=3)
    # labels
    ben_b, att_b = count("binary", "BENIGN")
    ben_r, att_r = count("router", "ATTACK")
    ben_g, att_g = count("router", "gate")
    ben_ga, att_ga = count("gate", "ATTACK")
    ben_gb, att_gb = count("gate", "BENIGN")
    svg.text(170, 156, f"binary: {n:,} flows", size=14, weight=700)
    svg.text(500, 156, f"router: {ben_r + att_r + ben_g + att_g:,}", size=14, weight=700)
    svg.text(830, 290, f"gate: {ben_g + att_g:,}", size=14, weight=700)
    ta = nodes["ATTACK"][2]
    svg.text(1134, 186, "ATTACK", size=14, weight=700, fill=C["red"])
    svg.text(1134, 204, f"{ta:,} flows", size=12.5, fill=C["muted"])
    svg.text(1134, nodes["BENIGN"][1] + 18, "BENIGN", size=14, weight=700, fill=C["green"])
    svg.text(1134, nodes["BENIGN"][1] + 36, f"{nodes['BENIGN'][2]:,} flows", size=12.5, fill=C["muted"])

    def note(x, y, lines, color=None):
        for i, line in enumerate(lines):
            svg.text(
                x,
                y + i * 17,
                line,
                size=12.5,
                weight=700 if i == 0 else 400,
                fill=(color or C["fg"]) if i == 0 else C["muted"],
            )

    note(
        420,
        556,
        [f"{ben_b + att_b:,} called benign by binary", f"{att_b:,} of them attacks: missed"],
        C["red"],
    )
    note(700, 146, [f"router flags {ben_r + att_r:,}", f"{att_r:,} attacks, {ben_r:,} false alarms"])
    note(620, 222, [f"router passes {ben_g + att_g:,} to the gate", f"{att_g:,} of them attacks"])
    note(900, 268, [f"gate flags {ben_ga + att_ga:,}", f"{att_ga:,} attacks, {ben_ga:,} benign"], C["red"])
    note(900, 336, [f"gate clears {ben_gb + att_gb:,}", f"{att_gb:,} attacks missed"])
    svg.rect(48, H - 74, W - 96, 46, fill="#fff", stroke=C["line"], rx=23)
    svg.text(
        W / 2,
        H - 45,
        f"The gate catches {att_ga} of the {att_g} attacks the router passes to it. The report's stage table"
        " says the same: removing the gate costs nothing.",
        size=13.5,
        anchor="middle",
    )
    svg.save(path)


FLOW_ALT = (
    "Flow map of the R1 cascade on seed 0 of the synthetic example: 3,000 TEST flows go through binary, "
    "router and gate to ATTACK or BENIGN, each band split into true attacks and true benign flows. The "
    "entry classifier sends 2,125 flows to BENIGN, 352 of them attacks; the router flags 743, 687 of them "
    "attacks, and passes 132 to the gate, 73 of them attacks; the gate flags 2 benign flows and misses all 73."
)


# ---------- stage steps ----------


def stage_steps(run: dict, path: Path) -> None:
    W, H = 900, 520
    svg = Svg(W, H, "Accuracy of the R1 cascade truncated after each stage, on VAL and on TEST, seed 0")
    header(
        svg,
        "ADDING STAGES",
        "VAL says the extra stages help. TEST disagrees.",
        "R1 example, seed 0: accuracy of the cascade cut after each stage.",
        size=26,
    )
    steps = run["steps"]
    px, py, pw, ph = 110, 140, 600, 280
    lo, hi = 0.82, 0.85

    def fx(i):
        return px + 60 + i * (pw - 120) / (len(steps) - 1)

    def fy(v):
        return py + ph - (v - lo) / (hi - lo) * ph

    svg.rect(px, py, pw, ph, fill="#fff", stroke=C["line"])
    for t in np.linspace(lo, hi, 7):
        svg.line(px, fy(t), px + pw, fy(t), stroke="#eaeef2", sw=1)
        svg.text(px - 8, fy(t) + 4, f"{t:.3f}", size=11, fill=C["muted"], anchor="end")
    svg.add(
        f'<text transform="translate({px - 66},{py + ph / 2}) rotate(-90)" font-family="{FONT}" font-size="13" '
        f'fill="{C["muted"]}" text-anchor="middle">accuracy</text>'
    )
    for key, color, dash, label in (("val", C["orange"], "6 4", "VAL"), ("test", C["blue"], None, "TEST")):
        pts = [(fx(i), fy(st[key])) for i, st in enumerate(steps)]
        svg.path("M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts), stroke=color, sw=2.6, dash=dash)
        for (a, b), st in zip(pts, steps, strict=True):
            svg.circle(a, b, 5, fill=color, stroke="#fff", sw=1.5)
            svg.text(
                a + 9, b + (20 if key == "val" else -10), f"{st[key]:.4f}", size=12, fill=color, weight=700
            )
        a, b = pts[-1]
        svg.text(px + pw + 12, b + (16 if key == "val" else -6), label, size=13, weight=700, fill=color)
    for i, st in enumerate(steps):
        svg.text(fx(i), py + ph + 20, st["label"], size=12.5, weight=700, anchor="middle")
        svg.text(fx(i), py + ph + 37, st["sub"], size=11.5, fill=C["muted"], anchor="middle")
    svg.text(
        48,
        H - 34,
        "Choices are made on VAL, as they must be; the router and gate were chosen on 363 and 53 VAL rows.",
        size=12.5,
        fill=C["muted"],
    )
    svg.text(
        48,
        H - 15,
        "IDSLab reads TEST once, after the freeze, and reports whether the gain held. Here it did not.",
        size=12.5,
        fill=C["muted"],
    )
    svg.save(path)


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
    frozen = rep["frozen_per_seed"]
    removal = rep["stage_removal"]
    stages = [
        (st, f"{sum(not f[st]['off'] for f in frozen.values())} of {len(frozen)}", removal[st]["mean"])
        for st in next(iter(frozen.values()))
    ]
    prefix = {len(p["stages_on"]): p[goal] for p in s0["controls"]["prefix_test"]}
    reach = {t["stage"]: t["val_rows_reaching"] for t in s0["selection_trace"]}
    steps = []
    for k, t in enumerate(s0["selection_trace"], start=1):
        val = next(
            o[goal]
            for o in t["options"]
            if o.get("candidate") == t["chosen"].get("candidate")
            and o.get("value") == t["chosen"].get("value")
        )
        steps.append(
            {
                "label": ("first stage" if k == 1 else f"+ {t['stage']}"),
                "sub": f"{reach[t['stage']]:,} VAL rows reach it",
                "val": val,
                "test": prefix[k],
            }
        )
    return {
        "steps": steps,
        "n_seeds": len(frozen),
        "stages": stages,
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
        flow_map(args.run, args.out / "flow-map.svg")
        stage_steps(run, args.out / "stage-steps.svg")
    print(f"figures written to {args.out}")


if __name__ == "__main__":
    main()
