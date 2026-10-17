// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Compare finished experiments, and write and run sweeps (one base config, several variants).

import {
  el, api, q, enc, fmt, signed, ago, card, cardBody, pageHead, table, empty, note, toast, field, checkbox,
  badge, startJob, job, onJob, store,
} from "./core.js";
import {barChart} from "./charts.js";

const pm = (v, d = 4) => (v && v.mean != null ? `${fmt(v.mean, d)}${v.sd != null ? ` ± ${fmt(v.sd, d)}` : ""}` : "n/a");
const pms = (v, d = 4) => (v && v.mean != null ? `${signed(v.mean, d)}${v.sd != null ? ` ± ${fmt(v.sd, d)}` : ""}` : "n/a");
const tone = (v) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
const label = (h) => h.label || h.name;

// ---------- compare ----------

export async function compareView(view, meta, preset) {
  const runs = await api("/api/runs");
  const reported = runs.filter((r) => r.has_report);
  const chosen = (preset || "").split(",").filter(Boolean).filter((p) => reported.some((r) => r.path === p));
  view.append(pageHead("Compare", "Experiments side by side. The first one you choose is the baseline; " +
    "experiments that ran on the same data and splits are also compared flow by flow, per seed."));
  if (reported.length < 2) {
    view.append(card("Not enough experiments", null, empty("Compare needs at least two experiments with a report. " +
      "Run two configs, or a sweep.", el("a", {class: "btn primary", href: "#/sweeps", text: "Sweeps"}))));
    return;
  }
  const result = el("div", {class: "stack"});
  const order = [...chosen];
  const pick = el("div", {class: "card-body flush"});
  const go = el("button", {class: "btn primary", type: "button", text: "Compare"});
  const save = el("button", {class: "btn", type: "button", text: "Save as files"});
  const boot = el("input", {class: "input", type: "number", min: "0", max: "2000", value: "300", style: {maxWidth: "110px"}});
  const drawPick = () => {
    pick.replaceChildren(table(["", "Experiment", "Mode", "Objective", "Units", "Cascade", "Updated"],
      reported.map((r) => {
        const i = order.indexOf(r.path);
        const box = el("input", {type: "checkbox", checked: i >= 0, "aria-label": `choose ${r.name}`});
        box.addEventListener("change", () => {
          const k = order.indexOf(r.path);
          if (box.checked && k < 0) order.push(r.path);
          if (!box.checked && k >= 0) order.splice(k, 1);
          drawPick();
        });
        const base = i === 0 ? badge("baseline", "blue") : i > 0 ? el("button", {class: "btn small", type: "button",
          text: "Make baseline", onclick: () => { order.splice(i, 1); order.unshift(r.path); drawPick(); }}) : null;
        return [box, el("div", {}, el("b", {text: r.name}), " ", base, el("div", {class: "muted small mono", text: r.path})),
          (r.modes || []).join(", "), r.report?.objective || "n/a", `${r.units_done}/${r.units_total}`,
          fmt(r.report?.cascade), ago(r.updated)];
      }), {numeric: [5]}));
    go.disabled = save.disabled = order.length < 2;
  };
  drawPick();
  go.addEventListener("click", async () => {
    result.replaceChildren(note("Comparing...", "info"));
    location.hash = `#/compare/${enc(order.join(","))}`;
  });
  save.addEventListener("click", async () => {
    try { await startJob({command: "compare", experiments: order, bootstrap: boot.value}); } catch (e) { toast(e.message); }
  });
  view.append(card("Experiments", "Tick two or more; the order you tick them in is the order of the comparison.",
    el("div", {}, pick, cardBody(el("div", {class: "row"}, field("Bootstrap replicates", boot), el("span", {class: "spacer"}), save, go)))));
  view.append(result);
  if (order.length >= 2) {
    result.append(note("Comparing...", "info"));
    try {
      const c = await api("/api/compare", {experiments: order, bootstrap: Number(boot.value) || 300});
      result.replaceChildren(...renderComparison(c));
    } catch (e) { result.replaceChildren(note(e.message, "bad")); }
  }
}

