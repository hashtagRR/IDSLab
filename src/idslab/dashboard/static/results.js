// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// An experiment's Runs, Results, Report and Files pages. Everything shown is read from the run's
// own files (summary.json per unit, report.json, report.html); nothing is estimated here, and
// no interval is pooled across units that the report did not compute.

import {
  el, api, q, enc, fileUrl, fmt, dur, ago, card, cardBody, table, empty, note, toast, field, badge,
  job, onJob, startJob, objective, metricLabel, systemLabel, pretty,
} from "./core.js";
import {barChart, cascadeMap} from "./charts.js";
import {filesBody} from "./views.js";

const unitLabel = (u) => `seed ${u.seed}` + (u.family ? `, held out ${u.family}` : "");
const href = (key, tab, sub) => `#/e/${enc(key)}/${tab}` + (sub ? `/${sub}` : "");
const SYSTEM_PLAIN = {
  cascade: "Cascade",
  B1_first_stage: "First stage alone (B1)",
  B2_first_stage_val_cutoff: "First stage at its best VAL cutoff (B2)",
  budget_matched_first_stage: "First stage at the cascade's alarm budget",
  joint_check: "Best VAL combination (joint check)",
};
const sysName = (k) => SYSTEM_PLAIN[k] || systemLabel(k);

function notRun(box, ctx) {
  box.append(card("No results yet", null, empty("This experiment has not been run.",
    ctx.info?.config ? el("a", {class: "btn primary", href: href(ctx.key, "setup", "review"), text: "Review and run"}) : null)));
}

// the reason a command cannot start from this page, or null
function blocked(ctx, what, report) {
  const {info} = ctx;
  if (info.kind === "output") return `${what} needs a config: ${info.reason}.`;
  if (info.validation && !info.validation.ok) return `${what} needs a config that loads; ${info.config} has problems (see Setup).`;
  if (report && info.output?.reportable_units === 0) {
    return `${what} needs finished units of this config and the current evaluation, and there are none yet. Run the experiment first.`;
  }
  if (job.snapshot?.state === "running") return "Another job is running; one job runs at a time.";
  return null;
}
function commandButton(ctx, label, what, body, {primary = false, report = false} = {}) {
  const btn = el("button", {class: `btn${primary ? " primary" : ""}`, type: "button", text: label});
  const why = el("span", {class: "muted small"});
  const refresh = () => {
    const r = blocked(ctx, what, report);
    btn.disabled = Boolean(r);
    why.textContent = r || "";
  };
  btn.addEventListener("click", async () => {
    try { await startJob(body()); } catch (e) { toast(e.message); }
  });
  onJob(refresh);
  refresh();
  return el("span", {class: "row"}, btn, why);
}

// ---------- Runs ----------

export async function runsTab(box, ctx) {
  const {info} = ctx;
  const o = info.output;
  if (!o) { notRun(box, ctx); return; }
  const goal = o.report_full?.objective || o.units.find((u) => u.objective)?.objective;
  const f = objective(goal);
  const rows = o.units.map((u) => ({u, cells: [
    el("a", {class: "link", href: `#/unit/${enc(u.path)}`, text: unitLabel(u)}), pretty(u.mode),
    u.done ? badge("Finished", "green") : badge("Stopped before the end", "amber"),
    f.value(u.cascade), f.value(u.B1_first_stage),
    u.cascade != null && u.B1_first_stage != null ? f.short(u.cascade - u.B1_first_stage) : "n/a",
    u.runtime_seconds != null ? dur(u.runtime_seconds) : "n/a", u.finished ? ago(u.finished) : "n/a",
  ]}));
  const unfinished = o.units.filter((u) => !u.done).length;
  box.append(
    card("Run units", `One unit per seed${info.output.modes.includes("heldout_family") ? " and held-out family" : ""}. TEST ${metricLabel(goal)}.`,
      el("div", {}, el("div", {class: "card-body flush"}, table(
        ["Unit", "Protocol", "State", "Cascade", "First stage alone", "Difference", "Runtime", "Finished"], rows,
        {onRow: (x) => { location.hash = `#/unit/${enc(x.u.path)}`; }, numeric: [3, 4, 5, 6]})),
      unfinished ? cardBody(note(`${unfinished} unit${unfinished === 1 ? "" : "s"} stopped before the end (no DONE file). ` +
        "Running again redoes them and reuses the finished ones.", "warn")) : null)),
    card("Run again", null, cardBody(el("div", {class: "stack"},
      el("p", {class: "small", text: "A new run reuses every unit whose config, data, split and package version fingerprints match, " +
        "so only new or changed units are computed. This is how a stopped run continues; work inside a unit is not checkpointed."}),
      el("div", {class: "row wrap"},
        info.kind === "config" ? el("a", {class: "btn primary", href: href(ctx.key, "setup", "review"), text: "Review and run"})
          : el("span", {class: "muted small", text: `Running again needs a config: ${info.reason}.`}),
        commandButton(ctx, "Export splits", "Exporting splits", () => ({command: "export-splits", config: info.config, out: "splits"}))),
      el("p", {class: "hint", text: "Export splits writes the row ids of every split per unit, for models trained outside IDSLab whose scores come back as score files."})))));
}

