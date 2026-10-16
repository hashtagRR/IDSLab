// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// IDSLab dashboard: hash router and page shell over the JSON API in server.py.

import {$, el, api, note, store, stateBadge, job, onJob, pollJob, clearViewListeners, dur} from "./core.js";
import {configsView, newConfigView, editorView, commandView} from "./editor.js";
import {builderView} from "./builder.js";
import {overviewView, runsView, runView, unitView, jobView, exampleView, filesView} from "./views.js";

let meta = null;
let leaveGuard = null;  // returns a message while the current view has unsaved edits

function setCrumbs(items) {
  const c = $("#crumbs");
  c.replaceChildren(el("a", {href: "#/", text: "IDSLab"}));
  for (const [label, href] of items) {
    c.append(el("span", {class: "sep", text: "/"}));
    c.append(href ? el("a", {href, text: label}) : el("b", {text: label}));
  }
}

function parse() {
  const raw = location.hash.replace(/^#\/?/, "");
  const parts = raw ? raw.split("/").map((p) => { try { return decodeURIComponent(p); } catch { return p; } }) : [];
  return {name: parts[0] || "", args: parts.slice(1)};
}

const NAV = {"": "overview", configs: "configs", config: "configs", new: "new", runs: "runs", run: "runs",
  unit: "runs", job: "job", example: "example", files: "files", builder: "builder"};

let lastHash = location.hash;
async function route() {
  if (leaveGuard) {
    const msg = leaveGuard();
    if (msg && !confirm(`${msg} Leave anyway?`)) {
      history.replaceState(null, "", lastHash || "#/");
      return;
    }
  }
  lastHash = location.hash;
  leaveGuard = null;
  clearViewListeners();
  const {name, args} = parse();
  const view = $("#view");
  view.replaceChildren();
  $("#app").classList.remove("menu-open");
  const navKey = name === "command" ? `command-${args[0]}` : NAV[name];
  for (const a of document.querySelectorAll(".nav a")) a.classList.toggle("active", a.dataset.nav === navKey);
  setCrumbs([]);
  try {
    switch (name) {
      case "": setCrumbs([["Overview"]]); await overviewView(view, meta); break;
      case "configs": setCrumbs([["Configs"]]); await configsView(view); break;
      case "new": setCrumbs([["Configs", "#/configs"], ["New"]]); await newConfigView(view); break;
      case "config": leaveGuard = await editorView(view, meta, args.join("/"), setCrumbs); break;
      case "builder": leaveGuard = await builderView(view, meta, args.join("/") || null, setCrumbs); break;
      case "command": setCrumbs([[args[0] || "Command"]]); await commandView(view, meta, args[0], args.slice(1).join("/")); break;
      case "runs": setCrumbs([["Runs"]]); await runsView(view); break;
      case "run": await runView(view, meta, args[0], args[1], setCrumbs); break;
      case "unit": await unitView(view, args.join("/"), setCrumbs); break;
      case "job": setCrumbs([["Job"]]); jobView(view); break;
      case "example": setCrumbs([["Example data"]]); exampleView(view); break;
      case "files": await filesView(view, args.join("/"), setCrumbs); break;
      default: view.append(note("No such page.", "warn"));
    }
  } catch (e) {
    view.append(note(e.message, "bad"));
  }
  view.focus({preventScroll: true});
}

function footer(s) {
  const foot = $("#side-foot");
  const line = s && s.state !== "idle"
    ? el("a", {class: "job", href: "#/job"}, stateBadge(s.state),
      el("span", {text: `${s.command}${s.state === "running" ? `, ${dur(s.now - s.started_at)}` : ""}`}))
    : null;
  foot.replaceChildren(line || "", el("div", {text: `IDSLab ${meta?.version ?? ""}`}),
    el("div", {class: "mono", text: meta?.workspace ?? ""}));
}

function theme() {
  const cur = store.get("theme");
  if (cur) document.documentElement.dataset.theme = cur;
  $("#theme-btn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
  });
}

async function boot() {
  theme();
  $("#menu-btn").addEventListener("click", () => $("#app").classList.toggle("menu-open"));
  $("#refresh-btn").addEventListener("click", () => route());
  addEventListener("beforeunload", (e) => { if (leaveGuard?.()) e.preventDefault(); });
  try {
    meta = await api("/api/meta");
  } catch (e) {
    $("#view").append(note(`The dashboard server is not answering: ${e.message}`, "bad"));
    return;
  }
  onJob(footer, {global: true});
  footer(job.snapshot);
  addEventListener("hashchange", route);
  await pollJob();
  route();
}

boot();
