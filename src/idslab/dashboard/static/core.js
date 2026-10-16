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
  const t = el("div", {class: "toast", role: "status", text});
  document.body.append(t);
  setTimeout(() => t.remove(), 3200);
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
  location.hash = "#/job";
  return snap;
}