// ---------- Results ----------

function interpretation(d, f, n) {
  const above = d.ci_above_zero, below = d.ci_below_zero, m = d.diff.mean;
  const mean = `mean difference ${f.diff(m)}`;
  if (!n) return "No units to compare.";
  if (above === n) return `Better than the first stage alone in every unit: the 95% bootstrap interval is above zero in ${n} of ${n} (${mean}).`;
  if (below === n) return `Worse than the first stage alone in every unit: the interval is below zero in ${n} of ${n} (${mean}).`;
  if (above && below) return `Mixed: better in ${above} unit${above === 1 ? "" : "s"} and worse in ${below} of ${n} (${mean}). No consistent difference.`;
  if (above) return `Better in ${above} of ${n} units; in the others the interval includes zero. That is not a consistent improvement (${mean}).`;
  if (below) return `Worse in ${below} of ${n} units; in the others the interval includes zero (${mean}).`;
  return `No unit shows a difference that the bootstrap separates from zero (${mean}${m > 0 ? "; a positive mean alone does not show the cascade is better" : ""}).`;
}

export async function resultsTab(box, ctx) {
  const {info} = ctx;
  const o = info.output;
  if (!o) { notRun(box, ctx); return; }
  const rep = o.report_full;
  if (!rep) {
    box.append(card("No report yet", null, cardBody(el("div", {class: "stack"},
      el("p", {text: o.units_done ? `${o.units_done} unit${o.units_done === 1 ? "" : "s"} finished. The results page reads the report, which has not been built.`
        : "No unit has finished yet."}),
      o.units_done ? commandButton(ctx, "Build the report", "Building the report", () => ({command: "report", config: info.config, out: o.out_root}), {primary: true, report: true}) : null))));
    return;
  }
  const goal = rep.objective, f = objective(goal);
  const st = rep.systems_test;
  const n = rep.units.length;
  const held = rep.mode === "heldout_family";
  const spread = held ? "spread over units (seeds and held-out families together)" : `spread over ${rep.seeds.length} seed${rep.seeds.length === 1 ? "" : "s"}`;
  const b1 = rep.cascade_minus_control?.B1_first_stage;
  const sections = [];
  const nav = el("div", {class: "seg section-nav", role: "navigation", "aria-label": "Result sections"});
  const section = (id, title, sub, body) => {
    const c = card(title, sub, body);
    c.id = `res-${id}`;
    sections.push([id, title, c]);
    return c;
  };
  if (o.report_behind) {
    box.append(el("div", {class: "note warn row"}, el("span", {text: "Some finished units are newer than the report, so these figures may be out of date."}),
      el("span", {class: "spacer"}), commandButton(ctx, "Rebuild the report", "Rebuilding the report", () => ({command: "report", config: info.config, out: o.out_root}), {report: true})));
  }

  // summary
  const m = (sys) => st[sys]?.[goal];
  section("summary", "Summary", `TEST ${f.label}, mean over ${n} unit${n === 1 ? "" : "s"}, against the first stage alone`, cardBody(el("div", {class: "stack"},
    el("div", {class: "grid-kpi"},
      kpi(`Cascade`, f.value(m("cascade")?.mean), m("cascade")?.sd != null ? `sd ${f.isRate ? (m("cascade").sd * 100).toFixed(2) + " pp" : fmt(m("cascade").sd)} (${spread})` : null),
      kpi("First stage alone (B1)", f.value(m("B1_first_stage")?.mean), "the entry model on its own, at the cascade's choices"),
      b1 ? kpi("Difference", f.diff(b1.diff.mean), `bootstrap interval above zero in ${b1.ci_above_zero} of ${n}, below in ${b1.ci_below_zero}`,
        b1.ci_above_zero === n ? "good" : b1.ci_below_zero === n ? "bad" : "") : null),
    b1 ? el("p", {class: "lead", text: interpretation(b1, f, n)}) : null,
    f.isCost ? el("p", {class: "hint", text: "The cost objective is stored as the negative average cost per flow; it is shown here as a cost, so lower is better."}) : null,
    f.isRate ? el("p", {class: "hint", text: "Differences of rates are in percentage points (pp): 95.0% against 94.0% is +1.00 pp, not a 1% relative change."}) : null)));

  // stage effects
  const removal = rep.stage_removal || {}, steps = rep.prefix_steps || {};
  const stages = [...new Set([...Object.keys(removal), ...Object.keys(steps)])];
  const pmd = (v) => (v?.mean == null ? "n/a" : `${f.short(v.mean)}${v.sd != null ? ` (sd ${f.isRate ? (v.sd * 100).toFixed(2) + " pp" : fmt(v.sd)})` : ""}`);
  const sc = rep.stage_cost;
  section("stages", "Stage effects", "What each stage adds, on TEST", el("div", {},
    el("div", {class: "card-body flush"}, table(["Stage", "Cascade minus the cascade without it", "Change when it is added in order"],
      stages.map((s) => [s, pmd(removal[s]), pmd(steps[s])]), {numeric: [1, 2]})),
    sc ? el("div", {class: "card-body flush"}, table(["Stage", "Share of TEST flows reaching it", "Scoring time per 1,000 flows", "Declared cost"],
      Object.entries(sc.stages).map(([s, r]) => [s, r.reach_share?.mean != null ? `${(r.reach_share.mean * 100).toFixed(1)}%` : "n/a",
        r.ms_per_1k?.mean != null ? `${fmt(r.ms_per_1k.mean, 3)} ms` : "n/a", String(r.declared_cost)]), {numeric: [1, 2, 3]})) : null,
    sc ? cardBody(el("p", {class: "small", text: `Expected cost per flow: cascade ${fmt(sc.cascade_cost?.mean, 3)}, first stage alone ${fmt(sc.first_stage_cost, 3)}.`})) : null));

  // unseen attacks and types
  if (rep.unseen || rep.types) {
    const parts = [];
    if (rep.unseen) {
      parts.push(el("div", {class: "card-body flush"}, table(["Unseen attacks", "Runs", "Detected by the cascade", "By the first stage", "False-alarm rate (FPR)", "Frontier gap"],
        Object.entries(rep.unseen).map(([k, g]) => [k, String(g.runs), pct(g.recall_unseen.cascade), pct(g.recall_unseen.B1_first_stage),
          pct(g.fpr_cascade), `${fmt(g.gap_interp?.mean)} (interval above zero in ${g.gap_ci_above_zero} of ${g.runs})`]), {numeric: [1, 2, 3, 4]})));
    }
    if (rep.types) {
      const t = rep.types;
      parts.push(cardBody(el("p", {class: "small", text: `Attack type on true attacks (${t.seeds_with_types} of ${n} units with an active type stage): ` +
        `accuracy ${pct(t.accuracy)}, macro F1 ${fmt(t.macro_f1?.mean)}. Untyped and uncertain flows count as wrong.`})),
      el("div", {class: "card-body flush"}, table(["Type", "F1", "Support"], Object.keys(t.per_type_f1).map((c) =>
        [c, fmt(t.per_type_f1[c].mean), fmt(t.per_type_support[c].mean, 0)]), {numeric: [1, 2]})));
    }
    section("unseen", "Unseen attacks and types", null, el("div", {}, parts));
  }

  // comparisons and uncertainty
  const systems = Object.keys(st);
  const rateKeys = ["accuracy", "balanced_accuracy", "f1", "recall", "precision"].filter((k) => st.cascade?.[k]);
  const chartKeys = [...new Set([f.isRate && !goal.startsWith("recall_at") ? goal : null, ...rateKeys])].filter(Boolean).slice(0, 4);
  const cols = [goal, "recall", "fpr", "precision", "mcc"].filter((k, i, a) => a.indexOf(k) === i && st.cascade?.[k]);
  const val = (k, v) => (v?.mean == null ? "n/a" : k === goal ? f.value(v.mean) : ["recall", "fpr", "precision"].includes(k) ? pct(v) : fmt(v.mean));
  section("uncertainty", "Comparisons and uncertainty", null, el("div", {},
    el("div", {class: "card-body flush"}, table(["System", ...cols.map(metricLabel)],
      systems.map((s) => [sysName(s), ...cols.map((k) => val(k, st[s][k]))]), {numeric: cols.map((_, i) => i + 1)})),
    el("div", {class: "card-body flush"}, table(["Cascade minus", "Mean difference", `sd (${held ? "seeds and families" : "seeds"})`, "Interval above zero", "Interval below zero"],
      Object.entries(rep.cascade_minus_control || {}).map(([c, d]) => [sysName(c),
        // coloured only when the bootstrap separates the difference from zero in every unit
        el("span", {class: d.ci_above_zero === d.diff.n ? "pos" : d.ci_below_zero === d.diff.n ? "neg" : "", text: f.short(d.diff.mean)}),
        d.diff.sd == null ? "n/a" : f.isRate ? `${(d.diff.sd * 100).toFixed(2)} pp` : fmt(d.diff.sd),
        `${d.ci_above_zero} of ${d.diff.n}`, `${d.ci_below_zero} of ${d.diff.n}`]), {numeric: [1, 2, 3, 4]})),
    cardBody(el("div", {class: "stack"},
      el("ul", {class: "plain-list small"},
        el("li", {}, el("b", {text: "Seed variation: "}), held
          ? "the sd columns mix seeds and held-out families, because each unit is one seed with one family held out."
          : "the sd columns are the spread of the unit means over seeds (new splits and refits each time)."),
        el("li", {}, el("b", {text: "Bootstrap: "}), "inside each unit, a paired bootstrap over the TEST flows gives a 95% interval for the difference. " +
          "The counts say in how many units that interval lies above or below zero. No interval is pooled across units."),
        held ? el("li", {}, el("b", {text: "Family variation: "}), "the Unseen attacks section breaks results down by held-out family.") : null,
        el("li", {}, el("b", {text: "Frontier gap: "}), `detection rate above the first stage's ROC hull at the cascade's false-alarm rate; mean ${fmt(rep.frontier_gap?.interp?.mean)}, ` +
          `interval above zero in ${rep.frontier_gap?.ci_above_zero ?? 0} of ${n} units.`)),
      chartKeys.length ? el("div", {class: "chart-box"}, barChart(chartKeys.map((k) => ({label: metricLabel(k), values: systems.filter((s) => st[s][k])
        .map((s) => ({name: sysName(s), mean: st[s][k]?.mean, sd: st[s][k]?.sd}))})))) : null,
      chartKeys.length ? el("p", {class: "hint", text: `Error bars are one sd (${spread}), not confidence intervals.`}) : null))));

  // reproducibility
  const fb = Object.entries(rep.fallbacks || {}).flatMap(([u, list]) => list.map((x) => `unit ${u}: ${x}`));
  section("repro", "Reproducibility", null, cardBody(el("div", {class: "stack"},
    el("dl", {class: "kv"},
      el("dt", {text: "IDSLab version"}), el("dd", {text: rep.idslab_version}),
      el("dt", {text: "Seeds"}), el("dd", {text: rep.seeds.join(", ")}),
      el("dt", {text: "Units in the report"}), el("dd", {text: rep.units.join(", ")}),
      el("dt", {text: "Protocol"}), el("dd", {text: pretty(rep.mode)}),
      el("dt", {text: "First stage for controls"}), el("dd", {text: rep.first_stage}),
      rep.joint_check ? [el("dt", {text: "Joint check"}), el("dd", {text: `a better VAL combination than greedy selection exists in ${rep.joint_check.differs} of ${rep.joint_check.runs} runs` +
        (rep.joint_check.skipped ? `; ${rep.joint_check.skipped} skipped (too many combinations)` : "")})] : null,
      el("dt", {text: "Config"}), el("dd", {text: info.changed_since_run ? "changed since these units ran" : info.kind === "config" ? "unchanged since these units ran" : "not in the workspace"})),
    rep.seeds_left_out?.length ? note(`Left out of the report: ${rep.seeds_left_out.map((x) => typeof x === "string" ? x : JSON.stringify(x)).join("; ")}`, "warn") : null,
    fb.length ? el("details", {class: "trace"}, el("summary", {text: `Fallbacks and split notes (${fb.length})`}), el("ul", {class: "plain-list small"}, fb.map((x) => el("li", {text: x})))) : null,
    el("details", {class: "trace"}, el("summary", {text: "Statements in the report"}), el("ul", {class: "plain-list small"}, (rep.statements || []).map((x) => el("li", {text: x})))))));

  nav.append(...sections.map(([id, title, c]) => el("button", {type: "button", text: title, onclick: () => { c.scrollIntoView({behavior: "smooth", block: "start"}); c.querySelector("h3")?.focus?.(); }})));
  box.append(nav, ...sections.map(([, , c]) => c));
}

