// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Shared helpers. The DOM is built with el() and textContent only, never innerHTML:
// stage names, file names and log lines come from user files and are untrusted text.

export const $ = (s, root = document) => root.querySelector(s);

export function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "style") Object.assign(n.style, v);
    else if (k === "value") n.value = v;
    else if (k === "checked") n.checked = Boolean(v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) n.append(k);
  return n;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function svg(tag, attrs = {}, ...kids) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "text") n.textContent = v;
    else if (k === "style") Object.assign(n.style, v);  // CSSOM, so var() works and CSP allows it
    else n.setAttribute(k, v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) n.append(k);
  return n;
}

export async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

export const q = (params) => "?" + new URLSearchParams(params).toString();
export const fileUrl = (rel, download) =>
  "/files/" + rel.split("/").map(encodeURIComponent).join("/") + (download ? "?download=1" : "");
export const enc = encodeURIComponent;

export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* private window */ } },
};

// ---------- formatting ----------

export function fmt(v, digits = 4) {
  if (v == null || v === "") return "n/a";
  if (typeof v !== "number") return String(v);
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toFixed(digits);
}
export function signed(v, digits = 4) {
  if (v == null) return "n/a";
  const s = v.toFixed(digits);
  return v > 0 ? "+" + s : s;
}
export function dur(sec) {
  if (sec == null) return "n/a";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}
export function ago(ts, now = Date.now() / 1000) {
  if (!ts) return "n/a";
  const t = typeof ts === "string" ? Date.parse(ts) / 1000 : ts;
  const d = now - t;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
}
export function bytes(n) {
  if (n == null) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i ? n.toFixed(1) : n} ${u[i]}`;
}
export const pretty = (s) => String(s).replace(/_/g, " ");
export const SYSTEM_LABEL = {
  cascade: "Cascade",
  B1_first_stage: "B1 first stage",
  B2_first_stage_val_cutoff: "B2 first stage, VAL cutoff",
  budget_matched_first_stage: "First stage, budget matched",
  joint_check: "Joint check",
};
export const systemLabel = (k) => SYSTEM_LABEL[k] || pretty(k);

// ---------- small components ----------

const STATE_BADGE = {
  running: ["blue", "Running"], completed: ["green", "Completed"], failed: ["red", "Failed"],
  stopped: ["amber", "Stopped"], idle: ["", "Idle"],
};
export function stateBadge(state) {
  const [cls, label] = STATE_BADGE[state] || ["", state];
  return el("span", {class: `badge ${cls}`}, el("span", {class: "dot" + (state === "running" ? " pulse" : "")}), label);
}
export const badge = (text, cls = "") => el("span", {class: `badge ${cls}`, text});

export function card(title, sub, body, actions) {
  return el("section", {class: "card"},
    el("div", {class: "card-head"},
      el("div", {}, el("h3", {text: title}), sub ? el("p", {text: sub}) : null),
      actions ? el("div", {class: "row"}, actions) : null),
    body);
}
export const cardBody = (...kids) => el("div", {class: "card-body"}, ...kids);

export function pageHead(title, text, actions, eyebrow) {
  return el("div", {class: "page-head"},
    el("div", {},
      eyebrow ? el("div", {class: "eyebrow"}, eyebrow) : null,
      el("h2", {text: title}),
      text ? el("p", {text}) : null),
    actions ? el("div", {class: "row"}, actions) : null);
}

export function metric(label, value, sub, cls = "") {
  return el("div", {class: `card metric ${cls}`},
    el("div", {class: "label", text: label}),
    el("div", {class: "value", text: value}),
    sub ? el("div", {class: "sub", text: sub}) : null);
}

export function table(headers, rows, {onRow, numeric = []} = {}) {
  const num = new Set(numeric);
  return el("div", {class: "table-wrap"},
    el("table", {class: "table"},
      el("thead", {}, el("tr", {}, headers.map((h, i) => el("th", {class: num.has(i) ? "num" : null, text: h})))),
      el("tbody", {}, rows.map((r) => {
        const cells = r.cells || r;
        const tr = el("tr", {class: onRow ? "clickable" : null},
          cells.map((c, i) => el("td", {class: num.has(i) ? "num" : null},
            c instanceof Node ? c : String(c ?? "")))) ;
        if (onRow) tr.addEventListener("click", (e) => { if (!e.target.closest("a,button")) onRow(r); });
        return tr;
      }))));
}

export function empty(text, action) {
  return el("div", {class: "empty"}, el("div", {text}), action || null);
}

export function note(text, cls = "info") {
  return el("div", {class: `note ${cls}`, text});
}

export function toast(text) {
  const t = el("div", {class: "toast", text});
  document.body.append(t);
  announce(text);
  setTimeout(() => t.remove(), 3200);
}

// Text for screen readers: one polite live region in index.html, refilled per message.
export function announce(text) {
  const live = document.getElementById("live");
  if (!live) return;
  live.textContent = "";
  setTimeout(() => { live.textContent = text; }, 30);
}

// Redraw part of a page after the browser has moved focus (a change event fires before Tab moves
// on), then put focus back on the element in the same place, so keyboard users keep their spot.
const FOCUSABLE = "a[href],button,input,select,textarea,summary,[tabindex]";
export function rerender(container, render) {
  setTimeout(() => {
    const a = document.activeElement;
    let id = null, idx = -1;
    if (a && a !== document.body && container.contains(a)) {
      id = a.id || null;
      idx = [...container.querySelectorAll(FOCUSABLE)].indexOf(a);
    }
    render();
    const n = (id && container.querySelector(`#${CSS.escape(id)}`)) || (idx >= 0 ? container.querySelectorAll(FOCUSABLE)[idx] : null);
    n?.focus({preventScroll: true});
  }, 0);
}

