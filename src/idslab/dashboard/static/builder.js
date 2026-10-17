// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Visual pipeline builder. The design is edited as config data; the server writes it as YAML and
// validates it with the CLI's loader after every change, so the YAML stays the source of truth.

import {el, svg, api, q, enc, card, cardBody, pageHead, note, toast, field, badge, checkbox} from "./core.js";
import {commandForm} from "./editor.js";

const TERMS = ["ATTACK", "BENIGN"];
const OUTPUTS = {classifier: ["attack", "benign"], detector: ["anomalous", "normal"]};
const DECISIONS = {
  classifier: ["threshold", "grid"],
  detector: ["threshold", "grid", "budget", "top_fraction"],
  multiclass: ["confidence"],
};
const KIND_LABEL = {classifier: "classifier", detector: "detector", multiclass: "type stage"};
const NW = 196, PORT = 20, COLW = 252, ROWH = 150;

const BLANK = {
  name: "my-cascade",
  dataset: {train: "data/train.parquet", test: "data/test.parquet", label: "label", type: "attack_cat",
    normal_type: "Normal"},
  protocol: {seeds: [0, 1, 2], objective: "accuracy"},
  entry: "",
  stages: {},
};

// ---------- small helpers ----------

const clone = (v) => JSON.parse(JSON.stringify(v));
const dirOf = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
function relpath(fromDir, to) {  // both relative to the workspace
  const a = fromDir ? fromDir.split("/") : [], b = to.split("/");
  let i = 0;
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  return [...Array(a.length - i).fill(".."), ...b.slice(i)].join("/");
}
function parseList(text) {
  return String(text).split(/[,\s]+/).filter(Boolean).map((x) => {
    if (x === "null" || x === "None") return null;
    if (x === "true") return true;
    if (x === "false") return false;
    const n = Number(x);
    return Number.isFinite(n) ? n : x;
  });
}
const listText = (v) => (Array.isArray(v) ? v : v == null ? [] : [v]).map((x) => (x === null ? "null" : String(x))).join(", ");
const shortClass = (c) => String(c || "").split(".").pop().replace("Classifier", "");

function outputsOf(st) {
  if (st.kind === "multiclass") {
    if ((st.role || "annotate") === "annotate") return ["next"];
    return Object.keys(st.routes || {}).length ? Object.keys(st.routes) : ["default"];
  }
  return OUTPUTS[st.kind] || [];
}
function targetOf(st, out) {
  if (out === "next") return st.next;
  return (st.routes || {})[out];
}
function setTarget(st, out, target) {
  if (out === "next") st.next = target;
  else st.routes = {...(st.routes || {}), [out]: target};
}

// ---------- the page ----------

