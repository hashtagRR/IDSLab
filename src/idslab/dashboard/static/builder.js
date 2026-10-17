// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Visual cascade builder. It edits the shared draft's config data; the server writes the YAML and
// validates it with the CLI's loader after every change. Routes can be drawn by dragging a port,
// by clicking a port and then a destination, from the keyboard, or with the route lists in the
// inspector.

import {el, svg, card, cardBody, note, toast, field, checkbox, ask, rerender, KIND, KIND_SHORT, plainError} from "./core.js";
import {bound, parseList, listText, names} from "./draft.js";

const TERMS = ["ATTACK", "BENIGN"];
const OUTPUTS = {classifier: ["attack", "benign"], detector: ["anomalous", "normal"]};
const DECISIONS = {
  classifier: ["threshold", "grid"],
  detector: ["threshold", "grid", "budget", "top_fraction"],
  multiclass: ["confidence"],
};
const DECISION_HINT = {
  threshold: "one number; a score at or above it takes attack (or anomalous)",
  grid: "a list of cutoffs; the best on VAL is kept, and switching the stage off is one of the options",
  budget: "share of benign VAL flows the detector may flag; a list is chosen on VAL",
  top_fraction: "share of the flows reaching the stage to flag",
  confidence: "below this top probability the type is uncertain; a list is chosen on VAL",
};
const NW = 196, PORT = 20, COLW = 252, ROWH = 150;

const shortClass = (c) => String(c || "").split(".").pop().replace("Classifier", "");

export function outputsOf(st) {
  if (st.kind === "multiclass") {
    if ((st.role || "annotate") === "annotate") return ["next"];
    return Object.keys(st.routes || {}).length ? Object.keys(st.routes) : ["default"];
  }
  return OUTPUTS[st.kind] || [];
}
const targetOf = (st, out) => (out === "next" ? st.next : (st.routes || {})[out]);
function setTarget(st, out, target) {
  if (out === "next") { if (target) st.next = target; else delete st.next; return; }
  st.routes = {...(st.routes || {})};
  if (target) st.routes[out] = target; else delete st.routes[out];
}
const outName = (o) => (o === "next" ? "output" : `${o} output`);

// Problems visible in the drawing itself: outputs without a destination, loops, stages no route
// reaches. The server's validation covers everything else; the draft stays editable either way.
export function graphIssues(raw) {
  const st = raw.stages || {};
  const out = [];
  if (!st[raw.entry]) out.push({stage: null, text: "Choose an entry stage: the stage every flow starts at."});
  for (const [n, s] of Object.entries(st)) {
    for (const o of outputsOf(s)) {
      const t = targetOf(s, o);
      if (!t) out.push({stage: n, out: o, text: `${n}: the ${outName(o)} has no destination. Choose a stage, ATTACK or BENIGN.`});
      else if (!st[t] && !TERMS.includes(t)) out.push({stage: n, out: o, text: `${n}: the ${outName(o)} goes to ${t}, which is not a stage. Choose a stage, ATTACK or BENIGN.`});
    }
  }
  const cyc = cycle(st);
  if (cyc) out.push({stage: cyc[0], cycle: cyc, text: `The routes loop: ${cyc.join(" to ")}. Point one of these outputs to a later stage or a terminal.`});
  const reached = new Set();
  const walk = (n) => {
    if (!st[n] || reached.has(n)) return;
    reached.add(n);
    for (const o of outputsOf(st[n])) walk(targetOf(st[n], o));
  };
  walk(raw.entry);
  const sources = new Set(Object.values(st).flatMap((s) => [...(s.extra_features || []), s.hard?.source].filter(Boolean)));
  for (const n of Object.keys(st)) {
    if (!reached.has(n) && !sources.has(n) && st[raw.entry]) {
      out.push({stage: n, text: `${n} is not reached: no route leads to it from the entry stage. Send an output to it, or remove it.`});
    }
  }
  return out;
}
function cycle(st) {
  const color = {}, stack = [];
  let found = null;
  const visit = (n) => {
    color[n] = 1;
    stack.push(n);
    for (const o of outputsOf(st[n])) {
      const t = targetOf(st[n], o);
      if (!st[t] || found) continue;
      if (color[t] === 1) { found = [...stack.slice(stack.indexOf(t)), t]; return; }
      if (!color[t]) visit(t);
    }
    stack.pop();
    color[n] = 2;
  };
  for (const n of Object.keys(st)) if (!color[n] && !found) visit(n);
  return found;
}

// ---------- edits on the config ----------