// ---------- dialogs ----------

// A modal dialog with focus kept inside; resolves with the value of the button pressed (or null).
export function dialog(title, body, buttons, {initial = null} = {}) {
  return new Promise((resolve) => {
    const d = el("dialog", {class: "dialog", "aria-label": title});
    const done = (v) => { d.close(); d.remove(); resolve(v); };
    d.append(el("h3", {text: title}), el("div", {class: "dialog-body"}, body),
      el("div", {class: "row dialog-actions"}, el("span", {class: "spacer"}), buttons.map(([label, value, cls]) =>
        el("button", {class: `btn ${cls || ""}`, type: "button", text: label, onclick: () => done(typeof value === "function" ? value() : value)}))));
    d.addEventListener("cancel", (e) => { e.preventDefault(); done(null); });
    document.body.append(d);
    d.showModal();
    (initial ? d.querySelector(initial) : d.querySelector(".btn.primary") || d.querySelector("button"))?.focus();
  });
}
export async function ask(title, text, okLabel = "Continue", cls = "primary") {
  return (await dialog(title, el("p", {text}), [["Cancel", false], [okLabel, true, cls]])) === true;
}
export async function askPath(title, text, value) {
  const input = el("input", {class: "input mono", value, "aria-label": "File path"});
  const body = el("div", {class: "stack"}, el("p", {class: "small", text}), input);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") body.closest("dialog").querySelector(".btn.primary").click(); });
  const v = await dialog(title, body, [["Cancel", null], ["Save", () => input.value.trim(), "primary"]], {initial: "input"});
  return v || null;
}

// ---------- plain labels ----------

export const KIND = {
  classifier: "Classifier (attack or benign)",
  detector: "Anomaly detector (normal or anomalous)",
  multiclass: "Type stage (names the attack type)",
};
export const KIND_SHORT = {classifier: "classifier", detector: "anomaly detector", multiclass: "type stage"};
export const METRIC = {
  accuracy: "Accuracy", balanced_accuracy: "Balanced accuracy", recall: "Detection rate (recall)",
  precision: "Precision", f1: "F1", mcc: "MCC", fpr: "False-alarm rate (FPR)", auc: "AUC",
};
export const metricLabel = (k) => METRIC[k] || (String(k).startsWith("cost(") ? "Average cost per flow"
  : String(k).startsWith("recall_at_fpr(") ? `Recall at a false-alarm cap of ${String(k).slice(14, -1)}` : pretty(k));
export const STATE = {
  draft: ["", "Draft"], needs_data: ["amber", "Data missing"], invalid: ["red", "Needs fixing"],
  running: ["blue", "Running"], partial: ["amber", "Unfinished"], completed: ["green", "Completed"],
  outdated: ["amber", "Needs a new run"],
};
export function expBadge(state) {
  const [cls, label] = STATE[state] || ["", state];
  return el("span", {class: `badge ${cls}`}, el("span", {class: "dot" + (state === "running" ? " pulse" : "")}), label);
}

// Objective-aware numbers. Rates read as percentages and their differences as percentage points;
// a cost objective is stored as the negative average cost, so it is shown as a positive cost.
const RATES = new Set(["accuracy", "balanced_accuracy", "recall", "precision", "f1", "fpr"]);
export function objective(goal) {
  const g = String(goal || "");
  const isCost = g.startsWith("cost(");
  const isRate = RATES.has(g) || g.startsWith("recall_at_fpr(");
  const num = (v, d = 4) => (v == null ? "n/a" : v.toFixed(d));
  return {
    label: metricLabel(g),
    isCost, isRate,
    value: (v) => (v == null ? "n/a" : isCost ? num(-v) : isRate ? `${(v * 100).toFixed(2)}%` : num(v)),
    diff: (d) => {
      if (d == null) return "n/a";
      if (isCost) return d === 0 ? "no change in cost" : `${num(Math.abs(d))} ${d > 0 ? "lower" : "higher"} cost per flow`;
      if (isRate) return `${d > 0 ? "+" : ""}${(d * 100).toFixed(2)} percentage points`;
      return `${d > 0 ? "+" : ""}${num(d)}`;
    },
    short: (d) => (d == null ? "n/a" : isRate ? `${d > 0 ? "+" : ""}${(d * 100).toFixed(2)} pp`
      : isCost ? `${d > 0 ? "-" : "+"}${num(Math.abs(d))} cost` : `${d > 0 ? "+" : ""}${num(d)}`),
  };
}

// ---------- errors in plain words ----------