const pct = (v) => (v?.mean == null ? "n/a" : `${(v.mean * 100).toFixed(2)}%`);
function kpi(label, value, sub, cls = "") {
  return el("div", {class: `card metric ${cls}`}, el("div", {class: "label", text: label}), el("div", {class: "value", text: value}),
    sub ? el("div", {class: "sub", text: sub}) : null);
}

// ---------- Report ----------

export async function reportTab(box, ctx) {
  const {info} = ctx;
  const o = info.output;
  if (!o) { notRun(box, ctx); return; }
  const rebuild = commandButton(ctx, o.has_report ? "Rebuild report" : "Build the report", "Building the report",
    () => ({command: "report", config: info.config, out: o.out_root}), {primary: !o.has_report, report: true});
  if (!o.has_report) {
    box.append(card("No report yet", null, cardBody(el("div", {class: "stack"},
      el("p", {text: o.units_done ? `${o.units_done} of ${o.units_total} units finished. The report aggregates finished units into report.md, report.json and report.html.`
        : "No unit has finished yet, so there is nothing to report."}),
      o.report_files.json || o.report_files.md ? note("Some report files exist but report.html is missing; rebuild to write all three.", "warn") : null,
      o.units_done ? rebuild : null))));
  } else {
    const file = (ext, label) => (o.report_files[ext] ? el("a", {class: "btn small", href: fileUrl(`${o.path}/report.${ext}`), target: "_blank", rel: "noopener", text: label}) : null);
    box.append(
      el("div", {class: "row wrap"},
        el("a", {class: "btn primary", href: fileUrl(`${o.path}/report.html`), target: "_blank", rel: "noopener", text: "View full report"}),
        file("md", "report.md"), file("json", "report.json"), el("span", {class: "spacer"}), rebuild),
      el("p", {class: "muted small", text: `Built ${ago(o.report_mtime)} from ${o.report_units} of ${o.units_done} finished unit${o.units_done === 1 ? "" : "s"}.`}),
      o.report_behind ? note("Finished units are newer than this report, or not included in it. Rebuild it to include them.", "warn") : "",
      o.units_done < o.units_total ? note(`${o.units_total - o.units_done} unit${o.units_total - o.units_done === 1 ? "" : "s"} did not finish and ${o.units_total - o.units_done === 1 ? "is" : "are"} not in the report.`, "warn") : "",
      el("iframe", {class: "report-frame", src: fileUrl(`${o.path}/report.html`), title: `Report of ${info.name}`, sandbox: ""}));
  }
  const frozen = el("div");
  box.append(card("Frozen cascade", "The choices made on VAL for one unit, drawn on the routes that unit ran with.", cardBody(frozen)));
  await frozenGraph(frozen, ctx);
}