function encodeStep(draft) {
  const cats = draft.dataset?.suggest?.categorical;
  const existing = Object.values(draft.raw.stages || {}).flatMap((s) => s.pipeline || []).find((p) => p.class === "idslab.prep.Encode");
  const categorical = existing?.params?.categorical || cats;
  return categorical?.length ? [{class: "idslab.prep.Encode", params: {categorical: [...categorical]}}] : [];
}
export function stageDefaults(kind, prev = {}) {
  const pipeline = prev.pipeline;
  if (kind === "classifier") {
    return {kind, pipeline, candidates: [{class: "sklearn.ensemble.RandomForestClassifier"}],
      decision: {threshold: 0.5, off: "benign"}, routes: {attack: "ATTACK", benign: "BENIGN"}};
  }
  if (kind === "detector") {
    return {kind, fit_on: "benign", pipeline, candidates: [{class: "sklearn.ensemble.IsolationForest"}],
      decision: {budget: [0.01, 0.05], off: "normal"}, routes: {anomalous: "ATTACK", normal: "BENIGN"}};
  }
  return {kind: "multiclass", role: "annotate", pipeline, candidates: [{class: "sklearn.ensemble.HistGradientBoostingClassifier"}],
    decision: {confidence: [0.0, 0.2, 0.4]}, next: "ATTACK"};
}
export function addStage(draft, kind) {
  const raw = draft.raw;
  raw.stages = raw.stages || {};
  const base = {classifier: "classifier", detector: "detector", multiclass: "type"}[kind];
  let n = base, i = 1;
  while (raw.stages[n]) n = `${base}${++i}`;
  const st = stageDefaults(kind, {pipeline: encodeStep(draft)});
  if (!st.pipeline.length) delete st.pipeline;
  raw.stages[n] = st;
  if (!raw.entry || !raw.stages[raw.entry]) raw.entry = n;
  return n;
}
function renameStage(draft, from, to) {
  const raw = draft.raw;
  const stages = {};
  for (const [n, s] of Object.entries(raw.stages)) stages[n === from ? to : n] = s;
  raw.stages = stages;
  for (const s of Object.values(stages)) {
    for (const k of Object.keys(s.routes || {})) if (s.routes[k] === from) s.routes[k] = to;
    if (s.next === from) s.next = to;
    if (s.extra_features) s.extra_features = s.extra_features.map((x) => (x === from ? to : x));
    if (s.hard?.source === from) s.hard.source = to;
  }
  if (raw.entry === from) raw.entry = to;
  if (raw.controls?.first_stage === from) raw.controls.first_stage = to;
  if (draft.positions[from]) { draft.positions[to] = draft.positions[from]; delete draft.positions[from]; }
}
function removeStage(draft, name) {
  const raw = draft.raw;
  delete raw.stages[name];
  delete draft.positions[name];
  for (const s of Object.values(raw.stages)) {
    for (const k of Object.keys(s.routes || {})) if (s.routes[k] === name) delete s.routes[k];
    if (s.next === name) delete s.next;
    if (s.extra_features) {
      s.extra_features = s.extra_features.filter((x) => x !== name);
      if (!s.extra_features.length) delete s.extra_features;
    }
    if (s.hard?.source === name) delete s.hard.source;
  }
  if (raw.entry === name) raw.entry = Object.keys(raw.stages)[0] || "";
  if (raw.controls?.first_stage === name) delete raw.controls.first_stage;
}

// ---------- the component ----------