// Config errors as people read them. The loader's own line stays available next to it.
export function plainError(line) {
  let m;
  if ((m = /^stage (\S+): routes must cover exactly \[(.*?)\] \(got \[(.*?)\]\)/.exec(line))) {
    const need = m[2].split(",").map((x) => x.trim().replace(/'/g, "")).filter(Boolean);
    const got = new Set(m[3].split(",").map((x) => x.trim().replace(/'/g, "")).filter(Boolean));
    const miss = need.filter((x) => !got.has(x));
    if (miss.length) return `${m[1]}: the ${miss.join(" and ")} output${miss.length > 1 ? "s have" : " has"} no destination. Choose a stage, ATTACK or BENIGN.`;
  }
  if ((m = /^stage (\S+): an annotate stage needs 'next'/.exec(line))) return `${m[1]}: choose where every flow goes after the type is named (a stage, ATTACK or BENIGN).`;
  if ((m = /^stages not reachable from the entry stage: \[(.*)\]/.exec(line))) return `${m[1].replace(/'/g, "")}: no route leads here from the entry stage. Send an output to it, or remove it.`;
  if ((m = /^routing cycle: (.+)$/.exec(line))) return `The routes loop back: ${m[1].replace(/ -> /g, " to ")}. Point one of these outputs to a later stage or a terminal.`;
  if ((m = /^dataset: give exactly one of 'test'/.exec(line))) return "Data: choose a test file, or hold out a share of the training file as TEST, not both.";
  if ((m = /^dataset: give 'label', 'type', or both/.exec(line))) return "Data: choose the label column, the type column, or both.";
  if ((m = /^protocol.seeds: give a non-empty list/.exec(line))) return "Evaluation: give at least one seed (one repeated run).";
  return line;
}
// Which setup step an error line belongs to.
export function errorStep(line) {
  if (/^dataset/.test(line)) return "data";
  if (/^(protocol|controls)/.test(line)) return "evaluation";
  if (/^(stage |stages|entry|routing cycle|routes and score|chained score)/.test(line)) return "cascade";
  if (/YAML|mapping/.test(line)) return "yaml";
  return "review";
}

// ---------- line diff (for showing what a rewrite changes) ----------

export function lineDiff(a, b) {
  const x = a.split("\n"), y = b.split("\n");
  const n = x.length, m = y.length;
  if (n * m > 4_000_000) return [...x.map((t) => ["-", t]), ...y.map((t) => ["+", t])];
  const L = Array.from({length: n + 1}, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) {
    L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push([" ", x[i]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push(["-", x[i++]]);
    else out.push(["+", y[j++]]);
  }
  while (i < n) out.push(["-", x[i++]]);
  while (j < m) out.push(["+", y[j++]]);
  return out;
}
export function diffView(rows) {
  return el("pre", {class: "code diff"}, rows.filter((r, k) => r[0] !== " " || rows.slice(Math.max(0, k - 2), k + 3).some((z) => z[0] !== " "))
    .map(([t, line]) => el("span", {class: t === "-" ? "del" : t === "+" ? "add" : null}, `${t} ${line}\n`)));
}

export function field(label, input, hint) {
  return el("label", {class: "field"}, el("span", {text: label}), input, hint ? el("div", {class: "hint", text: hint}) : null);
}
export function checkbox(label, hint, checked = false) {
  const input = el("input", {type: "checkbox", checked});
  const node = el("label", {class: "check"}, input, el("div", {}, label, hint ? el("small", {text: hint}) : null));
  return {node, input};
}

export function logView(lines) {
  const pre = el("pre", {class: "log"});
  fillLog(pre, lines);
  return pre;
}
export function fillLog(pre, lines) {
  const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  pre.replaceChildren(...lines.map((line) => {
    let cls = null;
    if (line.startsWith("$ ")) cls = "cmd";
    else if (/^idslab: |Traceback|Error|error:/.test(line)) cls = "bad";
    else if (/^warning|UserWarning|ConvergenceWarning/.test(line)) cls = "warn";
    else if (/^report: |^wrote |TEST /.test(line)) cls = "ok";
    return el("span", {class: cls}, line + "\n");
  }));
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}

// ---------- job state shared by every view ----------

// View listeners are dropped on every navigation (clearViewListeners); global ones stay.
const listeners = new Set(), viewListeners = new Set();
export const job = {snapshot: null};
export function onJob(fn, {global = false} = {}) { (global ? listeners : viewListeners).add(fn); }
export function clearViewListeners() { viewListeners.clear(); }
const notify = (snap) => { for (const fn of [...listeners, ...viewListeners]) fn(snap); };
let timer = null;
export async function pollJob() {
  clearTimeout(timer);
  try {
    job.snapshot = await api("/api/job?tail=400");
    notify(job.snapshot);
  } catch { /* server gone; try again */ }
  timer = setTimeout(pollJob, job.snapshot?.state === "running" ? 1000 : 4000);
}

export async function startJob(body) {
  const snap = await api("/api/job", body);
  job.snapshot = snap;
  notify(snap);
  pollJob();
  location.hash = "#/jobs";
  return snap;
}