function renderComparison(c) {
  const out = [];
  for (const w of c.warnings) out.push(note(w, "warn"));
  out.push(card("Cascade and first stage alone", `TEST ${c.objective}, mean over units; error bars are one sd.`,
    cardBody(el("div", {style: {maxWidth: `${Math.min(900, 260 + 170 * c.experiments.length)}px`}},
      barChart(c.experiments.map((h) => ({label: label(h).split(":")[0].slice(0, 22), values: [
        {name: "Cascade", mean: h.cascade?.mean, sd: h.cascade?.sd},
        {name: "First stage alone", mean: h.first_stage?.mean, sd: h.first_stage?.sd},
      ]})), {digits: 4})))));
  out.push(card("Experiments", `Baseline ${c.baseline}.`, el("div", {class: "card-body flush"}, table(
    ["Experiment", "Units", "Cascade", "First stage alone", "Cascade minus first", "Frontier gap", "Cost per flow"],
    c.experiments.map((h, i) => [el("div", {}, el("b", {text: label(h)}), i === 0 ? [" ", badge("baseline", "blue")] : null,
      el("div", {class: "muted small mono", text: h.path})), String(h.units.length), pm(h.cascade), pm(h.first_stage),
      el("span", {class: tone(h.minus_first?.mean), text: pms(h.minus_first)}),
      el("span", {class: tone(h.frontier_gap?.mean), text: pms(h.frontier_gap)}), pm(h.expected_cost, 3)]),
    {numeric: [1, 2, 3, 4, 5, 6]}))));
  if (c.paired.length) {
    out.push(card(`Paired against ${c.baseline}`, "Same data and splits: per unit, the experiment's cascade minus the " +
      "baseline's on the same TEST flows, with a paired bootstrap 95% interval.",
      el("div", {class: "card-body flush"}, table(["Experiment", "Mean difference", "CI above 0", "CI below 0", "Per unit"],
        c.paired.map((p) => [p.other, el("span", {class: tone(p.mean_diff), text: p.mean_diff == null ? "n/a" : signed(p.mean_diff)}),
          `${p.units_ci_above_zero} of ${p.per_unit.length}`, `${p.units_ci_below_zero} of ${p.per_unit.length}`,
          el("div", {class: "small mono"}, p.per_unit.map((r) => el("div", {class: tone(r.ci95 && (r.ci95[0] > 0 ? 1 : r.ci95[1] < 0 ? -1 : 0)),
            text: `${r.unit}: ${signed(r.diff)}${r.ci95 ? ` [${signed(r.ci95[0])}, ${signed(r.ci95[1])}]` : ""}`})),
          p.skipped.map((s) => el("div", {class: "muted", text: `skipped ${s}`})))]), {numeric: [1, 2, 3]}))));
  }
  const stageRows = c.experiments.flatMap((h) => Object.entries(h.stages).map(([st, r]) => [label(h), st,
    `${r.on} of ${r.units}`, el("span", {class: tone(r.removal?.mean), text: pms(r.removal, 3)})]));
  out.push(card("Stages", "How often selection kept each stage on, and the objective lost when it is switched off.",
    el("div", {class: "card-body flush"}, table(["Experiment", "Stage", "Kept on", "Lost if removed"], stageRows, {numeric: [2, 3]}))));
  return out;
}

// ---------- sweeps ----------

function relpath(fromDir, to) {
  const a = fromDir ? fromDir.split("/") : [], b = to.split("/");
  let i = 0;
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  return [...Array(a.length - i).fill(".."), ...b.slice(i)].join("/");
}
const dirOf = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");

export async function sweepsView(view) {
  const [sweeps, configs] = await Promise.all([api("/api/sweeps"), api("/api/configs")]);
  view.append(pageHead("Sweeps", "One base config, several variants, one comparison. Each variant changes dotted config " +
    "keys, such as stages.gate.candidates or protocol.objective; the sweep runs every variant and compares them."));
  const list = sweeps.length
    ? el("div", {class: "card-body flush"}, table(["Sweep file", "Name", "Base config", "Variants", "Modified", ""],
      sweeps.map((s) => [el("a", {class: "link mono", href: `#/sweep/${enc(s.path)}`, text: s.path}), s.name, s.base,
        s.variants == null ? "grid" : String(s.variants), ago(s.mtime),
        el("a", {class: "btn small", href: `#/sweep/${enc(s.path)}`, text: "Open"})]), {numeric: [3]}))
    : empty("No sweep files yet. Start one below.");
  view.append(card("Sweep files", null, list));
  if (!configs.length) { view.append(note("A sweep needs a base config; create one in Configs first.", "warn")); return; }
  const base = el("select", {class: "select"}, configs.map((c) => el("option", {value: c.path, text: c.path})));
  const name = el("input", {class: "input mono", value: "my-sweep"});
  const path = el("input", {class: "input mono", value: "sweeps/my-sweep.yaml"});
  name.addEventListener("input", () => { path.value = `sweeps/${name.value.trim() || "my-sweep"}.yaml`; });
  const msg = el("div");
  const create = el("button", {class: "btn primary", type: "button", text: "Create and open"});
  create.addEventListener("click", async () => {
    msg.replaceChildren();
    const rel = relpath(dirOf(path.value.trim()), base.value);
    const text = `# A sweep: every variant is the base config with the listed changes.\n` +
      `name: ${name.value.trim()}\nbase: ${rel}\nvariants:\n  base: {}\n` +
      `  # another:\n  #   stages.<stage>.decision: {grid: [0.5, 0.7], off: attack}\n` +
      `# Or, instead of variants, a grid over every combination:\n# grid:\n#   protocol.objective: [accuracy, mcc]\n`;
    try {
      const r = await api("/api/config/save", {path: path.value.trim(), yaml: text, overwrite: false});
      location.hash = `#/sweep/${enc(r.path)}`;
    } catch (e) { msg.append(note(e.message, "bad")); }
  });
  view.append(card("New sweep", null, cardBody(el("div", {class: "stack"},
    el("div", {class: "form-grid"}, field("Base config", base), field("Sweep name", name, "letters, digits and . - _"),
      field("Save as", path, "relative to the workspace")), msg, el("div", {class: "row"}, create)))));
}