async function frozenGraph(box, ctx) {
  const done = ctx.info.output.units.filter((u) => u.done);
  if (!done.length) { box.append(empty("No finished unit yet.")); return; }
  const pick = el("select", {class: "select", "aria-label": "Unit"}, done.map((u, i) => el("option", {value: String(i), text: unitLabel(u)})));
  const area = el("div", {class: "stack"});
  const v = ctx.info.validation;
  const show = async () => {
    const u = done[Number(pick.value)];
    const {summary} = await api("/api/unit" + q({path: u.path}));
    const fz = summary.frozen_configuration;
    let map = null, source;
    const box = (m, n) => el("div", {style: {maxWidth: `${Math.min(1000, 320 * n)}px`}}, m);
    if (summary.cascade) {
      map = box(cascadeMap(summary.cascade, fz), summary.cascade.stages.length);
      source = "Drawn from the routes stored with this unit.";
    } else if (v?.ok && v.config.fingerprint === summary.config_fingerprint) {
      map = box(cascadeMap(v.config, fz), v.config.stages.length);
      source = "Drawn from the current config, which is unchanged since this unit ran (the fingerprints match).";
    } else {
      source = "This unit does not store its routes (an earlier IDSLab version wrote it) and the config has changed or is missing, " +
        "so the routes it ran with cannot be drawn. Its frozen choices are listed instead.";
    }
    area.replaceChildren(note(source, map ? "info" : "warn"), map || "",
      table(["Stage", "State", "Candidate", "Decision", "Value", "Threshold"], Object.entries(fz).map(([name, x]) => [name,
        x.off ? badge("Off", "amber") : badge("On", "green"), x.candidate || "n/a", x.decision_mode || "n/a", fmt(x.value, 3), fmt(x.threshold, 4)]), {numeric: [4, 5]}));
  };
  pick.addEventListener("change", show);
  box.append(el("div", {class: "stack"}, field("Unit", pick), area));
  await show();
}