export async function builderView(view, meta, path, setCrumbs) {
  const B = {
    path: path || null, raw: null, positions: {}, selected: null, tab: "stage",
    yaml: "", savedYaml: null, layoutDirty: false, validation: null, catalog: null, datafiles: [],
    dataset: null, errStages: new Set(),
  };
  setCrumbs(path ? [["Configs", "#/configs"], [path, `#/config/${enc(path)}`], ["Builder"]] : [["Builder"]]);
  const [catalog, datafiles] = await Promise.all([api("/api/catalog"), api("/api/datafiles")]);
  B.catalog = catalog;
  B.datafiles = datafiles;
  if (path) {
    const {yaml} = await api("/api/config" + q({path}));
    const parsed = await api("/api/parse", {yaml});
    if (!parsed.ok) {
      view.append(pageHead("Builder", null), note(`This file is not valid YAML, so it cannot be drawn: ${parsed.error}. ` +
        "Fix it in the YAML editor first.", "bad"), el("a", {class: "btn", href: `#/config/${enc(path)}`, text: "Open in the YAML editor"}));
      return null;
    }
    B.raw = parsed.config;
    B.savedYaml = null;  // set by the first dump, so an untouched file is not "unsaved"
    B.positions = (await api("/api/layout" + q({path}))).positions || {};
  } else {
    B.raw = clone(BLANK);
    addStage(B, "classifier");
  }
  B.raw.stages = B.raw.stages || {};
  B.selected = B.raw.entry || Object.keys(B.raw.stages)[0] || "dataset";
  await readData(B).catch(() => {});  // suggestions for the dataset panel; a missing file is fine
  if (!path && B.dataset) {  // a new design: encode the text columns the data has
    for (const st of Object.values(B.raw.stages)) {
      const steps = encodeStep(B);
      if (steps.length) st.pipeline = steps; else delete st.pipeline;
    }
  }

  // layout
  const status = el("div", {class: "stack"});
  const canvasWrap = el("div", {class: "bl-canvas"});
  const inspector = el("div", {class: "stack"});
  const tabs = el("div", {class: "seg"});
  const dirtyBadge = badge("Unsaved", "amber");
  const saveBtn = el("button", {class: "btn primary", type: "button", text: "Save"});
  const head = pageHead(B.path || "New design", null, [
    B.path ? el("a", {class: "btn", href: `#/config/${enc(B.path)}`, text: "YAML editor"}) : null,
    el("button", {class: "btn", type: "button", text: "Save as", onclick: () => save(true)}),
    saveBtn,
  ], [badge("Builder", "blue"), dirtyBadge]);
  head.querySelector("h2").classList.add("mono");
  saveBtn.addEventListener("click", () => save(!B.path));

  const palette = el("div", {class: "bl-palette"},
    el("div", {class: "section-title", text: "Add a stage"}),
    ["classifier", "detector", "multiclass"].map((k) => el("div", {
      class: `bl-chip ${k}`, draggable: "true", title: "Drag onto the canvas, or click",
      ondragstart: (e) => e.dataTransfer.setData("text/x-idslab-kind", k),
      onclick: () => { const n = addStage(B, k); B.selected = n; B.tab = "stage"; changed(); },
    }, el("b", {text: KIND_LABEL[k]}), el("small", {text: {
      classifier: "attack or benign", detector: "anomalous or normal", multiclass: "names the attack type",
    }[k]}))),
    el("div", {class: "section-title", text: "Design"}),
    ["dataset", "protocol", "controls"].map((k) => el("button", {class: "btn small bl-side", type: "button",
      text: k[0].toUpperCase() + k.slice(1), onclick: () => { B.selected = k; B.tab = "stage"; renderInspector(); }})),
    el("div", {class: "section-title", text: "How to"}),
    el("ul", {class: "bl-help"},
      el("li", {text: "Drag a port (the dots on a stage's right edge) onto a stage or a terminal to route that output."}),
      el("li", {text: "Drag the orange dot under a stage onto a later stage to use its score as a feature."}),
      el("li", {text: "Drag stages to arrange them; the layout is saved next to the config."})),
    status);

  view.append(head, el("div", {class: "bl-grid"}, palette, canvasWrap,
    el("div", {class: "bl-side-panel"}, tabs, inspector)));

  // ---------- sync with the server ----------

  let timer = null, seq = 0;
  function changed() {
    renderCanvas();
    renderInspector();
    clearTimeout(timer);
    timer = setTimeout(sync, 250);
  }
  async function sync() {
    const mine = ++seq;
    let r;
    try { r = await api("/api/dump", {config: ordered(B.raw), path: B.path}); }
    catch (e) { r = {ok: false, error: e.message, yaml: B.yaml}; }
    if (mine !== seq) return;
    B.yaml = r.yaml;
    if (B.savedYaml === null && B.path) B.savedYaml = r.yaml;
    B.validation = r;
    B.errStages = new Set();
    for (const line of String(r.error || "").split("\n")) {
      const m = /^stage (\S+?)(?=[: ])/.exec(line);
      if (m) B.errStages.add(m[1]);
    }
    renderStatus();
    renderCanvas();
    if (B.tab === "yaml") renderInspector();
  }
  const isDirty = () => B.layoutDirty || B.yaml !== B.savedYaml;
  function renderStatus() {
    dirtyBadge.classList.toggle("hidden", !isDirty());
    const v = B.validation;
    status.replaceChildren(el("div", {class: "section-title", text: "Validation"}));
    if (!v) return;
    if (v.ok) {
      status.append(note(`Valid. Selection order: ${v.config.order.join(" > ")}.`, "good"));
      const missing = (v.config.data || []).filter((d) => !d.exists);
      if (missing.length) status.append(note(`Data not found: ${missing.map((d) => d.path).join(", ")}`, "warn"));
      return;
    }
    const lines = String(v.error).split("\n").slice(0, 12);
    status.append(el("div", {class: "note bad bl-errors"}, lines.map((line) => {
      const m = /^stage (\S+?)(?=[: ])/.exec(line);
      return el("div", {class: m ? "bl-err-link" : null, text: line,
        onclick: m && B.raw.stages[m[1]] ? () => { B.selected = m[1]; B.tab = "stage"; renderCanvas(); renderInspector(); } : null});
    })));
  }

  async function save(asNew) {
    let target = B.path;
    if (asNew || !target) {
      target = prompt("Save the design as (relative to the workspace, .yaml)", B.path || `${B.raw.name || "design"}.yaml`);
      if (!target) return;
    }
    clearTimeout(timer);
    await sync();
    try {
      const r = await api("/api/config/save", {path: target, yaml: B.yaml, overwrite: !asNew && target === B.path});
      await api("/api/layout", {path: r.path, positions: B.positions});
      B.savedYaml = B.yaml;
      B.layoutDirty = false;
      toast(`Saved ${r.path}`);
      if (r.path !== B.path) { location.hash = `#/builder/${enc(r.path)}`; return; }
      renderStatus();
    } catch (e) { toast(e.message); }
  }

  // ---------- canvas ----------

  function layout() {
    const st = B.raw.stages, names = Object.keys(st);
    const col = {};
    if (st[B.raw.entry]) col[B.raw.entry] = 0;
    for (let pass = 0; pass < names.length; pass++) {
      for (const n of names) {
        if (col[n] == null) continue;
        for (const o of outputsOf(st[n])) {
          const t = targetOf(st[n], o);
          if (st[t] && (col[t] == null || col[t] < col[n] + 1)) col[t] = col[n] + 1;
        }
      }
    }
    const rows = {};
    const pos = {};
    for (const n of names) {
      const c = col[n] ?? 0;
      rows[c] = (rows[c] || 0) + 1;
      pos[n] = B.positions[n] || [40 + c * COLW, 40 + (rows[c] - 1) * ROWH];
    }
    return pos;
  }
  const nodeH = (s) => Math.max(84, 50 + outputsOf(s).length * PORT);

  function renderCanvas() {
    const st = B.raw.stages;
    const pos = layout();
    const xs = Object.values(pos).map((p) => p[0] + NW), ys = Object.values(pos).map((p) => p[1]);
    const termX = Math.max(300, ...xs) + 70;
    const termPos = {ATTACK: [termX, 40], BENIGN: [termX, Math.max(220, ...ys.map((y) => y + 60))]};
    const W = termX + 140, H = Math.max(420, ...ys.map((y) => y + 200), termPos.BENIGN[1] + 120);
    const root = svg("svg", {class: "bl-svg", viewBox: `0 0 ${W} ${H}`, style: {width: "100%", minWidth: `${Math.round(W * 0.75)}px`}},
      svg("defs", {}, svg("marker", {id: "bl-arr", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto"},
        svg("path", {d: "M0,0 L8,4 L0,8 z", class: "bl-arrow"})),
      svg("marker", {id: "bl-arr-feed", viewBox: "0 0 8 8", refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: "auto"},
        svg("path", {d: "M0,0 L8,4 L0,8 z", class: "bl-arrow feed"}))));
    const edges = svg("g", {}), nodes = svg("g", {});
    root.append(edges, nodes);
    const inPoint = (t) => (st[t] ? [pos[t][0], pos[t][1] + 24] : termPos[t] ? [termPos[t][0], termPos[t][1] + 20] : null);

    for (const [name, s] of Object.entries(st)) {
      const [x, y] = pos[name];
      outputsOf(s).forEach((o, i) => {
        const t = targetOf(s, o), b = inPoint(t);
        if (!b) return;
        const a = [x + NW, y + 50 + i * PORT];
        const mx = Math.max(a[0] + 30, (a[0] + b[0]) / 2);
        edges.append(svg("path", {class: "bl-edge", d: `M${a[0]},${a[1]} C${mx},${a[1]} ${mx},${b[1]} ${b[0] - 3},${b[1]}`,
          "marker-end": "url(#bl-arr)"}));
      });
      for (const src of s.extra_features || []) {
        if (!pos[src]) continue;
        const a = [pos[src][0] + NW / 2, pos[src][1] + nodeH(st[src])], b = [x + NW / 2, y];
        const dip = Math.max(a[1], b[1]) + 40;
        const g = svg("path", {class: "bl-edge feed", d: `M${a[0]},${a[1]} C${a[0]},${dip} ${b[0]},${b[1] - 60} ${b[0]},${b[1] - 3}`,
          "marker-end": "url(#bl-arr-feed)"});
        g.append(svg("title", {text: `${src} score feeds ${name}. Click to remove.`}));
        g.addEventListener("click", () => {
          s.extra_features = s.extra_features.filter((x) => x !== src);
          if (!s.extra_features.length) delete s.extra_features;
          changed();
        });
        edges.append(g);
      }
    }
    for (const t of TERMS) {
      const [x, y] = termPos[t];
      nodes.append(svg("g", {class: `bl-term ${t.toLowerCase()}`, "data-target": t, transform: `translate(${x},${y})`},
        svg("rect", {width: 120, height: 40, rx: 20}),
        svg("text", {x: 60, y: 25, "text-anchor": "middle", text: t})));
    }
    for (const [name, s] of Object.entries(st)) {
      const [x, y] = pos[name];
      const h = nodeH(s);
      const cls = ["bl-node", s.kind, name === B.selected ? "sel" : "", name === B.raw.entry ? "entry" : "",
        B.errStages.has(name) ? "err" : ""].join(" ");
      const g = svg("g", {class: cls, "data-target": name, transform: `translate(${x},${y})`});
      g.append(svg("rect", {class: "body", width: NW, height: h, rx: 10}),
        svg("rect", {class: "stripe", x: 0, y: 10, width: 4, height: h - 20, rx: 2}),
        svg("text", {class: "t", x: 14, y: 22, text: name + (name === B.raw.entry ? "  (entry)" : "")}),
        svg("text", {class: "m", x: 14, y: 38, text: `${KIND_LABEL[s.kind] || s.kind}: ${(s.candidates || []).map((c) => c.external ? "external" : shortClass(c.class)).join(", ").slice(0, 26)}`}));
      outputsOf(s).forEach((o, i) => {
        const py = 50 + i * PORT;
        const linked = inPoint(targetOf(s, o));
        g.append(svg("text", {class: "port-label", x: NW - 14, y: py + 4, "text-anchor": "end", text: o}));
        const port = svg("circle", {class: `port${linked ? "" : " open"}`, cx: NW, cy: py, r: 6});
        port.append(svg("title", {text: `${o}: drag to a stage or terminal`}));
        port.addEventListener("pointerdown", (e) => startWire(e, root, [x + NW, y + py], (t) => {
          if (t === name) return;
          setTarget(s, o, t);
          changed();
        }));
        g.append(port);
      });
      if (s.kind !== "detector" || s.fit_on !== "pool") {
        const sp = svg("rect", {class: "score-port", x: NW / 2 - 6, y: h - 6, width: 12, height: 12, rx: 2, transform: `rotate(45 ${NW / 2} ${h})`});
        sp.append(svg("title", {text: "score: drag to a later stage to use it as a feature"}));
        sp.addEventListener("pointerdown", (e) => startWire(e, root, [x + NW / 2, y + h], (t) => {
          const target = st[t];
          if (!target || t === name) return;
          target.extra_features = [...new Set([...(target.extra_features || []), name])];
          changed();
        }, true));
        g.append(sp);
      }
      g.addEventListener("pointerdown", (e) => {
        if (e.target.closest(".port, .score-port")) return;
        startMove(e, root, name, pos);
      });
      nodes.append(g);
    }
    root.addEventListener("dragover", (e) => e.preventDefault());
    root.addEventListener("drop", (e) => {
      const kind = e.dataTransfer.getData("text/x-idslab-kind");
      if (!kind) return;
      e.preventDefault();
      const p = toSvg(root, e);
      const n = addStage(B, kind);
      B.positions[n] = [Math.max(10, p.x - NW / 2), Math.max(10, p.y - 30)];
      B.layoutDirty = true;
      B.selected = n;
      B.tab = "stage";
      changed();
    });
    canvasWrap.replaceChildren(root);
  }

  function toSvg(root, e) {  // client coordinates to drawing coordinates (the drawing is scaled to fit)
    const pt = root.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const r = pt.matrixTransform(root.getScreenCTM().inverse());
    return {x: r.x, y: r.y};
  }
  function startWire(e, root, from, onDrop, feed = false) {
    e.preventDefault();
    e.stopPropagation();
    const line = svg("path", {class: `bl-edge temp${feed ? " feed" : ""}`});
    root.append(line);
    const move = (ev) => {
      const p = toSvg(root, ev);
      line.setAttribute("d", `M${from[0]},${from[1]} L${p.x},${p.y}`);
    };
    const up = (ev) => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      line.remove();
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
        B.positions = {...layout(), [name]: [Math.max(0, origin[0] + p.x - start.x), Math.max(0, origin[1] + p.y - start.y)]};
        B.layoutDirty = true;
        renderStatus();
      }
      B.selected = name;
      B.tab = "stage";
      renderCanvas();
      renderInspector();
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
  }

  // ---------- inspector ----------

  function renderInspector() {
    tabs.replaceChildren(...[["stage", B.raw.stages[B.selected] ? "Stage" : "Settings"], ["yaml", "YAML"], ["run", "Run"]].map(([k, label]) =>
      el("button", {type: "button", class: B.tab === k ? "active" : null, text: label,
        onclick: () => { B.tab = k; renderInspector(); }})));
    inspector.replaceChildren();
    if (B.tab === "yaml") {
      inspector.append(card("Generated YAML", "Written by the server with the CLI's rules; this is what Save writes.",
        cardBody(el("pre", {class: "code bl-yaml", text: B.yaml || "(waiting for the server)"}))));
      return;
    }
    if (B.tab === "run") {
      if (!B.path) { inspector.append(note("Save the design first: commands read the file on disk.", "warn")); return; }
      const seg = el("div", {class: "seg"}), panel = el("div");
      const show = (k) => {
        for (const b of seg.children) b.classList.toggle("active", b.dataset.k === k);
        panel.replaceChildren(commandForm(k, meta, {configPath: B.path, isDirty}));
      };
      for (const [k, label] of [["run", "Run"], ["report", "Report"], ["export-splits", "Export splits"]]) {
        seg.append(el("button", {type: "button", "data-k": k, text: label, onclick: () => show(k)}));
      }
      show("run");
      inspector.append(card("Commands", null, cardBody(el("div", {class: "stack"}, seg, panel))));
      return;
    }
    if (B.selected === "dataset") return inspector.append(datasetPanel());
    if (B.selected === "protocol") return inspector.append(protocolPanel());
    if (B.selected === "controls") return inspector.append(controlsPanel());
    const s = B.raw.stages[B.selected];
    if (!s) { inspector.append(note("Select a stage on the canvas, or open Dataset or Protocol.", "info")); return; }
    inspector.append(stagePanel(B.selected, s));
  }

  // a form field bound to obj[key]; empty input removes the key
  function bound(obj, key, {type = "text", options = null, placeholder = "", parse = null, onSet = null} = {}) {
    let input;
    if (options) {
      input = el("select", {class: "select"}, options.map((o) => {
        const [v, label] = Array.isArray(o) ? o : [o, o];
        return el("option", {value: v, text: label});
      }));
      input.value = obj[key] == null ? "" : String(obj[key]);
    } else {
      input = el("input", {class: "input", type, placeholder, value: obj[key] == null ? "" : (Array.isArray(obj[key]) ? listText(obj[key]) : String(obj[key]))});
    }
    input.addEventListener("change", () => {
      const raw = input.value.trim();
      if (raw === "") delete obj[key];
      else obj[key] = parse ? parse(raw) : type === "number" ? Number(raw) : raw;
      onSet?.();
      changed();
    });
    return input;
  }

  function stagePanel(name, s) {
    const nameInput = el("input", {class: "input mono", value: name});
    nameInput.addEventListener("change", () => {
      const nn = nameInput.value.trim();
      if (!nn || nn === name) return;
      if (B.raw.stages[nn] || TERMS.includes(nn)) { toast(`${nn} is taken`); nameInput.value = name; return; }
      renameStage(B, name, nn);
      changed();
    });
    const entry = checkbox("Entry stage", "every flow starts here", B.raw.entry === name);
    entry.input.addEventListener("change", () => { if (entry.input.checked) { B.raw.entry = name; changed(); } });
    const kind = el("select", {class: "select"}, Object.keys(KIND_LABEL).map((k) => el("option", {value: k, text: KIND_LABEL[k]})));
    kind.value = s.kind;
    kind.addEventListener("change", () => {
      Object.assign(s, stageDefaults(kind.value, s));
      for (const k of Object.keys(s)) if (!(k in stageDefaults(kind.value, s))) delete s[k];
      changed();
    });
    const remove = el("button", {class: "btn danger small", type: "button", text: "Remove stage", onclick: () => {
      if (!confirm(`Remove stage ${name}? Routes into it stay unconnected until you redraw them.`)) return;
      removeStage(B, name);
      B.selected = B.raw.entry || "dataset";
      changed();
    }});

    const parts = [
      card(name, KIND_LABEL[s.kind], cardBody(el("div", {class: "stack"},
        el("div", {class: "form-grid"}, field("Name", nameInput), field("Kind", kind)),
        el("div", {class: "row"}, entry.node, el("span", {class: "spacer"}), remove)))),
      card("Routes", "Or drag a port on the canvas.", cardBody(routesForm(name, s))),
      card("Decision", "How the score becomes an output.", cardBody(decisionForm(s))),
      card("Candidates", "Models to choose from on VAL.", cardBody(candidatesForm(s))),
      card("Pipeline", "Steps fitted on this stage's training rows, in order.", cardBody(pipelineForm(s))),
      card("Training", null, cardBody(trainingForm(name, s))),
    ];
    return el("div", {class: "stack"}, parts);
  }

  function targetsFor(name) {
    return [...TERMS, ...Object.keys(B.raw.stages).filter((n) => n !== name)];
  }
  function routesForm(name, s) {
    const box = el("div", {class: "stack"});
    if (s.kind === "multiclass") {
      const role = el("select", {class: "select"}, [["annotate", "annotate: names the type, verdict unchanged"], ["decide", "decide: routes by type"]]
        .map(([v, t]) => el("option", {value: v, text: t})));
      role.value = s.role || "annotate";
      role.addEventListener("change", () => {
        s.role = role.value;
        if (s.role === "annotate") { delete s.routes; s.next = s.next || "ATTACK"; delete s.decision?.off; }
        else { delete s.next; s.routes = s.routes || {default: "ATTACK"}; s.decision = {...(s.decision || {}), off: "default"}; }
        changed();
      });
      box.append(field("Role", role));
    }
    for (const o of outputsOf(s)) {
      const sel = el("select", {class: "select"}, el("option", {value: "", text: "(not connected)"}),
        targetsFor(name).map((t) => el("option", {value: t, text: t})));
      sel.value = targetOf(s, o) || "";
      sel.addEventListener("change", () => { setTarget(s, o, sel.value || undefined); if (!sel.value) { if (o === "next") delete s.next; else delete s.routes[o]; } changed(); });
      const row = el("div", {class: "bl-route"}, el("span", {class: "mono", text: o}), sel);
      if (s.kind === "multiclass" && s.role === "decide" && o !== "default") {
        row.append(el("button", {class: "btn small", type: "button", text: "x", title: "remove this route",
          onclick: () => { delete s.routes[o]; changed(); }}));
      }
      box.append(row);
    }
    if (s.kind === "multiclass" && s.role === "decide") {
      const types = B.dataset?.typeValues || [];
      const add = el("input", {class: "input mono", placeholder: "type name or uncertain", list: "bl-types"});
      box.append(el("datalist", {id: "bl-types"}, [...types, "uncertain"].map((t) => el("option", {value: t}))),
        el("div", {class: "row"}, add, el("button", {class: "btn small", type: "button", text: "Add route", onclick: () => {
          const t = add.value.trim();
          if (!t) return;
          s.routes = {...s.routes, [t]: "ATTACK"};
          changed();
        }})));
    }
    return box;
  }

  function decisionForm(s) {
    s.decision = s.decision || {};
    const d = s.decision;
    const modes = DECISIONS[s.kind] || [];
    const current = modes.find((m) => m in d) || modes[0];
    const mode = el("select", {class: "select"}, modes.map((m) => el("option", {value: m, text: m.replace("_", " ")})));
    mode.value = current;
    const values = el("input", {class: "input mono", value: listText(d[current])});
    const hint = {
      threshold: "one number; a score at or above it takes attack (or anomalous)", grid: "list chosen on VAL, with off",
      budget: "share of benign VAL flows to flag; a list is chosen on VAL", top_fraction: "share of reaching flows to flag",
      confidence: "below this top probability the flow is uncertain",
    };
    const hintEl = el("div", {class: "muted small", text: hint[current]});
    const apply = () => {
      const m = mode.value, vals = parseList(values.value).filter((x) => typeof x === "number");
      for (const k of modes) delete d[k];
      d[m] = m === "threshold" ? (vals[0] ?? 0.5) : m === "grid" ? vals : vals.length === 1 ? vals[0] : vals;
      hintEl.textContent = hint[m];
      changed();
    };
    mode.addEventListener("change", apply);
    values.addEventListener("change", apply);
    const parts = [el("div", {class: "form-grid"}, field("Mode", mode), field("Values", values)), hintEl];
    if (!(s.kind === "multiclass" && (s.role || "annotate") === "annotate")) {
      parts.push(field("When off, every flow takes", bound(d, "off", {options: [["", "(choose)"], ...outputsOf(s)]})));
    }
    return el("div", {class: "stack"}, parts);
  }

  function catalogSelect(section, value) {
    const entries = B.catalog[section] || [];
    const sel = el("select", {class: "select"},
      el("optgroup", {label: "Installed"}, entries.filter((e) => e.installed).map((e) => el("option", {value: e.class, text: e.label}))),
      el("optgroup", {label: "Needs an extra"}, entries.filter((e) => !e.installed).map((e) =>
        el("option", {value: e.class, text: `${e.label} (pip install idslab[${e.extra}])`}))),
      el("option", {value: "__custom", text: "Other class path..."}));
    sel.value = entries.some((e) => e.class === value) ? value : "__custom";
    return sel;
  }
  function paramsForm(section, obj) {
    const entry = (B.catalog[section] || []).find((e) => e.class === obj.class);
    const box = el("div", {class: "form-grid bl-params"});
    for (const p of entry?.params || []) {
      let input;
      const cur = obj.params?.[p.name];
      if (p.type === "bool") {
        input = el("select", {class: "select"}, [["", `default (${p.default})`], ["true", "true"], ["false", "false"]].map(([v, t]) => el("option", {value: v, text: t})));
        input.value = cur == null ? "" : String(cur);
      } else {
        input = el("input", {class: "input mono", placeholder: p.default == null ? "default" : listText(p.default),
          value: cur == null ? "" : Array.isArray(cur) ? listText(cur) : String(cur)});
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
        changed();
      });
      box.append(field(p.name, input));
    }
    if (!entry) box.append(el("div", {class: "hint", text: "Set parameters for this class in the YAML tab or editor."}));
    return el("details", {class: "trace"}, el("summary", {text: `Parameters${obj.params ? ` (${Object.keys(obj.params).length} set)` : ""}`}), box);
  }

  function candidatesForm(s) {
    s.candidates = s.candidates || [];
    const section = s.kind === "detector" ? "detector" : "classifier";
    const box = el("div", {class: "stack"});
    s.candidates.forEach((c, i) => {
      const row = el("div", {class: "bl-item"});
      if (c.external) {
        const p = el("input", {class: "input mono", value: typeof c.external === "string" ? c.external : c.external.path || ""});
        p.addEventListener("change", () => { c.external = {path: p.value.trim()}; changed(); });
        row.append(field("Score file (may use {seed} and {family})", p));
      } else {
        const sel = catalogSelect(section, c.class);
        const custom = el("input", {class: `input mono${sel.value === "__custom" ? "" : " hidden"}`, value: c.class || "", placeholder: "package.module.Class"});
        sel.addEventListener("change", () => {
          custom.classList.toggle("hidden", sel.value !== "__custom");
          if (sel.value !== "__custom") { c.class = sel.value; delete c.params; delete c.search; changed(); }
        });
        custom.addEventListener("change", () => { c.class = custom.value.trim(); delete c.params; changed(); });
        row.append(field(`Candidate ${i + 1}`, el("div", {class: "stack"}, sel, custom)), paramsForm(section, c));
        if (s.kind !== "detector") {
          const space = el("textarea", {class: "textarea mono", rows: 2, placeholder: "max_depth: 8, 16, null\nmin_samples_leaf: 1, 5",
            value: Object.entries(c.search?.space || {}).map(([k, v]) => `${k}: ${listText(v)}`).join("\n")});
          const iters = el("input", {class: "input", type: "number", min: "1", value: c.search?.n_iter ?? "", placeholder: "10"});
          const apply = () => {
            const sp = {};
            for (const line of space.value.split("\n")) {
              const m = /^\s*([\w.]+)\s*:\s*(.+)$/.exec(line);
              if (m) sp[m[1]] = parseList(m[2]);
            }
            if (Object.keys(sp).length) c.search = {...(c.search || {}), space: sp, ...(iters.value ? {n_iter: Number(iters.value)} : {})};
            else delete c.search;
            changed();
          };
          space.addEventListener("change", apply);
          iters.addEventListener("change", apply);
          row.append(el("details", {class: "trace"}, el("summary", {text: c.search ? "Search space (set)" : "Search space"}),
            el("div", {class: "stack"}, field("One parameter per line", space), field("Tries (n_iter)", iters))));
        }
      }
      row.append(el("div", {class: "row"}, el("span", {class: "spacer"}),
        el("button", {class: "btn small danger", type: "button", text: "Remove", onclick: () => { s.candidates.splice(i, 1); changed(); }})));
      box.append(row);
    });
    box.append(el("div", {class: "row"},
      el("button", {class: "btn small", type: "button", text: "Add model", onclick: () => {
        s.candidates.push({class: (B.catalog[section] || []).find((e) => e.installed)?.class}); changed();
      }}),
      el("button", {class: "btn small", type: "button", text: "Add external score file", onclick: () => {
        s.candidates.push({external: {path: `scores/${B.selected}_{seed}.parquet`}}); changed();
      }})));
    return box;
  }

  function pipelineForm(s) {
    const steps = s.pipeline || [];
    const box = el("div", {class: "stack"});
    let dragFrom = null;
    steps.forEach((step, i) => {
      const sel = catalogSelect("step", step.class);
      const custom = el("input", {class: `input mono${sel.value === "__custom" ? "" : " hidden"}`, value: step.class || ""});
      sel.addEventListener("change", () => {
        custom.classList.toggle("hidden", sel.value !== "__custom");
        if (sel.value !== "__custom") { step.class = sel.value; delete step.params; changed(); }
      });
      custom.addEventListener("change", () => { step.class = custom.value.trim(); changed(); });
      const item = el("div", {class: "bl-item bl-step", draggable: "true"},
        el("div", {class: "row"}, el("span", {class: "bl-grip", text: "::", title: "drag to reorder"}),
          el("b", {text: `${i + 1}`}), el("div", {class: "spacer"}, sel, custom),
          el("button", {class: "btn small danger", type: "button", text: "Remove", onclick: () => { steps.splice(i, 1); if (!steps.length) delete s.pipeline; changed(); }})));
      if (step.class === "idslab.prep.Encode") {
        const cats = el("input", {class: "input mono", value: listText(step.params?.categorical || []), placeholder: "proto, service"});
        cats.addEventListener("change", () => {
          step.params = {...(step.params || {}), categorical: cats.value.split(/[,\s]+/).filter(Boolean)};
          changed();
        });
        const fill = B.dataset?.suggest?.categorical;
        item.append(field("Categorical columns", el("div", {class: "row"}, cats,
          fill ? el("button", {class: "btn small", type: "button", text: "From data", onclick: () => { step.params = {...(step.params || {}), categorical: [...fill]}; changed(); }}) : null)));
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
        changed();
      });
      box.append(item);
    });
    box.append(el("div", {class: "row"}, el("button", {class: "btn small", type: "button", text: "Add step", onclick: () => {
      steps.push({class: "sklearn.preprocessing.StandardScaler"}); s.pipeline = steps; changed();
    }})));
    return box;
  }

  function trainingForm(name, s) {
    const box = el("div", {class: "stack"});
    if (s.kind === "detector") {
      box.append(el("div", {class: "form-grid"},
        field("Fit on", bound(s, "fit_on", {options: [["", "fit (all FIT rows)"], ["benign", "benign FIT rows"], ["pool", "each batch (refit per batch)"]]})),
        s.fit_on === "pool" ? field("Preprocessing fit on", bound(s, "prep_fit_on", {options: [["", "fit"], ["benign", "benign"]]})) : null,
        s.fit_on === "pool" ? field("Smallest batch scored", bound(s, "min_pool", {type: "number", placeholder: "auto"})) : null));
    } else {
      box.append(el("div", {class: "form-grid"},
        field("Calibrate", bound(s, "calibrate", {options: [["", "none"], ["sigmoid", "sigmoid"], ["isotonic", "isotonic"]]})),
        field("Train on", bound(s, "train_on", {options: [["", "all rows"], ...(s.kind === "multiclass" ? [["attack", "attack rows"]] : []), ["hard", "hard rows of another stage"]],
          onSet: () => { if (s.train_on === "hard") s.hard = s.hard || {source: Object.keys(B.raw.stages).find((n) => n !== name), band: [0.3, 0.7]}; else delete s.hard; }}))));
      if (s.train_on === "hard" && s.hard) {
        box.append(el("div", {class: "form-grid"},
          field("Hard rows from", bound(s.hard, "source", {options: Object.keys(B.raw.stages).filter((n) => n !== name)})),
          field("Score band", bound(s.hard, "band", {parse: (v) => parseList(v).slice(0, 2), placeholder: "0.3, 0.7"}))));
      }
      if (s.kind === "multiclass") {
        box.append(field("Type objective", bound(s, "type_objective", {options: [["", "macro_f1"], ["accuracy", "accuracy"], ["auc", "auc"]]})));
      }
    }
    const feats = (s.extra_features || []);
    box.append(field("Uses scores of", el("div", {class: "row"}, feats.length ? feats.map((f) => badge(f, "amber")) : el("span", {class: "muted small", text: "none (drag a score port onto this stage)"}))));
    box.append(el("details", {class: "trace"}, el("summary", {text: "Selection"}), el("div", {class: "form-grid"},
      field("Choose candidates by", bound(s, "select_by", {options: [["", "default"], ["auc", "AUC, then tune"], ["objective", "objective, jointly"]]})),
      field("Fewest VAL rows", bound(s, "min_val_rows", {type: "number", placeholder: "20"})),
      field("Declared cost per flow", bound(s, "cost", {type: "number", placeholder: "1 (reports only)"})))));
    return box;
  }

  function datasetPanel() {
    const d = B.raw.dataset = B.raw.dataset || {};
    const base = B.path ? dirOf(B.path) : "";
    const fileSel = (key) => {
      const sel = el("select", {class: "select"}, el("option", {value: "", text: "(none)"}),
        B.datafiles.map((f) => el("option", {value: relpath(base, f), text: f})));
      sel.value = d[key] || "";
      if (d[key] && sel.value !== d[key]) sel.append(el("option", {value: d[key], text: d[key]})), sel.value = d[key];
      sel.addEventListener("change", () => { if (sel.value) d[key] = sel.value; else delete d[key]; if (key === "test" && sel.value) delete d.test_fraction; changed(); });
      return sel;
    };
    const cols = el("div");
    const inspect = el("button", {class: "btn small", type: "button", text: "Read columns", onclick: async () => {
      if (!d.train) return toast("Choose a train file first");
      try { await readData(B); renderInspector(); } catch (e) { toast(e.message); }
    }});
    if (B.dataset) {
      const sg = B.dataset.suggest;
      cols.append(
        note(`Suggested: label ${sg.label || "none"}, type ${sg.type || "none"} (normal ${sg.normal_type || "?"}), ` +
          `categorical ${sg.categorical.join(", ") || "none"}.`, "info"),
        el("div", {class: "row"}, el("button", {class: "btn small primary", type: "button", text: "Apply suggestions", onclick: () => {
          if (sg.label) d.label = sg.label; else delete d.label;
          if (sg.type) d.type = sg.type;
          if (sg.normal_type) d.normal_type = sg.normal_type;
          for (const st of Object.values(B.raw.stages)) for (const step of st.pipeline || []) {
            if (step.class === "idslab.prep.Encode") step.params = {...(step.params || {}), categorical: [...sg.categorical]};
          }
          changed();
        }})),
        el("div", {class: "table-wrap"}, el("table", {class: "table"},
          el("thead", {}, el("tr", {}, ["column", "type", "distinct", "examples"].map((h) => el("th", {text: h})))),
          el("tbody", {}, B.dataset.columns.map((c) => el("tr", {}, el("td", {class: "mono", text: c.name}),
            el("td", {text: c.numeric ? "number" : "text"}), el("td", {class: "num", text: String(c.distinct)}),
            el("td", {class: "small muted", text: c.examples.slice(0, 4).join(", ")})))))));
    }
    const colNames = B.dataset ? B.dataset.columns.map((c) => c.name) : [];
    const colField = (key, label) => colNames.length
      ? field(label, bound(d, key, {options: [["", "(none)"], ...colNames]}))
      : field(label, bound(d, key, {placeholder: "column name"}));
    return card("Dataset", "Paths are relative to the config file.", cardBody(el("div", {class: "stack"},
      field("Name of the design", bound(B.raw, "name")),
      el("div", {class: "form-grid"}, field("Train file", fileSel("train")), field("Test file", fileSel("test"))),
      !d.test ? field("Or hold out this share as TEST", bound(d, "test_fraction", {type: "number", placeholder: "0.3"})) : null,
      el("div", {class: "row"}, inspect, el("span", {class: "muted small", text: "reads a sample, never the whole file"})),
      cols,
      el("div", {class: "form-grid"}, colField("label", "Label column (0/1)"), colField("type", "Type column"),
        field("Normal type", bound(d, "normal_type", {placeholder: "Normal"})), colField("id_column", "Row id column")),
      field("Drop columns", bound(d, "drop", {parse: (v) => v.split(/[,\s]+/).filter(Boolean), placeholder: "src_ip, dst_ip"})))));
  }

  function protocolPanel() {
    const p = B.raw.protocol = B.raw.protocol || {};
    const mode = p.mode || "standard";
    return card("Protocol", null, cardBody(el("div", {class: "stack"},
      el("div", {class: "form-grid"},
        field("Seeds", bound(p, "seeds", {parse: (v) => parseList(v).filter(Number.isInteger), placeholder: "0, 1, 2, 3, 4"})),
        objectiveField(p),
        field("Mode", bound(p, "mode", {options: [["", "standard"], ["heldout_family", "held-out family"], ["natural_novelty", "natural novelty"]],
          onSet: () => { if (p.mode !== "heldout_family") { delete p.families; delete p.family_level; } if (p.mode !== "natural_novelty") delete p.novel_types; }})),
        field("Search", bound(p, "search", {options: [["", "greedy"], ["greedy_fixed", "greedy, fixed later stages on"],
          ["joint", "greedy plus joint check"]]}))),
      costFields(p),
      mode === "heldout_family" ? field("Families to hold out", bound(p, "families", {parse: (v) => v.split(/[,\s]+/).filter(Boolean), placeholder: "all attack families"})) : null,
      mode === "natural_novelty" ? field("Novel types (TEST only)", bound(p, "novel_types", {parse: (v) => v.split(/[,\s]+/).filter(Boolean)})) : null,
      el("details", {class: "trace"}, el("summary", {text: "Fractions, folds and size"}), el("div", {class: "form-grid"},
        field("VAL fraction", bound(p, "val_fraction", {type: "number", placeholder: "0.2"})),
        field("CAL fraction", bound(p, "cal_fraction", {type: "number", placeholder: "0.2"})),
        field("Out-of-fold folds", bound(p, "oof_folds", {type: "number", placeholder: "3"})),
        field("Cores per fit", bound(p, "n_jobs", {type: "number", placeholder: "1"})),
        field("Subsample train rows", subsampleInput(p)))))));
  }

  // objective: a metric name, {cost: {missed_attack, false_alarm}} or {recall_at_fpr: x}
  function objectiveField(p) {
    const cur = p.objective;
    const kind = typeof cur === "object" && cur ? Object.keys(cur)[0] : (cur || "accuracy");
    const sel = el("select", {class: "select"},
      [...meta.objectives, "cost", "recall_at_fpr"].map((o) => el("option", {value: o, text: {
        cost: "cost of errors", recall_at_fpr: "recall at a false alarm cap"}[o] || o})));
    sel.value = kind;
    sel.addEventListener("change", () => {
      const v = sel.value;
      if (v === "accuracy") delete p.objective;
      else if (v === "cost") p.objective = {cost: {missed_attack: 10, false_alarm: 1}};
      else if (v === "recall_at_fpr") p.objective = {recall_at_fpr: 0.01};
      else p.objective = v;
      changed();
    });
    return field("Objective", sel);
  }
  function costFields(p) {
    const cur = p.objective;
    if (cur?.cost) {
      const c = cur.cost;
      return el("div", {class: "form-grid"},
        field("Cost of a missed attack", bound(c, "missed_attack", {type: "number"})),
        field("Cost of a false alarm", bound(c, "false_alarm", {type: "number"})),
        el("div", {class: "muted small", text: "Choices on VAL minimise the average cost per flow."}));
    }
    if (cur?.recall_at_fpr != null) {
      return el("div", {class: "form-grid"},
        field("Highest false alarm rate allowed", bound(cur, "recall_at_fpr", {type: "number"})),
        el("div", {class: "muted small", text: "Choices on VAL maximise recall among options within the cap."}));
    }
    return null;
  }

  function subsampleInput(p) {
    const input = el("input", {class: "input", type: "number", placeholder: "all", value: p.subsample?.train ?? ""});
    input.addEventListener("change", () => {
      const v = input.value.trim();
      const sub = {...(p.subsample || {})};
      if (v) sub.train = Number(v); else delete sub.train;
      if (Object.keys(sub).length) p.subsample = sub; else delete p.subsample;
      changed();
    });
    return input;
  }

  function controlsPanel() {
    const c = B.raw.controls = B.raw.controls || {};
    const classifiers = Object.entries(B.raw.stages).filter(([, s]) => s.kind === "classifier").map(([n]) => n);
    return card("Controls", "Built from the first stage alone.", cardBody(el("div", {class: "form-grid"},
      field("First stage", bound(c, "first_stage", {options: [["", "the entry stage"], ...classifiers]})),
      field("Bootstrap replicates", bound(c, "bootstrap", {type: "number", placeholder: "1000"})),
      field("Frontier replicates", bound(c, "frontier_bootstrap", {type: "number", placeholder: "200"})))));
  }

  renderCanvas();
  renderInspector();
  await sync();
  return () => isDirty() && "This design has unsaved changes.";
}