export async function sweepEditorView(view, meta, path, setCrumbs) {
  setCrumbs([["Sweeps", "#/sweeps"], [path]]);
  const {yaml} = await api("/api/config" + q({path}));
  let saved = yaml;
  const area = el("textarea", {class: "textarea mono", spellcheck: "false", style: {minHeight: "420px"}});
  area.value = yaml;
  const status = el("div", {class: "stack"});
  const keysBox = el("div");
  const dirty = badge("Unsaved", "amber");
  const isDirty = () => area.value !== saved;
  let timer = null, seq = 0;
  const check = async () => {
    const mine = ++seq;
    let r;
    try { r = await api("/api/sweep/check", {yaml: area.value, path}); } catch (e) { r = {ok: false, error: e.message}; }
    if (mine !== seq) return;
    dirty.classList.toggle("hidden", !isDirty());
    status.replaceChildren();
    if (!r.ok) { status.append(note(r.error, "bad")); return; }
    status.append(note(`Valid: ${r.variants.length} variant${r.variants.length === 1 ? "" : "s"} of ${r.base}.`, "good"),
      table(["Variant", "Changes"], r.variants.map((v) => [el("b", {text: v.name}), el("span", {class: "mono small", text: v.label})])));
    keysBox.replaceChildren(el("details", {class: "trace"}, el("summary", {text: `Keys of the base config (${r.keys.length})`}),
      el("div", {class: "mono small", style: {columns: "2", lineHeight: "1.7"}}, r.keys.map((k) => el("div", {text: k})))));
  };
  area.addEventListener("input", () => { dirty.classList.toggle("hidden", !isDirty()); clearTimeout(timer); timer = setTimeout(check, 400); });
  const save = async () => {
    try {
      await api("/api/config/save", {path, yaml: area.value, overwrite: true});
      saved = area.value;
      toast(`Saved ${path}`);
      check();
    } catch (e) { toast(e.message); }
  };
  area.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); } });

  const out = el("input", {class: "input mono", value: store.get("out.sweep") || meta.default_out.sweep || "runs"});
  const seeds = el("input", {class: "input mono", placeholder: "all seeds in the base config"});
  const jobs = el("input", {class: "input mono", type: "number", value: "1", min: "-1"});
  const boot = el("input", {class: "input mono", type: "number", value: "1000", min: "0"});
  const force = checkbox("Force", "rerun seeds that are already done");
  const runMsg = el("div");
  const go = el("button", {class: "btn primary", type: "button", text: "Run sweep"});
  const refresh = () => { go.disabled = job.snapshot?.state === "running"; };
  onJob(refresh);
  refresh();
  go.addEventListener("click", async () => {
    runMsg.replaceChildren();
    if (isDirty()) { runMsg.append(note("Save the sweep first: the job reads the file on disk.", "warn")); return; }
    store.set("out.sweep", out.value.trim());
    try {
      await startJob({command: "sweep", sweep: path, out: out.value.trim(), seeds: seeds.value.trim(), jobs: jobs.value,
        bootstrap: boot.value, force: force.input.checked});
    } catch (e) { runMsg.append(note(e.message, "bad")); }
  });
  view.append(pageHead(path, null, el("button", {class: "btn primary", type: "button", text: "Save", onclick: save}),
    [badge("Sweep", "blue"), dirty]),
  el("div", {class: "split"},
    el("div", {class: "stack"},
      card("Sweep file", "name, base (relative to this file), and variants or grid. Ctrl+S saves.", cardBody(area)),
      card("Help", null, cardBody(el("div", {class: "stack small"},
        el("div", {text: "Each variant maps dotted keys to new values. Numbers in a key index lists: " +
          "stages.gate.candidates.0.class picks the first candidate's class."}),
        el("pre", {class: "code", text: "variants:\n  base: {}\n  lof:\n    stages.gate.candidates: " +
          "[{class: sklearn.neighbors.LocalOutlierFactor}]\n  fixed-cutoff:\n    stages.router.decision: {threshold: 0.5, off: attack}"}),
        keysBox)))),
    el("div", {class: "stack"},
      card("Check", null, cardBody(status)),
      card("Run", "Runs every variant, reports each, and writes the comparison.", cardBody(el("div", {class: "stack"},
        el("div", {class: "form-grid"}, field("Output root", out), field("Seeds", seeds), field("Parallel seeds", jobs),
          field("Bootstrap replicates", boot)), force.node, runMsg, el("div", {class: "row"}, go)))))));
  head0(view);
  check();
  return () => isDirty() && "This sweep has unsaved changes.";
}

function head0(view) {
  const h = view.querySelector(".page-head h2");
  if (h) h.classList.add("mono");
}
