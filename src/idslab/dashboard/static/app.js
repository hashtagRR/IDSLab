// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// IDSLab dashboard: hash router and page shell over the JSON API in server.py. Old addresses
// (#/config, #/builder, #/run, #/job, #/command/...) still work: they lead to the page that now
// holds the same thing.

import {$, el, api, q, note, store, stateBadge, job, onJob, pollJob, clearViewListeners, announce} from "./core.js";
import {homeView, newView, experimentView, expHref, leaveMessage, left, unsavedOnClose} from "./experiments.js";
import {templatesView, datasetsView, reportsView, helpView} from "./library.js";
import {jobsView, jobLine} from "./jobs.js";
import {compareView, sweepsView, sweepEditorView} from "./compare.js";
import {unitView, filesView} from "./views.js";

let meta = null;
let destroyView = null;  // cleanup of the page on screen (draft listeners, key handlers)
let leaveGuard = null;   // a page outside experiments with unsaved edits (the sweep editor)

function setCrumbs(items) {
  const c = $("#crumbs");
  c.replaceChildren(el("a", {href: "#/", text: "IDSLab"}));
  for (const [label, href] of items) {
    c.append(el("span", {class: "sep", text: "/", "aria-hidden": "true"}));
    c.append(href ? el("a", {href, text: label}) : el("b", {text: label, "aria-current": "page"}));
  }
  document.title = `${items.length ? items[items.length - 1][0] + " | " : ""}IDSLab`;
}

