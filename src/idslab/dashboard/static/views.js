// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Views over results and jobs: overview, runs, one run, one unit, the job log, example data, files.

import {
  el, api, q, enc, fileUrl, fmt, signed, dur, ago, bytes, pretty, systemLabel, stateBadge, badge, card, cardBody,
  pageHead, metric, table, empty, note, field, checkbox, logView, fillLog, job, onJob, startJob, toast,
} from "./core.js";
import {barChart, cascadeMap} from "./charts.js";

const runHref = (path, tab) => `#/run/${enc(path)}` + (tab ? `/${tab}` : "");
const unitLabel = (u) => `seed ${u.seed}` + (u.family ? `, held out ${u.family}` : "");

// ---------- overview ----------

export async function overviewView(view, meta) {
  const [configs, runs] = await Promise.all([api("/api/configs"), api("/api/runs")]);
  const units = runs.reduce((a, r) => a + r.units_done, 0);
  const jobCard = el("div");
  const showJob = (s) => {
    if (!s || s.state === "idle") {
      jobCard.replaceChildren(empty("No job has run since the dashboard started."));
      return;
    }
    jobCard.replaceChildren(el("div", {class: "stack"},
      el("div", {class: "row"}, stateBadge(s.state), el("span", {class: "mono small wrap", text: `idslab ${s.command}`}),
        el("span", {class: "muted small", text: s.config || s.target || ""}), el("span", {class: "spacer"}),
        el("a", {class: "btn small", href: "#/job", text: "Open log"})),
      logView(s.log.slice(-8))));
  };
  showJob(job.snapshot);
  onJob(showJob);

  view.append(pageHead("Overview", null, [
    el("a", {class: "btn", href: "#/new", text: "New config"}),
    el("a", {class: "btn primary", href: "#/command/run", text: "Run a config"}),
  ], [badge("Workspace", "blue"), el("span", {class: "mono small wrap", text: meta.workspace})]));
  view.append(el("div", {class: "grid-kpi"},
    metric("Configs", String(configs.length), "YAML files with entry and stages"),
    metric("Experiments", String(runs.length), "run outputs found in the workspace"),
    metric("Finished units", String(units), "seeds (and held-out families) with DONE"),
    metric("IDSLab", meta.version, Object.entries(meta.extras).filter(([, v]) => v).map(([k]) => k).join(", ") ||
      "no optional extras installed")));
  if (!configs.length && !runs.length) {
    view.append(card("Getting started", null, cardBody(el("ol", {class: "stack", style: {paddingLeft: "18px", margin: 0}},
      el("li", {}, "Write the synthetic dataset and three reference configs: ",
        el("a", {class: "link", href: "#/example", text: "Example data"}), "."),
      el("li", {}, "Open a config, check the cascade preview, then start a run from its Commands panel."),
      el("li", {}, "Read the result in Runs: headline figures, the controls and the full report page.")))));
  }
  const latest = runs.slice(0, 8).map((r) => ({path: r.path, cells: runCells(r)}));
  view.append(el("div", {class: "grid-2"},
    card("Latest runs", null, el("div", {class: "card-body flush"}, latest.length
      ? table(["Experiment", "Units", "Cascade", "B1", "Updated"], latest,
        {onRow: (r) => { location.hash = runHref(r.path); }, numeric: [1, 2, 3]})
      : empty("No run output yet."))),
    card("Current job", null, cardBody(jobCard))));
}

function runCells(r) {
  const h = r.report;
  const cas = h?.cascade, b1 = h?.B1_first_stage;
  const cls = cas != null && b1 != null ? (cas > b1 ? "pos" : cas < b1 ? "neg" : "") : "";
  return [
    el("div", {}, el("a", {class: "link", href: runHref(r.path), text: r.name}),
      el("div", {class: "muted small mono wrap", text: r.path})),
    `${r.units_done}/${r.units_total}`,
    el("span", {class: cls, text: fmt(cas)}),
    fmt(b1),
    ago(r.updated),
  ];
}

