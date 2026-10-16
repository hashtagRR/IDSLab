// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Configs: the list, new config from a template, the YAML editor with live validation and
// cascade preview, and the command forms (run, report, export-splits) the CLI offers.

import {
  el, api, q, enc, ago, card, cardBody, pageHead, table, empty, note, toast, field, checkbox,
  badge, startJob, job, onJob, store,
} from "./core.js";
import {cascadeMap} from "./charts.js";

const BLANK = `name: my-cascade
dataset:
  train: data/train.parquet   # relative to this file
  test: data/test.parquet     # or leave out and set test_fraction
  label: label
  type: attack_cat
  normal_type: Normal
protocol:
  seeds: [0, 1, 2]
  objective: accuracy
entry: first
stages:
  first:
    kind: classifier
    pipeline:
      - {class: idslab.prep.Encode, params: {categorical: [proto, service]}}
    candidates:
      - class: sklearn.ensemble.RandomForestClassifier
        params: {n_estimators: 200}
    decision: {threshold: 0.5, off: benign}
    routes: {attack: ATTACK, benign: BENIGN}
`;

const TEMPLATE_TEXT = {
  "r1_router_gate.yaml": "Entry classifier, router with a tuned cutoff, then a benign-trained anomaly gate.",
  "r2_anomaly_first.yaml": "Anomaly detector first; a classifier can clear its alarms.",
  "r1_four_layer.yaml": "Four layers: entry, check, type stage with score features, final stage.",
};

// ---------- command forms ----------

const COMMAND_INFO = {
  run: {title: "Run", verb: "Start run",
    text: "Fit, select on VAL, evaluate on TEST, write per-seed results and the report. Finished seeds are skipped unless forced."},
  report: {title: "Report", verb: "Rebuild report",
    text: "Aggregate finished seeds into report.md, report.json and report.html."},
  "export-splits": {title: "Export splits", verb: "Export splits",
    text: "Write row ids, folds and fingerprints per seed, for score files trained outside IDSLab."},
};

function cliLine(body) {
  const parts = ["idslab"];
  if (body.debug) parts.push("--debug");
  parts.push(body.command, body.config || "<config>", "--out", body.out);
  if (body.seeds) parts.push("--seeds", ...body.seeds.split(/[\s,]+/).filter(Boolean));
  if (body.command === "run") {
    if (String(body.jobs) !== "1") parts.push("--jobs", String(body.jobs));
    if (body.force) parts.push("--force");
    if (body.no_report) parts.push("--no-report");
  }
  return parts.join(" ");
}

// configPath: fixed config (editor) or null to show a picker. isDirty: () => bool for unsaved edits.
export function commandForm(command, meta, {configPath = null, configs = [], isDirty = () => false} = {}) {
  const info = COMMAND_INFO[command];
  const picker = configPath ? null : el("select", {class: "select"},
    configs.length ? configs.map((c) => el("option", {value: c.path, text: `${c.path} (${c.name})`}))
      : el("option", {value: "", text: "No configs in this workspace"}));
  const remembered = store.get(`out.${command}`);
  const out = el("input", {class: "input mono", value: remembered || meta.default_out[command]});
  const seeds = el("input", {class: "input mono", placeholder: "all seeds in the config"});
  const jobs = el("input", {class: "input mono", type: "number", value: "1", min: "-1", step: "1"});
  const force = checkbox("Force", "rerun seeds that are already done");
  const noReport = checkbox("No report", "skip writing the report after the run");
  const debug = checkbox("Debug", "show the full traceback on errors");
  const line = el("pre", {class: "code"});
  const msg = el("div");
  const go = el("button", {class: "btn primary", type: "button", text: info.verb});

  const body = () => ({
    command, config: configPath || picker?.value || "", out: out.value.trim(), seeds: seeds.value.trim(),
    jobs: jobs.value.trim() || "1", force: force.input.checked, no_report: noReport.input.checked,
    debug: debug.input.checked,
  });
  const refresh = () => {
    line.textContent = cliLine(body());
    const busy = job.snapshot?.state === "running";
    go.disabled = busy || !body().config;
    go.title = busy ? "A job is running" : "";
  };
  for (const n of [picker, out, seeds, jobs, force.input, noReport.input, debug.input]) {
    n?.addEventListener("input", refresh);
  }
  onJob(refresh);
  go.addEventListener("click", async () => {
    msg.replaceChildren();
    if (isDirty()) { msg.append(note("Save the config first: commands read the file on disk.", "warn")); return; }
    try {
      store.set(`out.${command}`, out.value.trim());
      await startJob(body());
    } catch (e) {
      msg.append(note(e.message, "bad"));
    }
  });
  refresh();
  const seedHint = command === "report" ? "also include these seeds (space separated)"
    : "run only these seeds (space separated)";
  return el("div", {class: "stack"},
    el("p", {class: "muted small", text: info.text}),
    picker ? field("Config", picker) : null,
    el("div", {class: "form-grid"},
      field("Output root", out, "relative to the workspace"),
      field("Seeds", seeds, seedHint),
      command === "run" ? field("Parallel seeds", jobs, `-1 uses all cores (${meta.cpu_count ?? "?"})`) : null),
    el("div", {class: "row"}, command === "run" ? [force.node, noReport.node] : null, debug.node),
    field("Same as", line),
    msg,
    el("div", {class: "row"}, go));
}