function parse(hash) {
  const raw = hash.replace(/^#\/?/, "");
  const parts = raw ? raw.split("/").map((p) => { try { return decodeURIComponent(p); } catch { return p; } }) : [];
  return {name: parts[0] || "", args: parts.slice(1)};
}

const NAV = {"": "experiments", e: "experiments", new: "experiments", templates: "templates", datasets: "datasets",
  jobs: "jobs", reports: "reports", help: "help", files: "files", unit: "experiments", compare: "compare",
  sweeps: "sweeps", sweep: "sweeps"};

// old addresses: the hash of the page that holds the same thing now, or null
async function alias(name, args) {
  const path = args.join("/");
  switch (name) {
    case "configs": case "runs": return "#/";
    case "config": return path ? expHref(`c:${path}`, "setup", "yaml") : "#/";
    case "builder": return path ? expHref(`c:${path}`, "setup", "cascade") : "#/new/blank";
    case "job": return "#/jobs";
    case "example": return "#/datasets";
    case "run": {
      const tab = {overview: "results", report: "report", units: "runs", runs: "runs", files: "files"}[args[1]] || "";
      try {
        const e = await api("/api/experiment" + q({output: args[0]}));
        return expHref(e.key, tab || null);
      } catch { return expHref(`o:${args[0]}`, tab || null); }
    }
    case "command": {
      const [cmd, ...rest] = args;
      const cfg = rest.join("/");
      if (cmd === "run") return cfg ? expHref(`c:${cfg}`, "setup", "review") : "#/";
      if (cmd === "report") return cfg ? expHref(`c:${cfg}`, "report") : "#/reports";
      if (cmd === "export-splits") return cfg ? expHref(`c:${cfg}`, "runs") : "#/";
      return "#/";
    }
    default: return null;
  }
}

let lastHash = location.hash;
let seq = 0;
async function route() {
  const next = location.hash || "#/";
  const msg = leaveMessage(next) || leaveGuard?.();
  if (msg && !confirm(`${msg} Leave anyway?`)) {
    history.replaceState(null, "", lastHash || "#/");
    return;
  }
  const {name, args} = parse(next);
  const target = await alias(name, args);
  if (target) { history.replaceState(null, "", target); return route(); }
  lastHash = next;
  left(next);
  leaveGuard = null;
  destroyView?.();
  destroyView = null;
  clearViewListeners();
  const mine = ++seq;
  // each visit draws into its own container, so a slow page cannot draw over a newer one
  const view = el("div", {class: "page"});
  $("#view").replaceChildren(view);
  $("#app").classList.remove("menu-open");
  for (const a of document.querySelectorAll(".nav a")) {
    const on = a.dataset.nav === NAV[name];
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  setCrumbs([]);
  try {
    let d = null;
    switch (name) {
      case "": setCrumbs([["Experiments"]]); await homeView(view, meta); break;
      case "new": setCrumbs([["Experiments", "#/"], ["New experiment"]]); await newView(view, meta, args); break;
      case "e": d = await experimentView(view, meta, args[0] || "", args[1], args[2], setCrumbs); break;
      case "templates": setCrumbs([["Templates"]]); await templatesView(view, meta); break;
      case "datasets": setCrumbs([["Datasets", "#/datasets"], ...(args.length ? [[args.join("/")]] : [])]); await datasetsView(view, meta, args.join("/")); break;
      case "jobs": setCrumbs([["Jobs"]]); await jobsView(view); break;
      case "reports": setCrumbs([["Reports"]]); await reportsView(view); break;
      case "help": setCrumbs([["Help"]]); helpView(view, meta); break;
      case "compare": setCrumbs([["Compare"]]); await compareView(view, meta, args.join("/")); break;
      case "sweeps": setCrumbs([["Sweeps"]]); await sweepsView(view); break;
      case "sweep": leaveGuard = await sweepEditorView(view, meta, args.join("/"), setCrumbs); break;
      case "unit": await unitView(view, args.join("/"), setCrumbs); break;
      case "files": await filesView(view, args.join("/"), setCrumbs); break;
      default: view.append(note("No such page.", "warn"), el("a", {class: "btn", href: "#/", text: "Experiments"}));
    }
    if (mine !== seq) { d?.(); return; }
    destroyView = d;
  } catch (e) {
    if (mine === seq) view.append(note(e.message, "bad"));
  }
  if (mine !== seq) return;
  const h = view.querySelector("h2");
  announce(h ? `${h.textContent} page` : "Page loaded");
  $("#view").focus({preventScroll: true});
}

function jobIndicator(s) {
  const pill = $("#job-pill");
  const line = jobLine(s);
  pill.classList.toggle("hidden", !line || s.state !== "running");
  if (line && s.state === "running") pill.replaceChildren(stateBadge(s.state), el("span", {text: line}));
  const foot = $("#side-foot");
  foot.replaceChildren(
    line && s.state !== "running" ? el("a", {class: "job", href: "#/jobs"}, stateBadge(s.state), el("span", {text: `last job: idslab ${s.command}`})) : "",
    el("div", {class: "ws", title: meta?.workspace ?? ""}, el("span", {class: "muted", text: "Workspace "}),
      el("b", {text: (meta?.workspace ?? "").split(/[\\/]/).filter(Boolean).pop() || "/"})),
    el("div", {class: "muted", text: `IDSLab ${meta?.version ?? ""}`}));
}

let lastState = null;
function announceJob(s) {
  if (s?.state && lastState === "running" && s.state !== "running") announce(`Job ${s.command} ${s.state}`);
  lastState = s?.state;
}

function theme() {
  const cur = store.get("theme");
  if (cur) document.documentElement.dataset.theme = cur;
  const btn = $("#theme-btn");
  const label = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    btn.textContent = dark ? "Light theme" : "Dark theme";
    btn.setAttribute("aria-pressed", String(dark));
  };
  btn.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
    label();
  });
  label();
}

async function boot() {
  theme();
  $("#menu-btn").addEventListener("click", () => {
    const open = $("#app").classList.toggle("menu-open");
    $("#menu-btn").setAttribute("aria-expanded", String(open));
  });
  addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#app").classList.contains("menu-open")) $("#app").classList.remove("menu-open"); });
  $("#refresh-btn").addEventListener("click", () => route());
  addEventListener("beforeunload", (e) => { if (unsavedOnClose() || leaveGuard?.()) e.preventDefault(); });
  try {
    meta = await api("/api/meta");
  } catch (e) {
    $("#view").append(note(`The dashboard server is not answering: ${e.message}`, "bad"));
    return;
  }
  onJob(jobIndicator, {global: true});
  onJob(announceJob, {global: true});
  jobIndicator(job.snapshot);
  addEventListener("hashchange", route);
  await pollJob();
  route();
}

boot();