// ---------- runs ----------

export async function runsView(view) {
  const runs = await api("/api/runs");
  view.append(pageHead("Runs", "Every run output in the workspace (folders holding <mode>/seed_<s>). " +
    "Cascade and B1 are the TEST objective, mean over units, from report.json."));
  if (!runs.length) {
    view.append(card("No runs yet", null, empty("Start a run from a config.",
      el("a", {class: "btn primary", href: "#/command/run", text: "Run a config"}))));
    return;
  }
  const rows = runs.map((r) => ({path: r.path, cells: [
    ...runCells(r).slice(0, 1),
    el("div", {class: "row"}, r.modes.map((m) => badge(pretty(m)))),
    r.report?.objective || "n/a",
    ...runCells(r).slice(1, 4),
    r.has_report ? badge("Report", "green") : badge("No report", "amber"),
    ago(r.updated),
  ]}));
  view.append(card(`${runs.length} experiment${runs.length === 1 ? "" : "s"}`, null,
    el("div", {class: "card-body flush"}, table(
      ["Experiment", "Mode", "Objective", "Units", "Cascade", "B1", "Report", "Updated"], rows,
      {onRow: (r) => { location.hash = runHref(r.path); }, numeric: [3, 4, 5]}))));
}

// ---------- one run ----------