// ---------- edits on the config ----------

const TOP_ORDER = ["name", "dataset", "protocol", "entry", "stages", "controls"];
const STAGE_ORDER = ["kind", "role", "fit_on", "prep_fit_on", "min_pool", "calibrate", "train_on", "hard", "extra_features",
  "pipeline", "candidates", "decision", "routes", "next", "select_by", "min_val_rows", "type_objective"];

function byOrder(obj, order) {
  const out = {};
  for (const k of order) if (k in obj) out[k] = obj[k];
  for (const k of Object.keys(obj)) if (!(k in out)) out[k] = obj[k];
  return out;
}

// the config with keys in the order people write them, so saved files read the same way
function ordered(raw) {
  const out = byOrder(raw, TOP_ORDER);
  if (raw.stages) out.stages = Object.fromEntries(Object.entries(raw.stages).map(([n, st]) => [n, byOrder(st, STAGE_ORDER)]));
  return out;
}

async function readData(B) {
  const train = B.raw.dataset?.train;
  if (!train) return;
  const base = B.path ? dirOf(B.path) : "";
  const wsPath = [base, train].filter(Boolean).join("/").split("/")
    .reduce((acc, part) => (part === ".." ? acc.slice(0, -1) : part === "." ? acc : [...acc, part]), []).join("/");
  B.dataset = await api("/api/dataset" + q({path: wsPath}));
  const t = B.dataset.suggest.type;
  B.dataset.typeValues = t ? (B.dataset.columns.find((c) => c.name === t)?.examples || []) : [];
}