export async function commandView(view, meta, command, configPath) {
  const configs = await api("/api/configs");
  const info = COMMAND_INFO[command];
  if (!info) { view.append(empty("Unknown command.")); return; }
  const form = commandForm(command, meta, {configs});
  if (configPath) {
    const sel = form.querySelector("select");
    if (sel) sel.value = configPath;
    sel?.dispatchEvent(new Event("input"));
  }
  view.append(
    pageHead(info.title, `idslab ${command} from the browser. The job runs the CLI, so results match a shell run.`),
    card(info.title, null, cardBody(form)));
}

// ---------- config list ----------

export async function configsView(view) {
  const configs = await api("/api/configs");
  view.append(pageHead("Configs", "IDSLab configs found in the workspace (YAML files with entry and stages).",
    el("a", {class: "btn primary", href: "#/new", text: "New config"})));
  if (!configs.length) {
    view.append(card("No configs yet", null, empty("Write the example dataset and configs, or start a new config.",
      el("div", {class: "row", style: {justifyContent: "center"}},
        el("a", {class: "btn primary", href: "#/example", text: "Example data"}),
        el("a", {class: "btn", href: "#/new", text: "New config"})))));
    return;
  }
  const rows = configs.map((c) => ({
    path: c.path,
    cells: [el("a", {class: "link mono", href: `#/config/${enc(c.path)}`, text: c.path}), c.name || "n/a",
      String(c.stages), c.mode, ago(c.mtime),
      el("div", {class: "row"},
        el("a", {class: "btn small", href: `#/config/${enc(c.path)}`, text: "Edit"}),
        el("a", {class: "btn small", href: `#/command/run/${enc(c.path)}`, text: "Run"}))],
  }));
  view.append(card(`${configs.length} config${configs.length === 1 ? "" : "s"}`, null,
    el("div", {class: "card-body flush"},
      table(["File", "Name", "Stages", "Mode", "Modified", ""], rows,
        {onRow: (r) => { location.hash = `#/config/${enc(r.path)}`; }, numeric: [2]}))));
}

// ---------- new config ----------

export async function newConfigView(view) {
  const templates = await api("/api/templates");
  let chosen = "blank";
  const texts = {blank: BLANK, ...templates};
  const path = el("input", {class: "input mono", value: "my-cascade.yaml"});
  const msg = el("div");
  const cards = el("div", {class: "templates"});
  const pick = (k) => {
    chosen = k;
    for (const c of cards.children) c.classList.toggle("active", c.dataset.key === k);
    if (k !== "blank") path.value = k;
  };
  const tpl = (k, title, text) => el("button", {class: "template", type: "button", "data-key": k, onclick: () => pick(k)},
    el("h4", {class: "mono", text: title}), el("p", {text}));
  cards.append(tpl("blank", "Blank", "One classifier stage: the smallest valid cascade, to build on."),
    ...Object.keys(templates).map((k) => tpl(k, k, TEMPLATE_TEXT[k] || "Shipped reference config.")));
  pick("blank");
  const create = el("button", {class: "btn primary", type: "button", text: "Create and open"});
  create.addEventListener("click", async () => {
    msg.replaceChildren();
    try {
      const r = await api("/api/config/save", {path: path.value.trim(), yaml: texts[chosen], overwrite: false});
      toast(`Saved ${r.path}`);
      location.hash = `#/config/${enc(r.path)}`;
    } catch (e) { msg.append(note(e.message, "bad")); }
  });
  view.append(
    pageHead("New config", "Start from a template. Data paths in a config are relative to the config file, " +
      "so save it next to its data folder (the example data lives in <folder>/data)."),
    card("Template", null, cardBody(cards)),
    card("Save as", null, cardBody(el("div", {class: "stack"},
      field("File", path, "relative to the workspace; .yaml or .yml; an existing file is not replaced"),
      msg, el("div", {class: "row"}, create)))));
}

// ---------- editor ----------