export async function runView(view, meta, path, tab, setCrumbs) {
  const r = await api("/api/run" + q({path}));
  tab = tab || "overview";
  setCrumbs([["Runs", "#/runs"], [r.name]]);
  const rep = r.report_full;
  const goal = rep?.objective;
  const config = r.configs[0];

  const reportBtn = el("button", {class: "btn", type: "button", text: "Rebuild report"});
  reportBtn.addEventListener("click", async () => {
    if (!config) { location.hash = "#/command/report"; return; }
    try { await startJob({command: "report", config, out: r.out_root}); } catch (e) { toast(e.message); }
  });
  view.append(pageHead(r.name, null, [
    r.has_report ? el("a", {class: "btn", href: fileUrl(`${r.path}/report.html`), target: "_blank", rel: "noopener",
      text: "Open report page"}) : null,
    reportBtn,
    config ? el("a", {class: "btn primary", href: `#/command/run/${enc(config)}`, text: "Run again"}) : null,
  ], [...r.modes.map((m) => badge(pretty(m), "blue")), badge(`${r.units_done}/${r.units_total} units done`,
    r.units_done === r.units_total ? "green" : "amber"), el("span", {class: "mono small muted wrap", text: r.path})]));
  if (!config) {
    view.append(note(`No config named ${r.name} in the workspace; the report and run actions need one. ` +
      "Use Rebuild report with the config file.", "warn"));
  }

  const tabs = [["overview", "Overview"], ["report", "Report page"], ["units", "Units"], ["files", "Files"]];
  view.append(el("nav", {class: "tabs"}, tabs.map(([k, label]) =>
    el("a", {href: runHref(path, k), class: k === tab ? "active" : null, text: label}))));

  if (tab === "report") {
    view.append(r.has_report
      ? el("iframe", {class: "report-frame", src: fileUrl(`${r.path}/report.html`), title: "Report", sandbox: ""})
      : card("No report yet", null, empty("Rebuild the report once a seed has finished.")));
    return;
  }
  if (tab === "units") {
    const rows = r.units.map((u) => ({u, cells: [
      el("a", {class: "link", href: `#/unit/${enc(u.path)}`, text: unitLabel(u)}), pretty(u.mode),
      u.done ? badge("Done", "green") : badge("Not finished", "amber"), fmt(u.cascade), fmt(u.B1_first_stage),
      u.cascade != null && u.B1_first_stage != null ? signed(u.cascade - u.B1_first_stage) : "n/a",
      u.runtime_seconds != null ? dur(u.runtime_seconds) : "n/a", u.finished ? ago(u.finished) : "n/a",
    ]}));
    view.append(card("Units", `TEST ${r.units[0]?.objective || "objective"} per unit.`,
      el("div", {class: "card-body flush"}, table(
        ["Unit", "Mode", "State", "Cascade", "B1", "Cascade minus B1", "Runtime", "Finished"], rows,
        {onRow: (x) => { location.hash = `#/unit/${enc(x.u.path)}`; }, numeric: [3, 4, 5, 6]}))));
    return;
  }
  if (tab === "files") {
    await filesBody(view, r.path, (p) => `#/files/${enc(p)}`);
    return;
  }

  // overview tab
  if (!rep) {
    view.append(card("No report.json yet", null, empty("The headline figures come from the report. Rebuild it.")));
  } else {
    const st = rep.systems_test;
    const m = (sys, k = goal) => st[sys]?.[k];
    const diff = (c) => rep.cascade_minus_control?.[c]?.diff;
    const fg = rep.frontier_gap?.interp;
    const pm = (v) => (v ? `${fmt(v.mean)} ± ${fmt(v.sd)}`.replace(" ± n/a", "") : "n/a");
    view.append(el("div", {class: "grid-kpi"},
      metric(`Cascade ${goal}`, fmt(m("cascade")?.mean), `sd ${fmt(m("cascade")?.sd)} over ${m("cascade")?.n} units`),
      metric("Minus B1 first stage", signed(diff("B1_first_stage")?.mean),
        `bootstrap CI above 0 in ${rep.cascade_minus_control?.B1_first_stage?.ci_above_zero ?? 0} units`,
        (diff("B1_first_stage")?.mean ?? 0) > 0 ? "good" : (diff("B1_first_stage")?.mean ?? 0) < 0 ? "bad" : ""),
      metric("Minus B2 VAL cutoff", signed(diff("B2_first_stage_val_cutoff")?.mean),
        `bootstrap CI above 0 in ${rep.cascade_minus_control?.B2_first_stage_val_cutoff?.ci_above_zero ?? 0} units`),
      metric("Frontier gap", signed(fg?.mean), `TPR over the first-stage ROC hull at the cascade FPR; CI above 0 in ` +
        `${rep.frontier_gap?.ci_above_zero ?? 0} units`),
      rep.joint_check ? metric("Joint check", rep.joint_check.differs_from_greedy ? "differs" : "agrees",
        "greedy against every combination on VAL") : null));

    const systems = Object.keys(st);
    // the chart's axis is 0 to 1, so an objective outside that range (a cost) is left to the tables
    const unit = ["accuracy", "mcc", "f1", "balanced_accuracy", "recall", "precision"];
    const metrics = [...new Set([unit.includes(goal) ? goal : "accuracy", "mcc", "recall", "precision"])]
      .filter((k) => st.cascade?.[k]);
    view.append(el("div", {class: "grid-2"},
      card("Systems on TEST", "Mean over units; error bars are one sd.", cardBody(barChart(
        metrics.map((k) => ({label: k, values: systems.map((s) => ({name: systemLabel(s), mean: st[s][k]?.mean,
          sd: st[s][k]?.sd}))}))))),
      card("Cascade minus each control", `${goal}, mean over units`, el("div", {class: "card-body flush"}, table(
        ["Control", "Difference", "sd", "CI above 0", "CI below 0"],
        Object.entries(rep.cascade_minus_control || {}).map(([c, d]) => [systemLabel(c),
          el("span", {class: d.diff.mean > 0 ? "pos" : d.diff.mean < 0 ? "neg" : "", text: signed(d.diff.mean)}),
          fmt(d.diff.sd), `${d.ci_above_zero} of ${d.diff.n}`, `${d.ci_below_zero} of ${d.diff.n}`]),
        {numeric: [1, 2, 3, 4]})))));

    const removal = rep.stage_removal || {}, steps = rep.prefix_steps || {};
    const stages = [...new Set([...Object.keys(removal), ...Object.keys(steps)])];
    view.append(el("div", {class: "grid-2"},
      card("Stages", "Cascade minus the cascade with the stage off, and the step when the stage is added in order.",
        el("div", {class: "card-body flush"}, table(["Stage", "Removal effect", "Prefix step"],
          stages.map((s) => [s, pm(removal[s]), pm(steps[s])]), {numeric: [1, 2]}))),
      card("Systems table", `TEST ${goal} and error rates`, el("div", {class: "card-body flush"}, table(
        ["System", goal, "Recall", "FPR", "MCC"],
        systems.map((s) => [systemLabel(s), pm(st[s][goal]), fmt(st[s].recall?.mean), fmt(st[s].fpr?.mean),
          fmt(st[s].mcc?.mean)]), {numeric: [1, 2, 3, 4]})))));
    const sc = rep.stage_cost;
    if (sc) {
      view.append(card("Stage cost", "Share of TEST flows reaching each stage, measured scoring time, declared cost.",
        el("div", {}, el("div", {class: "card-body flush"}, table(["Stage", "Flows reaching", "ms per 1,000 flows", "Declared cost"],
          Object.entries(sc.stages).map(([st, r]) => [st, pm(r.reach_share), r.ms_per_1k ? pm(r.ms_per_1k) : "n/a",
            String(r.declared_cost)]), {numeric: [1, 2, 3]})),
        cardBody(el("p", {class: "small", style: {margin: 0}, text: `Expected cost per flow: cascade ${pm(sc.cascade_cost)}, ` +
          `first stage alone ${sc.first_stage_cost}. Scoring time per 1,000 flows: cascade ${pm(sc.cascade_ms_per_1k)} ms, ` +
          `first stage alone ${sc.first_stage_ms_per_1k ? pm(sc.first_stage_ms_per_1k) : "n/a"} ms.`})))));
    }
    if (rep.seeds_left_out?.length) {
      view.append(note(`Left out of the report: ${rep.seeds_left_out.map((x) => typeof x === "string" ? x :
        JSON.stringify(x)).join("; ")}`, "warn"));
    }
  }
  const frozenCard = el("div");
  view.append(card("Frozen cascade", "The configuration chosen on VAL for one unit, on the routes of the config.",
    cardBody(frozenCard)));
  frozenMap(frozenCard, r, config);
}

