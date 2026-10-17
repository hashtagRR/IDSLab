// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// One run unit in detail, and the read-only file browser.

import {
  el, api, q, enc, fileUrl, fmt, dur, ago, bytes, pretty, systemLabel, badge, card, cardBody,
  pageHead, metric, table, empty, note,
} from "./core.js";

const runHref = (path, tab) => `#/run/${enc(path)}` + (tab ? `/${tab}` : "");

// ---------- one unit ----------

export async function unitView(view, path, setCrumbs) {
  const {summary: s, done} = await api("/api/unit" + q({path}));
  const runPath = path.split("/").slice(0, s.family ? -3 : -2).join("/");
  setCrumbs([["Experiments", "#/"], [s.config_name, runHref(runPath, "units")], [`seed ${s.seed}`]]);
  const ctl = s.controls, goal = ctl.objective;
  view.append(pageHead(`${s.config_name}, seed ${s.seed}`, null, [
    el("a", {class: "btn", href: fileUrl(`${path}/summary.json`), target: "_blank", rel: "noopener", text: "summary.json"}),
    el("a", {class: "btn", href: `#/files/${enc(path)}`, text: "Files"}),
  ], [badge(pretty(s.mode), "blue"), s.family ? badge(`held out ${s.family}`, "amber") : null,
    done ? badge("Done", "green") : badge("Not finished", "amber"), badge(`IDSLab ${s.idslab_version}`)]));

  view.append(el("div", {class: "grid-kpi"},
    metric(`Cascade ${goal}`, fmt(ctl.systems_test.cascade?.[goal])),
    metric(`B1 ${goal}`, fmt(ctl.systems_test.B1_first_stage?.[goal])),
    metric("Runtime", dur(s.runtime_seconds), done?.finished ? `finished ${ago(done.finished)}` : null),
    metric("Split sizes", Object.values(s.split_sizes).map((n) => fmt(n)).join(" / "),
      Object.keys(s.split_sizes).join(" / "))));

  const frozen = Object.entries(s.frozen_configuration).map(([name, f]) => [name,
    f.off ? badge("Off", "amber") : badge("On", "green"), f.candidate || "n/a", f.decision_mode || "n/a",
    fmt(f.value, 3), fmt(f.threshold, 4)]);
  const systems = Object.entries(ctl.systems_test).map(([k, v]) => [systemLabel(k), fmt(v[goal]), fmt(v.recall),
    fmt(v.fpr), fmt(v.precision), fmt(v.mcc)]);
  view.append(el("div", {class: "grid-2"},
    card("Frozen configuration", "Chosen on VAL before TEST was read.", el("div", {class: "card-body flush"},
      table(["Stage", "State", "Candidate", "Decision", "Value", "Threshold"], frozen, {numeric: [4, 5]}))),
    card("Systems on TEST", null, el("div", {class: "card-body flush"},
      table(["System", goal, "Recall", "FPR", "Precision", "MCC"], systems, {numeric: [1, 2, 3, 4, 5]})))));

  const cands = Object.entries(s.candidates).flatMap(([stage, list]) => list.map((c) => [stage, c.label,
    el("span", {class: "mono small", text: c.class}), fmt(c.n_train), fmt(c.n_cal),
    c.cv_score != null ? fmt(c.cv_score) : "n/a", c.fit_seconds != null ? dur(c.fit_seconds) : "n/a",
    c.best_params ? el("span", {class: "mono small wrap", text: JSON.stringify(c.best_params)}) : ""]));
  view.append(card("Candidates", "Every model fitted for this unit.", el("div", {class: "card-body flush"},
    table(["Stage", "Label", "Class", "Train rows", "Cal rows", "CV score", "Fit time", "Best params"], cands,
      {numeric: [3, 4, 5, 6]}))));

  const trace = el("div", {class: "stack"}, s.selection_trace.map((t) => {
    const opts = (t.options || []).map((o) => [o.candidate || "off", fmt(o.value, 3), fmt(o.threshold, 4),
      fmt(o[goal] ?? o.objective)]);
    return el("details", {class: "trace"},
      el("summary", {text: `${t.stage}: ${t.val_rows_reaching} VAL rows reach it; chose ` +
        (t.chosen?.off ? "off" : `${t.chosen?.candidate} at ${fmt(t.chosen?.value, 3)}`)}),
      t.fallbacks?.length ? note(t.fallbacks.join("\n"), "warn") : null,
      table(["Candidate", "Value", "Threshold", `VAL ${goal}`], opts, {numeric: [1, 2, 3]}));
  }));
  view.append(card("Selection trace", "Greedy forward selection in stage order; ties go to off.", cardBody(trace)));
  if (s.data_notes?.length || s.statements?.length) {
    view.append(card("Notes", null, cardBody(el("ul", {class: "stack", style: {margin: 0, paddingLeft: "18px"}},
      [...(s.data_notes || []), ...(s.statements || [])].map((t) => el("li", {class: "small", text: t}))))));
  }
}

// ---------- files ----------

export async function filesView(view, path, setCrumbs) {
  setCrumbs([["Files", "#/files"], ...(path ? [[path]] : [])]);
  view.append(pageHead("Files", "Every file in the workspace, read only. Text and report pages open in a new tab; " +
    "other files download."));
  await filesBody(view, path, (p) => `#/files/${enc(p)}`);
}

export async function filesBody(view, path, dirHref) {
  const d = await api("/api/files" + q({path}));
  const parts = d.path ? d.path.split("/") : [];
  const crumb = el("div", {class: "crumb-path"}, el("a", {href: dirHref(""), text: "workspace"}),
    parts.map((p, i) => [" / ", i === parts.length - 1 ? p
      : el("a", {href: dirHref(parts.slice(0, i + 1).join("/")), text: p})]));
  const rel = (name) => (d.path ? `${d.path}/${name}` : name);
  const rows = d.entries.map((e) => [
    e.dir ? el("a", {class: "link mono", href: dirHref(rel(e.name)), text: e.name + "/"})
      : el("a", {class: "link mono", href: fileUrl(rel(e.name)), target: "_blank", rel: "noopener", text: e.name}),
    e.dir ? "" : bytes(e.size), ago(e.mtime),
    e.dir ? "" : el("a", {class: "btn small", href: fileUrl(rel(e.name), true), text: "Download"}),
  ]);
  view.append(card("Folder", null, el("div", {},
    el("div", {class: "card-body"}, crumb),
    el("div", {class: "card-body flush"}, rows.length ? table(["Name", "Size", "Modified", ""], rows, {numeric: [1]})
      : empty("Empty folder.")))));
}