function encodeStep(B) {
  const cats = B.dataset?.suggest?.categorical;
  const existing = Object.values(B.raw.stages).flatMap((s) => s.pipeline || []).find((p) => p.class === "idslab.prep.Encode");
  const categorical = cats || existing?.params?.categorical;
  return categorical?.length ? [{class: "idslab.prep.Encode", params: {categorical: [...categorical]}}] : [];
}

function stageDefaults(kind, prev = {}) {
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

function addStage(B, kind) {
  const base = {classifier: "classifier", detector: "detector", multiclass: "type"}[kind];
  let n = base, i = 1;
  while (B.raw.stages[n]) n = `${base}${++i}`;
  const st = stageDefaults(kind, {pipeline: encodeStep(B)});
  if (!st.pipeline.length) delete st.pipeline;
  B.raw.stages[n] = st;
  if (!B.raw.entry || !B.raw.stages[B.raw.entry]) B.raw.entry = n;
  return n;
}

function renameStage(B, from, to) {
  const stages = {};
  for (const [n, s] of Object.entries(B.raw.stages)) stages[n === from ? to : n] = s;
  B.raw.stages = stages;
  for (const s of Object.values(stages)) {
    for (const k of Object.keys(s.routes || {})) if (s.routes[k] === from) s.routes[k] = to;
    if (s.next === from) s.next = to;
    if (s.extra_features) s.extra_features = s.extra_features.map((x) => (x === from ? to : x));
    if (s.hard?.source === from) s.hard.source = to;
  }
  if (B.raw.entry === from) B.raw.entry = to;
  if (B.raw.controls?.first_stage === from) B.raw.controls.first_stage = to;
  if (B.positions[from]) { B.positions[to] = B.positions[from]; delete B.positions[from]; B.layoutDirty = true; }
  B.selected = to;
}

function removeStage(B, name) {
  delete B.raw.stages[name];
  delete B.positions[name];
  for (const s of Object.values(B.raw.stages)) {
    for (const k of Object.keys(s.routes || {})) if (s.routes[k] === name) delete s.routes[k];
    if (s.next === name) delete s.next;
    if (s.extra_features) {
      s.extra_features = s.extra_features.filter((x) => x !== name);
      if (!s.extra_features.length) delete s.extra_features;
    }
  }
  if (B.raw.entry === name) B.raw.entry = Object.keys(B.raw.stages)[0] || "";
  if (B.raw.controls?.first_stage === name) delete B.raw.controls.first_stage;
}