async function frozenMap(box, r, config) {
  const done = r.units.filter((u) => u.done);
  if (!config || !done.length) {
    box.append(empty(config ? "No finished unit yet." : "Needs the config file to draw the routes."));
    return;
  }
  try {
    const {yaml} = await api("/api/config" + q({path: config}));
    const v = await api("/api/validate", {yaml, path: config});
    if (!v.ok) { box.append(note(`The config does not validate now: ${v.error}`, "warn")); return; }
    const pick = el("select", {class: "select", style: {maxWidth: "260px"}},
      done.map((u, i) => el("option", {value: String(i), text: unitLabel(u)})));
    const area = el("div");
    const show = async () => {
      const u = done[Number(pick.value)];
      const {summary} = await api("/api/unit" + q({path: u.path}));
      const changed = summary.config_fingerprint && summary.config_fingerprint !== v.config.fingerprint;
      area.replaceChildren(
        changed ? note("The config has changed since this unit ran; the routes shown are the current ones.", "warn") : "",
        cascadeMap(v.config, summary.frozen_configuration));
    };
    pick.addEventListener("change", show);
    box.append(el("div", {class: "stack"}, field("Unit", pick), area));
    await show();
  } catch (e) {
    box.append(note(e.message, "bad"));
  }
}

// ---------- one unit ----------