export async function editorView(view, meta, path, setCrumbs) {
  const {yaml} = await api("/api/config" + q({path}));
  let saved = yaml;
  const area = el("textarea", {spellcheck: "false", "aria-label": "Config YAML", autocomplete: "off"});
  area.value = yaml;
  const gutter = el("div", {class: "gutter", "aria-hidden": "true"});
  const status = el("div");
  const preview = el("div");
  const textOut = el("pre", {class: "code hidden"});
  const dirtyBadge = badge("Unsaved", "amber");
  const head = pageHead(path, null, null, [badge("Config", "blue"), dirtyBadge]);
  setCrumbs([["Configs", "#/configs"], [path]]);
  const isDirty = () => area.value !== saved;

  const lines = () => {
    const n = area.value.split("\n").length;
    gutter.textContent = Array.from({length: n}, (_, i) => i + 1).join("\n");
    gutter.scrollTop = area.scrollTop;
    dirtyBadge.classList.toggle("hidden", !isDirty());
  };
  area.addEventListener("scroll", () => { gutter.scrollTop = area.scrollTop; });
  area.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {  // two spaces, as YAML wants
      e.preventDefault();
      const {selectionStart: a, selectionEnd: b} = area;
      area.setRangeText("  ", a, b, "end");
      area.dispatchEvent(new Event("input"));
    }
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(false); }
  });

  let timer = null, seq = 0;
  const validate = async () => {
    const mine = ++seq;
    let r;
    try { r = await api("/api/validate", {yaml: area.value, path}); } catch (e) { r = {ok: false, error: e.message}; }
    if (mine !== seq) return;  // a newer edit is already being checked
    showValidation(r);
  };
  const showValidation = (r) => {
    status.replaceChildren();
    preview.replaceChildren();
    if (!r.ok) {
      status.append(note(r.error, "bad"));
      textOut.classList.add("hidden");
      return;
    }
    const c = r.config;
    status.append(note(`Valid. ${c.stages.length} stage${c.stages.length === 1 ? "" : "s"} in selection order: ` +
      `${c.order.join(" > ")}. Controls compare against ${c.first_stage}.`, "good"));
    const missing = c.data.filter((d) => !d.exists);
    if (missing.length) {
      status.append(note(`Data not found: ${missing.map((d) => d.path).join(", ")} ` +
        "(paths are relative to the config file). Write the example data from Example data.", "warn"));
    }
    textOut.textContent = r.text;
    textOut.classList.remove("hidden");
    preview.append(cascadeMap(c),
      el("div", {class: "legend"},
        el("span", {}, el("i", {style: {background: "var(--ref)"}}), "routing"),
        el("span", {}, el("i", {style: {background: "var(--s4)"}}), "score feeds a later stage"),
        el("span", {class: "muted", text: `mode ${c.protocol.mode}, seeds ${c.protocol.seeds.join(" ")}, ` +
          `objective ${c.protocol.objective}, search ${c.protocol.search}`})));
  };
  area.addEventListener("input", () => {
    lines();
    clearTimeout(timer);
    timer = setTimeout(validate, 500);
  });

  const msg = el("div");
  const save = async (asNew) => {
    msg.replaceChildren();
    let target = path;
    if (asNew) {
      target = prompt("Save as (relative to the workspace)", path.replace(/(\.ya?ml)$/, "_copy$1"));
      if (!target) return;
    }
    try {
      const r = await api("/api/config/save", {path: target, yaml: area.value, overwrite: !asNew});
      saved = area.value;
      lines();
      showValidation(r);
      toast(`Saved ${r.path}`);
      if (asNew) location.hash = `#/config/${enc(r.path)}`;
    } catch (e) { msg.append(note(e.message, "bad")); }
  };
  const revert = () => { area.value = saved; area.dispatchEvent(new Event("input")); };

  const saveBtn = el("button", {class: "btn primary", type: "button", text: "Save", onclick: () => save(false)});
  const bar = el("div", {class: "editor-bar"},
    el("span", {class: "muted small path", text: "Ctrl+S saves. Validation runs as you type, with the loader the CLI uses."}),
    el("button", {class: "btn", type: "button", text: "Validate", onclick: validate}),
    el("button", {class: "btn", type: "button", text: "Revert", onclick: revert}),
    el("button", {class: "btn", type: "button", text: "Save as", onclick: () => save(true)}),
    saveBtn);

  const forms = {};
  const panel = el("div");
  const seg = el("div", {class: "seg"});
  const showCmd = (k) => {
    for (const b of seg.children) b.classList.toggle("active", b.dataset.k === k);
    forms[k] ||= commandForm(k, meta, {configPath: path, isDirty});
    panel.replaceChildren(forms[k]);
  };
  for (const k of ["run", "report", "export-splits"]) {
    seg.append(el("button", {type: "button", "data-k": k, text: COMMAND_INFO[k].title, onclick: () => showCmd(k)}));
  }
  showCmd("run");

  head.querySelector("h2").classList.add("mono");
  view.append(head,
    el("div", {class: "split"},
      card("YAML", null, cardBody(el("div", {class: "stack"}, bar, msg, el("div", {class: "editor"}, gutter, area)))),
      el("div", {class: "stack"},
        card("Validation", null, cardBody(el("div", {class: "stack"}, status, textOut))),
        card("Commands", null, cardBody(el("div", {class: "stack"}, seg, panel))))),
    card("Cascade preview", "Routes from the parsed config. Hover a stage for its candidates and values.",
      cardBody(preview)));
  lines();
  validate();
  return () => isDirty() && "This config has unsaved changes.";
}

