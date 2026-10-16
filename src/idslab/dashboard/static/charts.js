// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// SVG charts: grouped bars with error bars, and the cascade map drawn from parsed routes.

import {el, svg, fmt} from "./core.js";

const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--ref)"];

// groups: [{label, values: [{name, mean, sd}]}]; one bar per value, same order in every group.
export function barChart(groups, {height = 260, min = null, max = null, digits = 4} = {}) {
  const names = [...new Set(groups.flatMap((g) => g.values.map((v) => v.name)))];
  const all = groups.flatMap((g) => g.values.flatMap((v) => [v.mean - (v.sd || 0), v.mean + (v.sd || 0)]))
    .filter((x) => Number.isFinite(x));
  if (!all.length) return el("div", {class: "empty", text: "No values to plot."});
  let lo = min ?? Math.min(...all), hi = max ?? Math.max(...all);
  const pad = (hi - lo) * 0.15 || 0.01;
  lo = min ?? Math.max(0, lo - pad);
  hi = max ?? Math.min(1, hi + pad);
  if (hi <= lo) hi = lo + 0.01;
  const W = Math.max(420, groups.length * (names.length * 22 + 40) + 70), H = height;
  const L = 56, R = 12, T = 12, B = 34;
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const gw = (W - L - R) / groups.length, bw = Math.min(26, (gw - 24) / names.length);
  const ticks = 5, nodes = [];
  for (let i = 0; i <= ticks; i++) {
    const v = lo + (hi - lo) * i / ticks;
    nodes.push(svg("line", {class: "gridline", x1: L, x2: W - R, y1: y(v), y2: y(v)}));
    nodes.push(svg("text", {x: L - 8, y: y(v) + 4, "text-anchor": "end", text: v.toFixed(3)}));
  }
  groups.forEach((g, gi) => {
    const x0 = L + gi * gw + (gw - bw * names.length) / 2;
    g.values.forEach((v) => {
      const si = names.indexOf(v.name);
      if (!Number.isFinite(v.mean)) return;
      const x = x0 + si * bw, top = y(Math.min(hi, Math.max(lo, v.mean)));
      const bar = svg("rect", {x: x + 2, y: top, width: bw - 4, height: Math.max(1, y(lo) - top), rx: 3,
        style: {fill: SERIES[si % SERIES.length]}});
      bar.append(svg("title", {text: `${g.label}, ${v.name}: ${fmt(v.mean, digits)}` +
        (v.sd != null ? ` (sd ${fmt(v.sd, digits)})` : "")}));
      nodes.push(bar);
      if (v.sd) {
        const cx = x + bw / 2;
        const a = y(Math.min(hi, v.mean + v.sd)), b = y(Math.max(lo, v.mean - v.sd));
        nodes.push(svg("line", {x1: cx, x2: cx, y1: a, y2: b, class: "errbar"}));
        nodes.push(svg("line", {x1: cx - 4, x2: cx + 4, y1: a, y2: a, class: "errbar"}));
        nodes.push(svg("line", {x1: cx - 4, x2: cx + 4, y1: b, y2: b, class: "errbar"}));
      }
    });
    nodes.push(svg("text", {x: L + gi * gw + gw / 2, y: H - 12, "text-anchor": "middle", class: "label-strong",
      text: g.label}));
  });
  nodes.push(svg("line", {class: "axis", x1: L, x2: W - R, y1: y(lo), y2: y(lo)}));
  return el("div", {},
    el("div", {class: "cascade-wrap"},
      svg("svg", {class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Bar chart"}, nodes)),
    el("div", {class: "legend"}, names.map((n, i) =>
      el("span", {}, el("i", {style: {background: SERIES[i % SERIES.length]}}), n))));
}

// Cascade map. desc: the /api/validate "config" block. frozen (optional): one unit's frozen
// configuration, to mark stages switched off on VAL and show the chosen values. Stages sit in
// columns by routing depth; a route to BENIGN or ATTACK ends in a chip under its stage, so no
// edge crosses a node.
export function cascadeMap(desc, frozen) {
  const stages = Object.fromEntries(desc.stages.map((s) => [s.name, s]));
  const order = desc.order;
  const col = {[desc.entry]: 0};
  for (let pass = 0; pass < order.length; pass++) {
    for (const name of order) {
      if (col[name] == null) continue;
      for (const t of Object.values(stages[name].routes)) {
        if (stages[t] && (col[t] == null || col[t] < col[name] + 1)) col[t] = col[name] + 1;
      }
    }
  }
  for (const name of order) if (col[name] == null) col[name] = 0;  // reached only as a score source
  const ncol = Math.max(...Object.values(col)) + 1;
  const byCol = Array.from({length: ncol}, () => []);
  for (const name of order) byCol[col[name]].push(name);
  const NW = 210, NH = 66, GX = 90, PAD = 20, TW = 86, TH = 26, CHIP = 62, GY = 26;
  const slot = NH + CHIP + GY;
  const rows = Math.max(...byCol.map((c) => c.length));
  const H = PAD * 2 + rows * slot;
  const W = PAD * 2 + ncol * NW + (ncol - 1) * GX;
  const pos = {};
  byCol.forEach((names, c) => {
    const top = (H - names.length * slot) / 2;
    names.forEach((n, i) => { pos[n] = {x: PAD + c * (NW + GX), y: top + i * slot + GY / 2}; });
  });
  const marker = (id, cls) => svg("marker", {id, viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7,
    markerHeight: 7, orient: "auto"}, svg("path", {d: "M0,0 L8,4 L0,8 z", class: cls}));
  const defs = svg("defs", {}, marker("arr", "arrow"), marker("arr-feed", "arrow feed"));
  const edges = [], labels = [], chips = [];
  for (const name of order) {
    const s = stages[name], p = pos[name];
    const outs = Object.entries(s.routes);
    const toStage = outs.filter(([, t]) => stages[t]);
    const toTerm = outs.filter(([, t]) => !stages[t]);
    toStage.forEach(([out, t], i) => {
      const a = {x: p.x + NW, y: p.y + NH * (i + 1) / (toStage.length + 1)};
      const b = {x: pos[t].x, y: pos[t].y + NH / 2};
      const mx = (a.x + b.x) / 2;
      edges.push(svg("path", {class: "edge", d: `M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x - 2},${b.y}`,
        "marker-end": "url(#arr)"}));
      labels.push(svg("text", {class: "edge-label", x: a.x + 6, y: a.y - 5, text: out}));
    });
    toTerm.forEach(([out, t], i) => {
      const cx = p.x + NW / 2 + (i - (toTerm.length - 1) / 2) * (TW + 14);
      const cy = p.y + NH + CHIP - TH;
      edges.push(svg("path", {class: "edge", d: `M${cx},${p.y + NH} L${cx},${cy - 2}`, "marker-end": "url(#arr)"}));
      labels.push(svg("text", {class: "edge-label", x: cx + 5, y: p.y + NH + 18, text: out}));
      chips.push(svg("g", {class: `term ${String(t).toLowerCase()}`, transform: `translate(${cx - TW / 2},${cy})`},
        svg("rect", {width: TW, height: TH, rx: TH / 2}),
        svg("text", {x: TW / 2, y: TH / 2 + 4, "text-anchor": "middle", text: t})));
    });
    s.sources.forEach((src) => {
      if (!pos[src]) return;
      const a = {x: pos[src].x + NW - 14, y: pos[src].y}, b = {x: p.x + 14, y: p.y};
      const lift = Math.min(a.y, b.y) - GY * 0.9;
      edges.push(svg("path", {class: "edge feed", d: `M${a.x},${a.y} C${a.x},${lift} ${b.x},${lift} ${b.x},${b.y - 2}`,
        "marker-end": "url(#arr-feed)"}));
    });
  }
  const nodes = order.map((name) => {
    const s = stages[name], p = pos[name], f = frozen?.[name];
    const plural = s.candidates.length === 1 ? "" : "s";
    let l2 = `${s.kind}${s.role === "annotate" ? ", annotate" : ""}, ${s.candidates.length} candidate${plural}`;
    let l3 = `${s.decision} ${s.values.slice(0, 4).join(" ")}${s.values.length > 4 ? " ..." : ""}` +
      (s.tunable ? ", tuned" : ", fixed");
    if (f && f.off) { l2 = "switched off on VAL"; l3 = `every flow goes ${s.off}`; }
    else if (f) {
      l2 = f.candidate || s.kind;
      l3 = `${f.decision_mode} ${fmt(f.value, 3)}` + (f.threshold !== f.value ? `, threshold ${fmt(f.threshold, 3)}` : "");
    }
    const g = svg("g", {class: `node${name === desc.entry ? " entry" : ""}${f?.off ? " off" : ""}`,
      transform: `translate(${p.x},${p.y})`},
      svg("rect", {width: NW, height: NH, rx: 10}),
      svg("text", {class: "title", x: 12, y: 21, text: clip(name, 26)}),
      svg("text", {class: "meta", x: 12, y: 39, text: clip(l2, 34)}),
      svg("text", {class: "meta", x: 12, y: 55, text: clip(l3, 34)}));
    g.append(svg("title", {text: `${name}\ncandidates: ${s.candidates.join(", ")}\n${s.decision}: ${s.values.join(", ")}` +
      (s.sources.length ? `\nuses scores of: ${s.sources.join(", ")}` : "")}));
    return g;
  });
  return el("div", {class: "cascade-wrap"},
    svg("svg", {class: "chart cascade", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Cascade map of ${desc.name}`,
      style: {minWidth: `${Math.min(W, 900)}px`}}, defs, edges, nodes, chips, labels));
}

const clip = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + "." : String(s));