export async function unitView(view, path, setCrumbs) {
  const {summary: s, done} = await api("/api/unit" + q({path}));
  const runPath = path.split("/").slice(0, s.family ? -3 : -2).join("/");
  setCrumbs([["Runs", "#/runs"], [s.config_name, runHref(runPath, "units")], [`seed ${s.seed}`]]);
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

// ---------- job ----------

export function jobView(view) {
  const head = el("div");
  const log = logView([]);
  const info = el("div");
  let full = false;
  const stop = el("button", {class: "btn danger", type: "button", text: "Stop"});
  stop.addEventListener("click", async () => {
    if (!confirm("Stop the running job? Finished seeds stay; the current seed is lost.")) return;
    try { await api("/api/job/stop", {}); } catch (e) { toast(e.message); }
  });
  const fullBtn = el("button", {class: "btn small", type: "button", text: "Full log"});
  fullBtn.addEventListener("click", async () => {
    full = true;
    const s = await api("/api/job");
    fillLog(log, s.log);
  });
  const show = async (s) => {
    if (!s) return;
    if (s.state === "idle") {
      head.replaceChildren(pageHead("Job", "No job has run since the dashboard started."));
      info.replaceChildren(card("Start something", null, empty("Commands start from a config or from Tools.",
        el("a", {class: "btn primary", href: "#/command/run", text: "Run a config"}))));
      log.classList.add("hidden");
      return;
    }
    log.classList.remove("hidden");
    const end = s.finished_at || s.now;
    const target = s.target_kind === "experiment" ? runHref(s.target) : `#/files/${enc(s.target || "")}`;
    head.replaceChildren(pageHead(`idslab ${s.command}`, null,
      [s.state === "running" ? stop : null,
        s.state !== "running" && s.target ? el("a", {class: "btn primary", href: target,
          text: s.target_kind === "experiment" ? "Open results" : "Open folder"}) : null],
      [stateBadge(s.state), el("span", {class: "muted small", text: `${dur(end - s.started_at)} elapsed` +
        (s.returncode != null ? `, exit code ${s.returncode}` : "")})]));
    info.replaceChildren(el("dl", {class: "kv"},
      s.config ? [el("dt", {text: "Config"}), el("dd", {class: "mono", text: s.config})] : null,
      el("dt", {text: "Writes to"}), el("dd", {class: "mono", text: s.target || "n/a"}),
      el("dt", {text: "Log"}), el("dd", {}, `${s.log_lines} lines`,
        s.log_lines > s.log.length && !full ? [" (showing the last ", String(s.log.length), ") ", fullBtn] : null)));
    if (!full || s.state === "running") { full = false; fillLog(log, s.log); }
  };
  view.append(head, card("Live log", null, cardBody(el("div", {class: "stack"}, info, log))));
  show(job.snapshot);
  onJob(show);
}

// ---------- example ----------

export function exampleView(view) {
  const dir = el("input", {class: "input mono", value: "example"});
  const over = checkbox("Overwrite", "replace files that are already there");
  const line = el("pre", {class: "code"});
  const msg = el("div");
  const refresh = () => {
    line.textContent = `idslab example ${dir.value.trim() || "<folder>"}${over.input.checked ? " --overwrite" : ""}`;
  };
  dir.addEventListener("input", refresh);
  over.input.addEventListener("input", refresh);
  refresh();
  const go = el("button", {class: "btn primary", type: "button", text: "Write example"});
  go.addEventListener("click", async () => {
    msg.replaceChildren();
    try { await startJob({command: "example", directory: dir.value.trim(), overwrite: over.input.checked}); }
    catch (e) { msg.append(note(e.message, "bad")); }
  });
  view.append(pageHead("Example data", "A synthetic flow dataset (types Normal, DoS, Scan, Exploit, Worms) and " +
    "the three reference configs, ready to run."),
  card("Write the example", null, cardBody(el("div", {class: "stack"},
    field("Folder", dir, "relative to the workspace; the data goes to <folder>/data"), over.node,
    field("Same as", line), msg, el("div", {class: "row"}, go)))));
}

// ---------- files ----------

export async function filesView(view, path, setCrumbs) {
  setCrumbs([["Files", "#/files"], ...(path ? [[path]] : [])]);
  view.append(pageHead("Files", "Read-only view of the workspace. Text and report pages open in a new tab; " +
    "other files download."));
  await filesBody(view, path, (p) => `#/files/${enc(p)}`);
}

async function filesBody(view, path, dirHref) {
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