// ---------- Files ----------

export async function filesTab(box, ctx) {
  const {info} = ctx;
  if (info.config) {
    const layout = await api("/api/layout" + q({path: info.config}));
    box.append(card("Config", null, cardBody(el("div", {class: "row wrap"},
      el("a", {class: "btn small", href: fileUrl(info.config), target: "_blank", rel: "noopener", text: info.config}),
      el("a", {class: "btn small", href: fileUrl(info.config, true), text: "Download"}),
      Object.keys(layout.positions || {}).length ? el("a", {class: "btn small", href: fileUrl(`${info.config}.layout.json`), target: "_blank", rel: "noopener",
        text: "Builder layout"}) : null))));
  }
  const o = info.output;
  if (!o) { box.append(card("Run output", null, empty("Nothing written yet."))); return; }
  box.append(card("Units", "Open a unit for its candidates, selection trace and notes.", el("div", {class: "card-body flush"},
    table(["Unit", "State", "Folder"], o.units.map((u) => [el("a", {class: "link", href: `#/unit/${enc(u.path)}`, text: unitLabel(u)}),
      u.done ? badge("Finished", "green") : badge("Stopped", "amber"), el("a", {class: "link mono small", href: `#/files/${enc(u.path)}`, text: u.path})])))));
  await filesBody(box, o.path, (p) => `#/files/${enc(p)}`);
}
