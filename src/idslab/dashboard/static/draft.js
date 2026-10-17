// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// One draft of a config, shared by the setup steps, the visual builder and the YAML view.
//
// The text is what Save writes. Typing in the YAML view keeps the text exactly as typed, comments
// included. A visual edit changes the config data and the server writes the text again with the
// CLI's writer; when that rewrite would drop comments or hand formatting, the first visual edit
// asks first and shows the change. Validation always comes from the server, with the loader the
// CLI uses.

import {el, api, q, dialog, lineDiff, diffView} from "./core.js";

export const clone = (v) => JSON.parse(JSON.stringify(v));
export const dirOf = (p) => (p && p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");

// a path relative to a folder; both are relative to the workspace
export function relpath(fromDir, to) {
  const a = fromDir ? fromDir.split("/") : [], b = to.split("/");
  let i = 0;
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  return [...Array(a.length - i).fill(".."), ...b.slice(i)].join("/");
}
// a path written in a config, as a workspace path
export function wsPath(configPath, p) {
  return [dirOf(configPath || ""), p].filter(Boolean).join("/").split("/")
    .reduce((acc, part) => (part === ".." ? acc.slice(0, -1) : part === "." || part === "" ? acc : [...acc, part]), []).join("/");
}

const TOP_ORDER = ["name", "dataset", "protocol", "entry", "stages", "controls"];
const STAGE_ORDER = ["kind", "role", "fit_on", "prep_fit_on", "min_pool", "calibrate", "train_on", "hard", "extra_features",
  "pipeline", "candidates", "decision", "routes", "next", "select_by", "min_val_rows", "type_objective", "cost"];

function byOrder(obj, order) {
  const out = {};
  for (const k of order) if (k in obj) out[k] = obj[k];
  for (const k of Object.keys(obj)) if (!(k in out)) out[k] = obj[k];
  return out;
}
// the config with keys in the order people write them, so written files read the same way
export function ordered(raw) {
  const out = byOrder(raw, TOP_ORDER);
  if (raw.stages && typeof raw.stages === "object") {
    out.stages = Object.fromEntries(Object.entries(raw.stages).map(([n, st]) =>
      [n, st && typeof st === "object" ? byOrder(st, STAGE_ORDER) : st]));
  }
  return out;
}

export class Draft {
  constructor({path = null, text = "", saved = null, positions = {}}) {
    this.path = path;
    this.text = text;
    this.savedText = saved;  // null until the draft is saved to a file
    this.positions = clone(positions);
    this.savedPositions = JSON.stringify(positions);
    this.raw = null;          // the parsed config, or null while the text does not parse
    this.base = null;         // raw as of the last accepted change, to undo a declined rewrite
    this.canonical = null;    // the text the server writes for raw
    this.validation = null;   // {ok, config, text} or {ok: false, error}
    this.source = "text";     // "text": the text is as typed; "raw": the text is written from raw
    this.rewriteOk = false;
    this.touched = false;     // edited since it was opened
    this.dataset = null;      // column inspection of the train file
    this.listeners = new Set();
    this.timer = null;
    this.pending = null;
    this.inflight = null;
    this.seq = 0;
  }

  static async open(path) {
    const [{yaml}, layout] = await Promise.all([api("/api/config" + q({path})), api("/api/layout" + q({path}))]);
    const d = new Draft({path, text: yaml, saved: yaml, positions: layout.positions || {}});
    await d.refresh();
    return d;
  }
  static async fromText(text) {
    const d = new Draft({text});
    await d.refresh();
    return d;
  }

  // events: "text" (typed in the YAML view), "parsed" (raw replaced from the text), "raw" (a visual
  // edit), "revert" (a visual edit undone), "validation" (new validation of the same raw),
  // "layout", "dataset", "saved"
  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit(what) { for (const fn of [...this.listeners]) fn(what); }

  get layoutDirty() { return JSON.stringify(this.positions) !== this.savedPositions; }
  get dirty() {
    if (this.savedText === null) return this.touched;
    return this.text !== this.savedText || Boolean(this.path && this.layoutDirty);
  }
  get lossy() { return this.canonical != null && this.canonical.trimEnd() !== this.text.trimEnd(); }
  get busy() { return Boolean(this.pending || this.inflight); }
  errors() {
    const v = this.validation;
    return v && !v.ok ? String(v.error).split("\n").filter(Boolean) : [];
  }

  // ---------- YAML typed by hand ----------

  setText(text) {
    this.text = text;
    this.touched = true;
    this.source = "text";
    this.rewriteOk = false;
    this.emit("text");
    this.schedule(() => this.refresh(), 450);
  }

  async refresh() {
    const mine = ++this.seq;
    const text = this.text;
    const work = (async () => {
      let parsed;
      try { parsed = await api("/api/parse", {yaml: text}); } catch (e) { parsed = {ok: false, error: e.message}; }
      if (mine !== this.seq) return;
      if (!parsed.ok) {
        Object.assign(this, {raw: null, base: null, canonical: null, validation: {ok: false, error: parsed.error}});
        this.emit("parsed");
        return;
      }
      let r;
      try { r = await api("/api/dump", {config: ordered(parsed.config), path: this.path}); }
      catch (e) { r = {ok: false, error: e.message, yaml: null}; }
      if (mine !== this.seq) return;
      Object.assign(this, {raw: parsed.config, base: clone(parsed.config), canonical: r.yaml, validation: r});
      this.emit("parsed");
    })();
    this.inflight = work;
    await work;
    if (this.inflight === work) this.inflight = null;
  }

  // ---------- visual edits: the caller has changed this.raw in place ----------

  async commit() {
    if (!this.raw) return false;
    if (this.source === "text" && this.lossy && !this.rewriteOk && !(await this.askRewrite())) {
      this.raw = clone(this.base);
      this.emit("revert");
      return false;
    }
    this.rewriteOk = true;
    this.touched = true;
    this.source = "raw";
    this.base = clone(this.raw);
    this.emit("raw");
    this.schedule(() => this.sync(), 250);
    return true;
  }

  async sync() {
    const mine = ++this.seq;
    const work = (async () => {
      let r;
      try { r = await api("/api/dump", {config: ordered(this.raw), path: this.path}); }
      catch (e) { r = {ok: false, error: e.message, yaml: this.text}; }
      if (mine !== this.seq) return;
      Object.assign(this, {text: r.yaml, canonical: r.yaml, validation: r});
      this.emit("validation");
    })();
    this.inflight = work;
    await work;
    if (this.inflight === work) this.inflight = null;
  }

  async askRewrite() {
    const rows = lineDiff(this.text, this.canonical);
    const comments = rows.filter((r) => r[0] === "-" && /(^|\s)#/.test(r[1])).length;
    const what = comments ? `${comments} comment line${comments === 1 ? "" : "s"} and the hand formatting` : "the hand formatting";
    const body = el("div", {class: "stack"},
      el("p", {text: `A visual edit writes the YAML again from the settings, so ${what} would not be kept. ` +
        "The settings themselves stay the same."}),
      el("p", {class: "small muted", text: "To keep the file as written, choose Keep and make this change in the YAML view."}),
      el("details", {class: "trace", open: true}, el("summary", {text: "What the rewrite changes"}), diffView(rows)));
    return (await dialog("Rewrite the YAML?", body,
      [["Keep the YAML as written", false], ["Rewrite and continue", true, "primary"]])) === true;
  }

  // ---------- timing ----------

  schedule(fn, ms) {
    clearTimeout(this.timer);
    this.pending = fn;
    this.timer = setTimeout(() => { this.pending = null; fn(); }, ms);
  }
  async flush() {
    if (this.pending) {
      clearTimeout(this.timer);
      const fn = this.pending;
      this.pending = null;
      await fn();
    }
    while (this.inflight) await this.inflight;
  }

  // ---------- files ----------

  async save(target, {overwrite}) {
    await this.flush();
    const r = await api("/api/config/save", {path: target, yaml: this.text, overwrite});
    if (Object.keys(this.positions).length || this.layoutDirty) await api("/api/layout", {path: r.path, positions: this.positions});
    this.path = r.path;
    this.savedText = this.text;
    this.savedPositions = JSON.stringify(this.positions);
    this.validation = r;
    this.emit("saved");
    return r;
  }
  async revert() {
    if (this.savedText === null) return;
    this.text = this.savedText;
    this.positions = JSON.parse(this.savedPositions);
    this.source = "text";
    this.rewriteOk = false;
    this.emit("text");
    await this.refresh();
  }

  // the train file's columns, with suggestions; a missing file leaves this.dataset null
  async inspect() {
    const train = this.raw?.dataset?.train;
    if (!train) { this.dataset = null; return null; }
    const ds = await api("/api/dataset" + q({path: wsPath(this.path, train)}));
    const t = this.raw.dataset.type || ds.suggest.type;
    ds.typeValues = t ? (ds.columns.find((c) => c.name === t)?.examples || []) : [];
    this.dataset = ds;
    this.emit("dataset");
    return ds;
  }
}

// ---------- form fields bound to the draft ----------

const listText = (v) => (Array.isArray(v) ? v : v == null ? [] : [v]).map((x) => (x === null ? "null" : String(x))).join(", ");
export function parseList(text) {
  return String(text).split(/[,\s]+/).filter(Boolean).map((x) => {
    if (x === "null" || x === "None") return null;
    if (x === "true") return true;
    if (x === "false") return false;
    const n = Number(x);
    return Number.isFinite(n) ? n : x;
  });
}
export const names = (v) => String(v).split(/[,\s]+/).filter(Boolean);
export {listText};

// An input bound to obj[key]; an empty value removes the key. Every change goes through commit().
export function bound(draft, obj, key, {type = "text", options = null, placeholder = "", parse = null, onSet = null, label = null, id = null} = {}) {
  let input;
  if (options) {
    input = el("select", {class: "select", id}, options.map((o) => {
      const [v, text] = Array.isArray(o) ? o : [o, o];
      return el("option", {value: v, text});
    }));
    const cur = obj[key] == null ? "" : String(obj[key]);
    if (cur && !options.some((o) => String(Array.isArray(o) ? o[0] : o) === cur)) input.append(el("option", {value: cur, text: `${cur} (as written)`}));
    input.value = cur;
  } else {
    const v = obj[key];
    input = el("input", {class: "input", type, placeholder, id,
      value: v == null ? "" : Array.isArray(v) ? listText(v) : typeof v === "object" ? JSON.stringify(v) : String(v)});
  }
  if (label) input.setAttribute("aria-label", label);
  input.addEventListener("change", () => {
    const raw = input.value.trim();
    if (raw === "") delete obj[key];
    else obj[key] = parse ? parse(raw) : type === "number" ? Number(raw) : raw;
    onSet?.();
    draft.commit();
  });
  return input;
}