// box: the element to fill. ctx: {catalog}. Returns {destroy}.
export function mountCascade(box, draft, ctx) {
  const UI = {selected: draft.raw?.entry || Object.keys(draft.raw?.stages || {})[0] || null, pending: null,
    open: new Set(["candidates"])};
  const issuesBox = el("div", {class: "stack", "aria-live": "polite"});
  const hint = el("div", {class: "bl-hint muted small", role: "status"});
  const canvasWrap = el("div", {class: "bl-canvas"});
  const inspector = el("div", {class: "stack bl-inspector"});

  const palette = el("div", {class: "bl-palette"},
    el("div", {class: "section-title", text: "Add a stage"}),
    ["classifier", "detector", "multiclass"].map((k) => el("button", {
      class: `bl-chip ${k}`, type: "button", draggable: "true", title: "Click to add, or drag onto the canvas",
      ondragstart: (e) => e.dataTransfer.setData("text/x-idslab-kind", k),
      onclick: () => { const n = addStage(draft, k); UI.selected = n; draft.commit(); },
    }, el("b", {text: KIND_SHORT[k]}), el("small", {text: {
      classifier: "attack or benign", detector: "anomalous or normal", multiclass: "names the attack type",
    }[k]}))),
    el("div", {class: "section-title", text: "How to connect"}),
    el("ul", {class: "bl-help"},
      el("li", {text: "Drag a port (the dots on a stage's right edge) onto a stage or a terminal."}),
      el("li", {text: "Or click a port, then click where that output should go. Escape cancels."}),
      el("li", {text: "From the keyboard: Tab to a port, press Enter, Tab to the destination, press Enter."}),
      el("li", {text: "The orange diamond under a stage passes its score to a later stage as a feature."}),
      el("li", {text: "Drag stages, or use the arrow keys on a selected stage, to arrange them. Delete removes it."})));

  box.replaceChildren(el("div", {class: "stack"}, issuesBox,
    el("div", {class: "bl-grid"}, palette, el("div", {class: "stack"}, hint, canvasWrap), el("div", {class: "bl-side-panel"}, inspector))));

  const unsub = draft.on((what) => {
    if (what === "text") return;  // the YAML view is typing; redraw once it is parsed
    if (!draft.raw) { renderUnavailable(); return; }
    if (UI.selected && !draft.raw.stages?.[UI.selected]) UI.selected = draft.raw.entry || null;
    renderIssues();
    renderCanvas();
    if (what === "validation" || what === "layout") markInspectorErrors();
    else if (what !== "dataset") rerender(inspector, renderInspector);
  });
  const onKey = (e) => {
    if (e.key === "Escape" && UI.pending) { UI.pending = null; renderCanvas(); setHint(); }
  };
  addEventListener("keydown", onKey);

  function renderUnavailable() {
    issuesBox.replaceChildren(note("The YAML does not parse, so the cascade cannot be drawn. Fix it in the YAML view; " +
      `the loader says: ${draft.validation?.error || "unknown error"}`, "bad"));
    canvasWrap.replaceChildren();
    inspector.replaceChildren();
  }

  function setHint() {
    const p = UI.pending;
    hint.textContent = p
      ? (p.feed ? `Choose the stage that should use ${p.from}'s score: click it or press Enter on it. Escape cancels.`
        : `Choose where ${p.from}'s ${outName(p.out)} goes: click a stage or ATTACK or BENIGN, or press Enter on it. Escape cancels.`)
      : "";
  }

  function connect(target) {
    const p = UI.pending;
    UI.pending = null;
    setHint();
    const st = draft.raw.stages;
    if (!p || !st[p.from]) return;
    if (p.feed) {
      if (!st[target] || target === p.from) { toast("A score can feed another stage, not a terminal"); renderCanvas(); return; }
      st[target].extra_features = [...new Set([...(st[target].extra_features || []), p.from])];
    } else {
      if (target === p.from) { toast("A stage cannot route to itself"); renderCanvas(); return; }
      setTarget(st[p.from], p.out, target);
    }
    draft.commit();
  }

  function select(name) {
    UI.selected = name;
    renderCanvas();
    renderInspector();
  }

  // ---------- issues ----------

  function renderIssues() {
    const list = graphIssues(draft.raw);
    issuesBox.replaceChildren(...list.map((i) => el("div", {class: `note ${i.warn ? "warn" : "bad"} bl-issue`},
      el("span", {text: i.text}),
      i.stage && draft.raw.stages[i.stage] ? el("button", {class: "btn small", type: "button", text: "Show",
        onclick: () => {
          select(i.stage);
          if (i.out) { UI.pending = {from: i.stage, out: i.out}; setHint(); renderCanvas(); }
        }}) : null)));
  }

  // ---------- canvas ----------

  function layout() {
    const st = draft.raw.stages || {}, all = Object.keys(st);
    const col = {};
    if (st[draft.raw.entry]) col[draft.raw.entry] = 0;
    for (let pass = 0; pass < all.length; pass++) {
      for (const n of all) {
        if (col[n] == null) continue;
        for (const o of outputsOf(st[n])) {
          const t = targetOf(st[n], o);
          if (st[t] && (col[t] == null || col[t] < col[n] + 1) && col[n] + 1 < all.length) col[t] = col[n] + 1;
        }
      }
    }
    const rows = {}, pos = {};
    for (const n of all) {
      const c = col[n] ?? 0;
      rows[c] = (rows[c] || 0) + 1;
      pos[n] = draft.positions[n] || [40 + c * COLW, 40 + (rows[c] - 1) * ROWH];
    }
    return pos;
  }
  const nodeH = (s) => Math.max(84, 50 + outputsOf(s).length * PORT);

  function renderCanvas() {
    const focusKey = document.activeElement?.getAttribute?.("data-key");
    const st = draft.raw.stages || {};
    const pos = layout();
    const cyc = graphIssues(draft.raw).find((i) => i.cycle)?.cycle || [];
    const cycEdges = new Set(cyc.slice(0, -1).map((n, i) => `${n}>${cyc[i + 1]}`));
    const errStages = new Set(draft.errors().map((l) => /^stage (\S+?)(?=[: ])/.exec(l)?.[1]).filter(Boolean));
    const xs = Object.values(pos).map((p) => p[0] + NW), ys = Object.values(pos).map((p) => p[1]);
    const termX = Math.max(300, ...xs) + 90;
    const termPos = {ATTACK: [termX, 40], BENIGN: [termX, Math.max(220, ...ys.map((y) => y + 60))]};
    const W = termX + 140, H = Math.max(420, ...ys.map((y) => y + 200), termPos.BENIGN[1] + 120);
    const root = svg("svg", {class: `bl-svg${UI.pending ? " routing" : ""}`, viewBox: `0 0 ${W} ${H}`,
      role: "group", "aria-label": "Cascade canvas",
      style: {width: "100%", minWidth: `${Math.round(W * 0.75)}px`}},
    svg("defs", {}, ...["bl-arr", "bl-arr-feed", "bl-arr-bad"].map((id) => svg("marker", {id, viewBox: "0 0 8 8", refX: 7, refY: 4,
      markerWidth: 7, markerHeight: 7, orient: "auto"}, svg("path", {d: "M0,0 L8,4 L0,8 z",
      class: `bl-arrow${id === "bl-arr-feed" ? " feed" : id === "bl-arr-bad" ? " bad" : ""}`})))));
    const edges = svg("g", {}), nodes = svg("g", {});
    root.append(edges, nodes);
    const inPoint = (t) => (st[t] ? [pos[t][0], pos[t][1] + 24] : termPos[t] ? [termPos[t][0], termPos[t][1] + 20] : null);

    for (const [name, s] of Object.entries(st)) {
      const [x, y] = pos[name];
      outputsOf(s).forEach((o, i) => {
        const t = targetOf(s, o), b = inPoint(t);
        const a = [x + NW, y + 50 + i * PORT];
        if (!b) {  // an output with no destination: a short dashed stub, so the gap is visible
          edges.append(svg("path", {class: "bl-edge missing", d: `M${a[0]},${a[1]} L${a[0] + 34},${a[1]}`}),
            svg("text", {class: "bl-missing-label", x: a[0] + 38, y: a[1] + 4, text: "?"}));
          return;
        }
        const mx = Math.max(a[0] + 30, (a[0] + b[0]) / 2);
        const bad = cycEdges.has(`${name}>${t}`);
        edges.append(svg("path", {class: `bl-edge${bad ? " cycle" : ""}`, d: `M${a[0]},${a[1]} C${mx},${a[1]} ${mx},${b[1]} ${b[0] - 3},${b[1]}`,
          "marker-end": bad ? "url(#bl-arr-bad)" : "url(#bl-arr)"}));
      });
      for (const src of s.extra_features || []) {
        if (!pos[src]) continue;
        const a = [pos[src][0] + NW / 2, pos[src][1] + nodeH(st[src])], b = [x + NW / 2, y];
        const dip = Math.max(a[1], b[1]) + 40;
        const g = svg("path", {class: "bl-edge feed", d: `M${a[0]},${a[1]} C${a[0]},${dip} ${b[0]},${b[1] - 60} ${b[0]},${b[1] - 3}`,
          "marker-end": "url(#bl-arr-feed)"});
        g.append(svg("title", {text: `${src} score feeds ${name}. Click to remove.`}));
        g.addEventListener("click", () => {
          s.extra_features = s.extra_features.filter((z) => z !== src);
          if (!s.extra_features.length) delete s.extra_features;
          draft.commit();
        });
        edges.append(g);
      }
    }
    for (const t of TERMS) {
      const [x, y] = termPos[t];
      const g = svg("g", {class: `bl-term ${t.toLowerCase()}`, "data-target": t, "data-key": `t:${t}`,
        transform: `translate(${x},${y})`, tabindex: UI.pending && !UI.pending.feed ? "0" : "-1", role: "button",
        "aria-label": `terminal ${t}`},
      svg("rect", {width: 120, height: 40, rx: 20}),
      svg("text", {x: 60, y: 25, "text-anchor": "middle", text: t}));
      const pick = () => { if (UI.pending) connect(t); };
      g.addEventListener("click", pick);
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      nodes.append(g);
    }
    for (const [name, s] of Object.entries(st)) {
      const [x, y] = pos[name];
      const h = nodeH(s);
      const cls = ["bl-node", s.kind, name === UI.selected ? "sel" : "", name === draft.raw.entry ? "entry" : "",
        errStages.has(name) ? "err" : "", UI.pending ? "target" : ""].join(" ");
      const g = svg("g", {class: cls, "data-target": name, "data-key": `n:${name}`, transform: `translate(${x},${y})`,
        tabindex: "0", role: "button", "aria-pressed": String(name === UI.selected),
        "aria-label": `${KIND_SHORT[s.kind] || s.kind} stage ${name}${name === draft.raw.entry ? ", entry" : ""}`});
      g.append(svg("rect", {class: "body", width: NW, height: h, rx: 10}),
        svg("rect", {class: "stripe", x: 0, y: 10, width: 4, height: h - 20, rx: 2}),
        svg("text", {class: "t", x: 14, y: 22, text: name + (name === draft.raw.entry ? "  (entry)" : "")}),
        svg("text", {class: "m", x: 14, y: 38, text: `${KIND_SHORT[s.kind] || s.kind}: ${(s.candidates || []).map((c) => c.external ? "score file" : shortClass(c.class)).join(", ").slice(0, 24)}`}));
      outputsOf(s).forEach((o, i) => {
        const py = 50 + i * PORT;
        const t = targetOf(s, o);
        const linked = inPoint(t);
        const active = UI.pending && !UI.pending.feed && UI.pending.from === name && UI.pending.out === o;
        g.append(svg("text", {class: "port-label", x: NW - 14, y: py + 4, "text-anchor": "end", text: o}));
        const port = svg("circle", {class: `port${linked ? "" : " open"}${active ? " active" : ""}`, cx: NW, cy: py, r: 7,
          tabindex: "0", role: "button", "data-key": `p:${name}:${o}`,
          "aria-label": `${name} ${outName(o)}, goes to ${t || "nowhere yet"}. Press Enter to choose a destination.`});
        port.append(svg("title", {text: `${o}: drag to a stage or terminal, or click and then click the destination`}));
        port.addEventListener("pointerdown", (e) => startWire(e, root, [x + NW, y + py], (hit) => {
          UI.pending = {from: name, out: o};
          connect(hit);
        }, () => { UI.pending = {from: name, out: o}; setHint(); renderCanvas(); }));
        port.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            UI.pending = {from: name, out: o};
            setHint();
            renderCanvas();
          }
        });
        g.append(port);
      });
      if (s.kind !== "detector" || s.fit_on !== "pool") {
        const active = UI.pending?.feed && UI.pending.from === name;
        const sp = svg("rect", {class: `score-port${active ? " active" : ""}`, x: NW / 2 - 7, y: h - 7, width: 14, height: 14, rx: 2,
          transform: `rotate(45 ${NW / 2} ${h})`, tabindex: "0", role: "button", "data-key": `f:${name}`,
          "aria-label": `${name} score. Press Enter, then choose a later stage to use it as a feature.`});
        sp.append(svg("title", {text: "score: drag (or click, then click) to a later stage to use it as a feature"}));
        const begin = () => { UI.pending = {from: name, feed: true}; setHint(); renderCanvas(); };
        sp.addEventListener("pointerdown", (e) => startWire(e, root, [x + NW / 2, y + h], (hit) => {
          UI.pending = {from: name, feed: true};
          connect(hit);
        }, begin, true));
        sp.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); begin(); } });
        g.append(sp);
      }
      g.addEventListener("pointerdown", (e) => {
        if (e.target.closest(".port, .score-port")) return;
        startMove(e, root, name, pos);
      });
      g.addEventListener("keydown", (e) => {
        if (e.target !== g) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (UI.pending) connect(name); else select(name); return; }
        if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); confirmRemove(name); return; }
        const step = {ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10]}[e.key];
        if (step) {
          e.preventDefault();
          const cur = layout();
          draft.positions = {...cur, [name]: [Math.max(0, cur[name][0] + step[0]), Math.max(0, cur[name][1] + step[1])]};
          draft.touched = true;
          draft.emit("layout");
        }
      });
      nodes.append(g);
    }
    root.addEventListener("dragover", (e) => e.preventDefault());
    root.addEventListener("drop", (e) => {
      const kind = e.dataTransfer.getData("text/x-idslab-kind");
      if (!kind) return;
      e.preventDefault();
      const p = toSvg(root, e);
      const n = addStage(draft, kind);
      draft.positions = {...layout(), [n]: [Math.max(10, p.x - NW / 2), Math.max(10, p.y - 30)]};
      UI.selected = n;
      draft.commit();
    });
    canvasWrap.replaceChildren(root);
    if (focusKey) root.querySelector(`[data-key="${CSS.escape(focusKey)}"]`)?.focus();
  }

  function toSvg(root, e) {  // client coordinates to drawing coordinates (the drawing is scaled to fit)
    const pt = root.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const r = pt.matrixTransform(root.getScreenCTM().inverse());
    return {x: r.x, y: r.y};
  }
  // drag from a port; a press without movement counts as a click, which starts click-to-route
  function startWire(e, root, from, onDrop, onClick, feed = false) {
    e.preventDefault();
    e.stopPropagation();
    const start = [e.clientX, e.clientY];
    let moved = false;
    const line = svg("path", {class: `bl-edge temp${feed ? " feed" : ""}`});
    root.append(line);
    const move = (ev) => {
      if (Math.abs(ev.clientX - start[0]) + Math.abs(ev.clientY - start[1]) > 4) moved = true;
      const p = toSvg(root, ev);
      line.setAttribute("d", `M${from[0]},${from[1]} L${p.x},${p.y}`);
    };
    const up = (ev) => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      line.remove();
      if (!moved) { onClick(); return; }
      const hit = document.elementFromPoint(ev.clientX, ev.clientY)?.closest("[data-target]");
      if (hit) onDrop(hit.getAttribute("data-target"));
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
  }
  function startMove(e, root, name, pos) {
    const start = toSvg(root, e), origin = [...pos[name]];
    let moved = false;
    const node = e.target.closest(".bl-node");
    const move = (ev) => {
      const p = toSvg(root, ev);
      const dx = p.x - start.x, dy = p.y - start.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      node.setAttribute("transform", `translate(${Math.max(0, origin[0] + dx)},${Math.max(0, origin[1] + dy)})`);
    };
    const up = (ev) => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      if (moved) {
        const p = toSvg(root, ev);
        draft.positions = {...layout(), [name]: [Math.max(0, origin[0] + p.x - start.x), Math.max(0, origin[1] + p.y - start.y)]};
        draft.touched = true;
        UI.selected = name;
        draft.emit("layout");
        renderInspector();
        return;
      }
      if (UI.pending) { connect(name); return; }
      select(name);
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
  }

  async function confirmRemove(name) {
    if (!(await ask("Remove stage", `Remove ${name}? Outputs that led to it are left without a destination until you choose a new one.`,
      "Remove", "danger"))) return;
    removeStage(draft, name);
    UI.selected = draft.raw.entry || null;
    draft.commit();
  }

  // ---------- inspector ----------

  function section(key, title, body, sub) {
    const d = el("details", {class: "trace bl-section", open: UI.open.has(key)},
      el("summary", {}, title, sub ? el("span", {class: "muted small", text: ` ${sub}`}) : null), el("div", {class: "stack"}, body));
    d.addEventListener("toggle", () => { if (d.open) UI.open.add(key); else UI.open.delete(key); });
    return d;
  }

  function markInspectorErrors() {
    const box = inspector.querySelector(".bl-stage-errors");
    if (box && UI.selected) fillStageErrors(box, UI.selected);
  }
  function fillStageErrors(box, name) {
    const mine = draft.errors().filter((l) => l.startsWith(`stage ${name}:`) || l.startsWith(`stage ${name} `));
    box.replaceChildren(...mine.map((l) => {
      const plain = plainError(l);
      return el("div", {class: "note bad"}, plain, plain !== l ? el("div", {class: "mono small muted", text: l}) : null);
    }));
  }

  function renderInspector() {
    inspector.replaceChildren();
    const s = draft.raw.stages?.[UI.selected];
    if (!s) {
      inspector.append(note(Object.keys(draft.raw.stages || {}).length
        ? "Select a stage on the canvas to edit it." : "Add a stage from the list on the left.", "info"));
      return;
    }
    inspector.append(stagePanel(UI.selected, s));
  }

  function stagePanel(name, s) {
    const nameInput = el("input", {class: "input mono", value: name, "aria-label": "Stage name"});
    nameInput.addEventListener("change", () => {
      const nn = nameInput.value.trim();
      if (!nn || nn === name) return;
      if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(nn)) { toast("Use letters, digits, '.', '-' and '_'"); nameInput.value = name; return; }
      if (draft.raw.stages[nn] || TERMS.includes(nn)) { toast(`${nn} is taken`); nameInput.value = name; return; }
      renameStage(draft, name, nn);
      UI.selected = nn;
      draft.commit();
    });
    const entry = checkbox("Entry stage", "every flow starts here", draft.raw.entry === name);
    entry.input.addEventListener("change", () => { if (entry.input.checked) { draft.raw.entry = name; draft.commit(); } else entry.input.checked = true; });
    const kind = el("select", {class: "select", "aria-label": "Kind"}, Object.keys(KIND).map((k) => el("option", {value: k, text: KIND[k]})));
    kind.value = s.kind;
    kind.addEventListener("change", () => {
      const d = stageDefaults(kind.value, s);
      for (const k of Object.keys(s)) if (!(k in d)) delete s[k];
      Object.assign(s, d);
      if (!s.pipeline) delete s.pipeline;
      draft.commit();
    });
    const remove = el("button", {class: "btn danger small", type: "button", text: "Remove stage", onclick: () => confirmRemove(name)});
    const errs = el("div", {class: "stack bl-stage-errors"});
    fillStageErrors(errs, name);

    const isDetector = s.kind === "detector", isType = s.kind === "multiclass";
    return el("div", {class: "stack"},
      card(name, KIND[s.kind] || s.kind, cardBody(el("div", {class: "stack"},
        errs,
        el("div", {class: "form-grid"}, field("Name", nameInput), field("Kind", kind)),
        el("div", {class: "row"}, entry.node, el("span", {class: "spacer"}), remove),
        el("div", {class: "section-title", text: "Where each output goes"}), routesForm(name, s),
        el("div", {class: "section-title", text: "Decision"}), decisionForm(s)))),
      section("candidates", "Models and parameters", candidatesForm(name, s), `${(s.candidates || []).length} candidate${(s.candidates || []).length === 1 ? "" : "s"}`),
      !isDetector ? section("search", "Hyperparameter search", searchForm(s)) : null,
      section("pipeline", "Preprocessing pipeline", pipelineForm(s), s.pipeline?.length ? `${s.pipeline.length} step${s.pipeline.length === 1 ? "" : "s"}` : "none"),
      section("selection", isDetector ? "Selection" : "Calibration and selection", selectionForm(s)),
      isType ? section("type", "Type stage", typeForm(s)) : null,
      isDetector ? section("detector", "Detector training", detectorForm(s)) : null,
      section("features", "Score features and training rows", featuresForm(name, s),
        s.extra_features?.length ? `uses ${s.extra_features.join(", ")}` : null),
      section("cost", "Declared cost", costForm(s), s.cost != null ? String(s.cost) : null));
  }

  function targetsFor(name) {
    return [...TERMS, ...Object.keys(draft.raw.stages).filter((n) => n !== name)];
  }
  function routesForm(name, s) {
    const box = el("div", {class: "stack"});
    if (s.kind === "multiclass") {
      const role = el("select", {class: "select", "aria-label": "Role"}, [["annotate", "annotate: names the type, the verdict does not change"],
        ["decide", "decide: sends flows on by type"]].map(([v, t]) => el("option", {value: v, text: t})));
      role.value = s.role || "annotate";
      role.addEventListener("change", () => {
        s.role = role.value;
        if (s.role === "annotate") { delete s.routes; s.next = s.next || "ATTACK"; if (s.decision) delete s.decision.off; }
        else { delete s.next; s.routes = s.routes || {default: "ATTACK"}; s.decision = {...(s.decision || {}), off: "default"}; }
        draft.commit();
      });
      box.append(field("Role", role));
    }
    for (const o of outputsOf(s)) {
      const sel = el("select", {class: "select", "aria-label": `${name} ${outName(o)} destination`},
        el("option", {value: "", text: "(no destination yet)"}),
        targetsFor(name).map((t) => el("option", {value: t, text: t})));
      const cur = targetOf(s, o) || "";
      if (cur && ![...sel.options].some((op) => op.value === cur)) sel.append(el("option", {value: cur, text: `${cur} (not a stage)`}));
      sel.value = cur;
      sel.addEventListener("change", () => { setTarget(s, o, sel.value || undefined); draft.commit(); });
      const row = el("div", {class: `bl-route${cur ? "" : " missing"}`}, el("span", {class: "mono", text: o === "next" ? "every flow" : o}), sel);
      if (s.kind === "multiclass" && s.role === "decide" && o !== "default") {
        row.append(el("button", {class: "btn small", type: "button", text: "Remove", "aria-label": `remove the ${o} route`,
          onclick: () => { delete s.routes[o]; draft.commit(); }}));
      }
      box.append(row);
    }
    if (s.kind === "multiclass" && s.role === "decide") {
      const types = draft.dataset?.typeValues || [];
      const add = el("input", {class: "input mono", placeholder: "type name or uncertain", list: "bl-types", "aria-label": "Type to route"});
      box.append(el("datalist", {id: "bl-types"}, [...types, "uncertain"].map((t) => el("option", {value: t}))),
        el("div", {class: "row"}, add, el("button", {class: "btn small", type: "button", text: "Add route", onclick: () => {
          const t = add.value.trim();
          if (!t) return;
          s.routes = {...s.routes, [t]: "ATTACK"};
          draft.commit();
        }})));
    }
    return box;
  }

  function decisionForm(s) {
    s.decision = s.decision || {};
    const d = s.decision;
    const modes = DECISIONS[s.kind] || [];
    const current = modes.find((m) => m in d) || modes[0];
    const mode = el("select", {class: "select", "aria-label": "Decision mode"}, modes.map((m) => el("option", {value: m, text: m.replace("_", " ")})));
    mode.value = current;
    const values = el("input", {class: "input mono", value: listText(d[current]), "aria-label": "Decision values"});
    const hintEl = el("div", {class: "hint", text: DECISION_HINT[current]});
    const apply = () => {
      const m = mode.value, vals = parseList(values.value).filter((x) => typeof x === "number");
      for (const k of modes) delete d[k];
      d[m] = m === "threshold" ? (vals[0] ?? 0.5) : m === "grid" ? vals : vals.length === 1 ? vals[0] : vals;
      hintEl.textContent = DECISION_HINT[m];
      draft.commit();
    };
    mode.addEventListener("change", apply);
    values.addEventListener("change", apply);
    const parts = [el("div", {class: "form-grid"}, field("Mode", mode), field("Values", values)), hintEl];
    if (!(s.kind === "multiclass" && (s.role || "annotate") === "annotate")) {
      parts.push(field("When the stage is switched off, every flow takes", bound(draft, d, "off",
        {options: [["", "(choose an output)"], ...outputsOf(s)]})));
    }
    return el("div", {class: "stack"}, parts);
  }

  function catalogSelect(section, value) {
    const entries = ctx.catalog[section] || [];
    const sel = el("select", {class: "select", "aria-label": "Model class"},
      el("optgroup", {label: "Installed"}, entries.filter((e) => e.installed).map((e) => el("option", {value: e.class, text: e.label}))),
      el("optgroup", {label: "Needs an extra"}, entries.filter((e) => !e.installed).map((e) =>
        el("option", {value: e.class, text: `${e.label} (pip install idslab[${e.extra}])`}))),
      el("option", {value: "__custom", text: "Other class path (custom adapter or plugin)..."}));
    sel.value = entries.some((e) => e.class === value) ? value : "__custom";
    return sel;
  }
  function paramsForm(section, obj) {
    const entry = (ctx.catalog[section] || []).find((e) => e.class === obj.class);
    const box = el("div", {class: "form-grid bl-params"});
    const known = new Set((entry?.params || []).map((p) => p.name));
    for (const p of entry?.params || []) {
      let input;
      const cur = obj.params?.[p.name];
      if (p.type === "bool") {
        input = el("select", {class: "select"}, [["", `default (${p.default})`], ["true", "true"], ["false", "false"]].map(([v, t]) => el("option", {value: v, text: t})));
        input.value = cur == null ? "" : String(cur);
      } else {
        input = el("input", {class: "input mono", placeholder: p.default == null ? "default" : listText(p.default),
          value: cur == null ? "" : Array.isArray(cur) ? listText(cur) : typeof cur === "object" ? JSON.stringify(cur) : String(cur)});
      }
      input.addEventListener("change", () => {
        const v = input.value.trim();
        obj.params = obj.params || {};
        if (v === "") delete obj.params[p.name];
        else if (p.type === "bool") obj.params[p.name] = v === "true";
        else if (p.type === "list") obj.params[p.name] = parseList(v);
        else if (p.type === "int" || p.type === "float") obj.params[p.name] = v === "null" ? null : Number(v);
        else obj.params[p.name] = v === "null" ? null : (Number.isFinite(Number(v)) ? Number(v) : v);
        if (!Object.keys(obj.params).length) delete obj.params;
        draft.commit();
      });
      box.append(field(p.name, input));
    }
    const extra = Object.keys(obj.params || {}).filter((k) => !known.has(k));
    const kept = extra.length ? el("div", {class: "hint", text: `Also set, kept as written: ${extra.map((k) => `${k}: ${JSON.stringify(obj.params[k])}`).join("; ")}. ` +
      "Edit these in the YAML view."}) : null;
    if (!entry) box.append(el("div", {class: "hint", text: "This class is not in the catalog; set its parameters in the YAML view."}));
    return el("div", {class: "stack"}, box, kept);
  }

  function candidatesForm(name, s) {
    s.candidates = s.candidates || [];
    const sectionName = s.kind === "detector" ? "detector" : "classifier";
    const box = el("div", {class: "stack"});
    if (!s.candidates.length) box.append(note("A stage needs at least one candidate model.", "warn"));
    s.candidates.forEach((c, i) => {
      const row = el("div", {class: "bl-item"});
      if (c.external) {
        const p = el("input", {class: "input mono", value: typeof c.external === "string" ? c.external : c.external.path || "", "aria-label": "Score file"});
        p.addEventListener("change", () => {
          const path = p.value.trim();
          c.external = typeof c.external === "object" ? {...c.external, path} : path;
          draft.commit();
        });
        row.append(field(`Candidate ${i + 1}: score file (may use {seed} and {family})`, p),
          field("Label", bound(draft, c, "label", {placeholder: "external"})));
      } else {
        const sel = catalogSelect(sectionName, c.class);
        const custom = el("input", {class: `input mono${sel.value === "__custom" ? "" : " hidden"}`, value: c.class || "",
          placeholder: "package.module.Class", "aria-label": "Class path"});
        sel.addEventListener("change", () => {
          custom.classList.toggle("hidden", sel.value !== "__custom");
          if (sel.value !== "__custom") { c.class = sel.value; delete c.params; delete c.search; draft.commit(); }
        });
        custom.addEventListener("change", () => { c.class = custom.value.trim(); delete c.params; draft.commit(); });
        row.append(field(`Candidate ${i + 1}`, el("div", {class: "stack"}, sel, custom)), paramsForm(sectionName, c));
        if (c.score != null) row.append(el("div", {class: "hint", text: `Score overrides kept as written: ${JSON.stringify(c.score)}.`}));
      }
      row.append(el("div", {class: "row"}, el("span", {class: "spacer"}),
        el("button", {class: "btn small danger", type: "button", text: "Remove", "aria-label": `remove candidate ${i + 1}`,
          onclick: () => { s.candidates.splice(i, 1); draft.commit(); }})));
      box.append(row);
    });
    box.append(el("div", {class: "row"},
      el("button", {class: "btn small", type: "button", text: "Add model", onclick: () => {
        s.candidates.push({class: (ctx.catalog[sectionName] || []).find((e) => e.installed)?.class}); draft.commit();
      }}),
      el("button", {class: "btn small", type: "button", text: "Add score file made elsewhere", onclick: () => {
        s.candidates.push({external: {path: `scores/${name}_{seed}.parquet`}}); draft.commit();
      }})));
    return box;
  }

  function searchForm(s) {
    const box = el("div", {class: "stack"});
    const models = (s.candidates || []).filter((c) => !c.external);
    if (!models.length) return note("No fitted candidates to search over.", "info");
    box.append(el("p", {class: "hint", text: "Cross-validated search on this stage's training rows, per candidate. Leave the space empty to use the parameters as set."}));
    for (const [i, c] of models.entries()) {
      const space = el("textarea", {class: "textarea mono", rows: 2, placeholder: "max_depth: 8, 16, null\nmin_samples_leaf: 1, 5",
        "aria-label": `Search space for candidate ${i + 1}`,
        value: Object.entries(c.search?.space || {}).map(([k, v]) => `${k}: ${listText(v)}`).join("\n")});
      const kind = el("select", {class: "select", "aria-label": "Search kind"}, [["random", "random"], ["grid", "grid (every combination)"]].map(([v, t]) => el("option", {value: v, text: t})));
      kind.value = c.search?.kind || "random";
      const iters = el("input", {class: "input", type: "number", min: "1", value: c.search?.n_iter ?? "", placeholder: "10", "aria-label": "Tries"});
      const cv = el("input", {class: "input", type: "number", min: "2", value: c.search?.cv ?? "", placeholder: "3", "aria-label": "Folds"});
      const apply = () => {
        const sp = {};
        for (const line of space.value.split("\n")) {
          const m = /^\s*([\w.]+)\s*:\s*(.+)$/.exec(line);
          if (m) sp[m[1]] = parseList(m[2]);
        }
        if (Object.keys(sp).length) {
          c.search = {space: sp, ...(kind.value !== "random" ? {kind: kind.value} : {}),
            ...(iters.value ? {n_iter: Number(iters.value)} : {}), ...(cv.value ? {cv: Number(cv.value)} : {})};
        } else delete c.search;
        draft.commit();
      };
      for (const n of [space, kind, iters, cv]) n.addEventListener("change", apply);
      box.append(el("div", {class: "bl-item"}, el("b", {class: "small", text: `${shortClass(c.class)} (candidate ${s.candidates.indexOf(c) + 1})`}),
        field("One parameter per line", space),
        el("div", {class: "form-grid"}, field("Kind", kind), field("Tries (n_iter)", iters), field("Folds (cv)", cv))));
    }
    return box;
  }

  function pipelineForm(s) {
    const steps = s.pipeline || [];
    const box = el("div", {class: "stack"});
    if (s.candidates?.some((c) => c.class && !c.class.startsWith("sklearn.") && !c.class.startsWith("xgboost.") &&
      !c.class.startsWith("lightgbm.") && !c.class.startsWith("catboost.") && !c.class.startsWith("pyod."))) {
      box.append(el("p", {class: "hint", text: "A custom adapter class does its own preprocessing; it takes no pipeline steps."}));
    }
    let dragFrom = null;
    steps.forEach((step, i) => {
      const sel = catalogSelect("step", step.class);
      const custom = el("input", {class: `input mono${sel.value === "__custom" ? "" : " hidden"}`, value: step.class || "", "aria-label": "Step class path"});
      sel.addEventListener("change", () => {
        custom.classList.toggle("hidden", sel.value !== "__custom");
        if (sel.value !== "__custom") { step.class = sel.value; delete step.params; draft.commit(); }
      });
      custom.addEventListener("change", () => { step.class = custom.value.trim(); draft.commit(); });
      const moveBy = (k) => { const j = i + k; if (j < 0 || j >= steps.length) return; [steps[i], steps[j]] = [steps[j], steps[i]]; draft.commit(); };
      const item = el("div", {class: "bl-item bl-step", draggable: "true"},
        el("div", {class: "row"}, el("span", {class: "bl-grip", text: "::", title: "drag to reorder"}),
          el("b", {text: `${i + 1}`}), el("div", {class: "spacer"}, sel, custom),
          el("button", {class: "btn small", type: "button", text: "Up", disabled: i === 0, "aria-label": `move step ${i + 1} up`, onclick: () => moveBy(-1)}),
          el("button", {class: "btn small", type: "button", text: "Down", disabled: i === steps.length - 1, "aria-label": `move step ${i + 1} down`, onclick: () => moveBy(1)}),
          el("button", {class: "btn small danger", type: "button", text: "Remove", onclick: () => { steps.splice(i, 1); if (!steps.length) delete s.pipeline; draft.commit(); }})));
      if (step.class === "idslab.prep.Encode") {
        const cats = el("input", {class: "input mono", value: listText(step.params?.categorical || []), placeholder: "proto, service", "aria-label": "Categorical columns"});
        cats.addEventListener("change", () => {
          step.params = {...(step.params || {}), categorical: names(cats.value)};
          draft.commit();
        });
        const fill = draft.dataset?.suggest?.categorical;
        item.append(field("Categorical columns", el("div", {class: "row"}, cats,
          fill ? el("button", {class: "btn small", type: "button", text: "Use the text columns found", onclick: () => {
            step.params = {...(step.params || {}), categorical: [...fill]}; draft.commit();
          }}) : null)));
      } else {
        item.append(paramsForm("step", step));
      }
      item.addEventListener("dragstart", () => { dragFrom = i; });
      item.addEventListener("dragover", (e) => e.preventDefault());
      item.addEventListener("drop", (e) => {
        e.preventDefault();
        if (dragFrom == null || dragFrom === i) return;
        const [moved] = steps.splice(dragFrom, 1);
        steps.splice(i, 0, moved);
        draft.commit();
      });
      box.append(item);
    });
    box.append(el("div", {class: "row"}, el("button", {class: "btn small", type: "button", text: "Add step", onclick: () => {
      steps.push({class: "sklearn.preprocessing.StandardScaler"}); s.pipeline = steps; draft.commit();
    }})));
    return box;
  }

  function selectionForm(s) {
    return el("div", {class: "form-grid"},
      s.kind !== "detector" ? field("Calibrate scores", bound(draft, s, "calibrate", {options: [["", "none"], ["sigmoid", "sigmoid"], ["isotonic", "isotonic"]]})) : null,
      field("Choose candidates by", bound(draft, s, "select_by", {options: [["", "default for this kind"], ["auc", "AUC, then tune the decision"], ["objective", "the objective, jointly with the decision"]]})),
      field("Fewest VAL rows to tune on", bound(draft, s, "min_val_rows", {type: "number", placeholder: "20"}),
        "below this many VAL rows reaching the stage, the decision is not tuned"));
  }

  function typeForm(s) {
    return el("div", {class: "stack"},
      field("Type objective", bound(draft, s, "type_objective", {options: [["", "macro F1 (default)"], ["accuracy", "accuracy"], ["auc", "AUC"]]}),
        "how candidates of this type stage are compared on VAL"),
      el("p", {class: "hint", text: "The role (annotate or decide) is set with the routes above."}));
  }

  function detectorForm(s) {
    return el("div", {class: "stack"},
      el("div", {class: "form-grid"},
        field("Fit on", bound(draft, s, "fit_on", {options: [["", "every FIT row"], ["benign", "benign FIT rows"], ["pool", "each batch of flows (refit per batch)"]]})),
        s.fit_on === "pool" ? field("Preprocessing fit on", bound(draft, s, "prep_fit_on", {options: [["", "FIT rows"], ["benign", "benign FIT rows"]]})) : null,
        s.fit_on === "pool" ? field("Smallest batch scored", bound(draft, s, "min_pool", {type: "number", placeholder: "automatic"})) : null),
      el("p", {class: "hint", text: "Thresholds (budget, top fraction, grid) are set under Decision above."}));
  }

  function featuresForm(name, s) {
    const box = el("div", {class: "stack"});
    const feats = s.extra_features || [];
    box.append(field("Uses the scores of", el("div", {class: "row"}, feats.length ? feats.map((f) => el("span", {class: "badge amber"}, f,
      el("button", {class: "chip-x", type: "button", text: "x", "aria-label": `stop using ${f}'s score`, onclick: () => {
        s.extra_features = feats.filter((z) => z !== f);
        if (!s.extra_features.length) delete s.extra_features;
        draft.commit();
      }}))) : el("span", {class: "muted small", text: "none"}))));
    const others = Object.keys(draft.raw.stages).filter((n) => n !== name && !feats.includes(n));
    if (others.length) {
      const add = el("select", {class: "select", "aria-label": "Add a score feature"}, el("option", {value: "", text: "Add the score of..."}),
        others.map((n) => el("option", {value: n, text: n})));
      add.addEventListener("change", () => { if (add.value) { s.extra_features = [...feats, add.value]; draft.commit(); } });
      box.append(add);
    }
    if (s.kind !== "detector") {
      const trainOn = Array.isArray(s.train_on)
        ? field("Train on these types", bound(draft, s, "train_on", {parse: names}), "a list of type names")
        : field("Train on", bound(draft, s, "train_on", {options: [["", "every row"], ...(s.kind === "multiclass" ? [["attack", "attack rows"]] : []),
          ["hard", "hard rows of another stage"]],
        onSet: () => {
          if (s.train_on === "hard") s.hard = s.hard || {source: Object.keys(draft.raw.stages).find((n) => n !== name), band: [0.3, 0.7]};
          else delete s.hard;
        }}));
      box.append(trainOn);
      if (s.train_on === "hard" && s.hard) {
        box.append(el("div", {class: "form-grid"},
          field("Hard rows from", bound(draft, s.hard, "source", {options: Object.keys(draft.raw.stages).filter((n) => n !== name)})),
          field("Score band", bound(draft, s.hard, "band", {parse: (v) => parseList(v).slice(0, 2), placeholder: "0.3, 0.7"}))));
      }
    }
    return box;
  }

  function costForm(s) {
    return field("Cost per flow reaching this stage", bound(draft, s, "cost", {type: "number", placeholder: "1"}),
      "used in the report's stage cost section only; it does not change any choice");
  }

  if (draft.raw) { renderIssues(); renderCanvas(); renderInspector(); } else renderUnavailable();
  return {destroy() { unsub(); removeEventListener("keydown", onKey); }};
}
