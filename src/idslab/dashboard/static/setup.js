// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Guided setup of one experiment: Data, Cascade, Evaluation, Review & run, and the YAML view.
// Every step edits the same draft; steps can be visited in any order, problems are shown where
// they belong, and a run always uses the file on disk, so unsaved changes are saved first.

import {
  el, card, cardBody, note, toast, field, checkbox, badge, job, onJob, startJob, store, announce, rerender,
  plainError, errorStep, metricLabel, KIND_SHORT,
} from "./core.js";
import {bound, parseList, listText, names, relpath, dirOf, wsPath} from "./draft.js";
import {mountCascade, addStage} from "./builder.js";
import {cascadeMap} from "./charts.js";

export const STEPS = [["data", "Data"], ["cascade", "Cascade"], ["evaluation", "Evaluation"], ["review", "Review & run"]];

// ctx: {draft, meta, catalog, datafiles, step, href(step), save(asNew) -> path|null}
export function setupView(box, ctx) {
  const {draft} = ctx;
  const step = ctx.step || "data";
  const stepper = el("nav", {class: "stepper", "aria-label": "Setup steps"});
  const body = el("div", {class: "stack"});
  const footer = el("div", {class: "row step-nav"});
  box.replaceChildren(stepper, body, footer);

  const drawStepper = () => {
    const errs = draft.errors();
    const count = Object.fromEntries(STEPS.map(([k]) => [k, 0]));
    for (const l of errs) count[errorStep(l) === "yaml" ? "review" : errorStep(l)]++;
    stepper.replaceChildren(...STEPS.map(([k, label], i) => el("a", {
      href: ctx.href(k), class: `step${k === step ? " active" : ""}`, "aria-current": k === step ? "step" : null},
    el("span", {class: "step-n", text: String(i + 1)}), el("span", {text: label}),
    count[k] ? el("span", {class: "badge red", text: String(count[k]), "aria-label": `${count[k]} problem${count[k] === 1 ? "" : "s"}`}) : null)),
    el("span", {class: "spacer"}),
    el("a", {href: ctx.href("yaml"), class: `step yaml${step === "yaml" ? " active" : ""}`, "aria-current": step === "yaml" ? "page" : null,
      text: "YAML view"}));
  };
  drawStepper();

  let destroy = null;
  const stepNames = STEPS.map(([k]) => k);
  const idx = stepNames.indexOf(step);
  if (idx >= 0) {
    const back = idx > 0 ? el("a", {class: "btn", href: ctx.href(stepNames[idx - 1]), text: `Back: ${STEPS[idx - 1][1]}`}) : null;
    const nextStep = stepNames[idx + 1];
    const next = nextStep ? el("button", {class: "btn primary", type: "button", text: `Next: ${STEPS[idx + 1][1]}`}) : null;
    const warn = el("div");
    next?.addEventListener("click", async () => {
      await draft.flush();
      const mine = draft.errors().filter((l) => errorStep(l) === step);
      if (mine.length && !warn.dataset.shown) {
        warn.dataset.shown = "1";
        warn.replaceChildren(note(`This step still has ${mine.length} problem${mine.length === 1 ? "" : "s"} (listed above). ` +
          "You can go on and come back; a run stays blocked until they are fixed. Press Next again to continue.", "warn"));
        announce(`${mine.length} problems on this step`);
        return;
      }
      location.hash = ctx.href(nextStep);
    });
    footer.replaceChildren(...[back, el("span", {class: "spacer"}), warn, next].filter(Boolean));
  }

  if (step === "data") destroy = dataStep(body, ctx);
  else if (step === "cascade") destroy = cascadeStep(body, ctx);
  else if (step === "evaluation") destroy = evaluationStep(body, ctx);
  else if (step === "review") destroy = reviewStep(body, ctx);
  else if (step === "yaml") destroy = yamlStep(body, ctx);
  else body.append(note("No such step.", "warn"));

  const off = draft.on((what) => { if (what !== "text") drawStepper(); });
  return () => { off(); destroy?.(); };
}

// ---------- shared pieces ----------

function needsParse(box, draft) {
  if (draft.raw) return false;
  box.append(note(`The YAML does not parse, so this step cannot show the settings. Fix it in the YAML view. ${draft.validation?.error || ""}`, "bad"));
  return true;
}

// Problems from the loader for one step, in plain words with the loader's line under each.
function stepErrors(draft, step, {exclude = () => false} = {}) {
  const box = el("div", {class: "stack", "aria-live": "polite"});
  const fill = () => {
    const mine = draft.errors().filter((l) => errorStep(l) === step && !exclude(l));
    box.replaceChildren(...mine.map((l) => {
      const plain = plainError(l);
      return el("div", {class: "note bad"}, plain, plain !== l ? el("div", {class: "mono small muted", text: l}) : null);
    }));
  };
  fill();
  return {node: box, fill};
}

// The loader's problems about one key, shown under its field.
function fieldErrors(draft, prefix) {
  const box = el("div", {class: "field-error", role: "alert"});
  const fill = () => {
    const mine = draft.errors().filter((l) => l.startsWith(prefix + ":") || l.startsWith(prefix + " "));
    box.replaceChildren(...mine.map((l) => el("div", {text: plainError(l.slice(prefix.length + 1).trim())})));
  };
  fill();
  return {node: box, fill};
}

export function validationSummary(draft, href) {
  const box = el("div", {class: "stack"});
  const v = draft.validation;
  if (!v) { box.append(note("Checking...", "info")); return box; }
  if (v.ok) {
    const c = v.config;
    box.append(note(`Valid. ${c.stages.length} stage${c.stages.length === 1 ? "" : "s"} in selection order: ${c.order.join(" > ")}. ` +
      `Controls compare against ${c.first_stage} alone.`, "good"));
    const missing = (c.data || []).filter((d) => !d.exists);
    if (missing.length) {
      box.append(el("div", {class: "note warn"}, `Data not found: ${missing.map((d) => d.path).join(", ")} (paths are relative to the config file). `,
        href ? el("a", {class: "link", href: href("data"), text: "Fix the dataset"}) : null));
    }
    return box;
  }
  for (const l of draft.errors()) {
    const step = errorStep(l);
    const plain = plainError(l);
    box.append(el("div", {class: "note bad"}, el("div", {}, plain, href ? [" ", el("a", {class: "link", href: href(step === "yaml" ? "yaml" : step),
      text: `Go to ${step === "yaml" ? "the YAML view" : STEPS.find(([k]) => k === step)?.[1] || step}`})] : null),
    plain !== l ? el("div", {class: "mono small muted", text: l}) : null));
  }
  return box;
}

// ---------- Data ----------

function dataStep(box, ctx) {
  const {draft} = ctx;
  if (needsParse(box, draft)) return null;
  const inner = el("div", {class: "stack"});
  box.append(inner);
  let fills = [];
  const draw = () => {
    fills = [];
    const raw = draft.raw;
    if (!raw) { inner.replaceChildren(); needsParse(inner, draft); return; }
    const d = raw.dataset = raw.dataset || {};
    const base = dirOf(draft.path || "");
    const errs = stepErrors(draft, "data", {exclude: (l) => /^dataset\.\w+/.test(l)});
    fills.push(errs.fill);
    const fe = (k) => { const f = fieldErrors(draft, `dataset.${k}`); fills.push(f.fill); return f; };
    const fileSel = (key, label, extra = []) => {
      const sel = el("select", {class: "select", id: `ds-${key}`, "aria-label": label},
        extra.map(([v, t]) => el("option", {value: v, text: t})),
        ctx.datafiles.map((f) => el("option", {value: relpath(base, f), text: f})));
      const cur = d[key] || "";
      if (cur && ![...sel.options].some((o) => o.value === cur)) sel.append(el("option", {value: cur, text: `${cur} (not found)`}));
      sel.value = cur;
      return sel;
    };
    const train = fileSel("train", "Training file", [["", "(choose a file)"]]);
    train.addEventListener("change", async () => {
      if (train.value) d.train = train.value; else delete d.train;
      if (await draft.commit()) inspect();
    });
    const testMode = d.test_fraction != null && !d.test ? "fraction" : "file";
    const test = fileSel("test", "Test file", [["", "(choose a file)"], ["__fraction", "None: hold out a share of the training file"]]);
    if (testMode === "fraction") test.value = "__fraction";
    test.addEventListener("change", () => {
      if (test.value === "__fraction") { delete d.test; d.test_fraction = d.test_fraction ?? 0.3; }
      else { if (test.value) d.test = test.value; else delete d.test; delete d.test_fraction; }
      draft.commit();
    });
    const exists = (p) => p && ctx.datafiles.includes(wsPath(draft.path, p));
    const ds = draft.dataset;
    const cols = ds ? ds.columns.map((c) => c.name) : [];
    const colField = (key, label, hint) => {
      const err = fe(key);
      const input = cols.length ? bound(draft, d, key, {options: [["", "(none)"], ...cols], id: `ds-${key}`})
        : bound(draft, d, key, {placeholder: "column name", id: `ds-${key}`});
      return el("div", {}, field(label, input, hint), err.node);
    };
    const typeVals = ds && d.type ? (ds.columns.find((c) => c.name === d.type)?.examples || []) : [];
    const normal = bound(draft, d, "normal_type", {placeholder: "Normal", id: "ds-normal_type"});
    normal.setAttribute("list", "ds-normal-values");

    // suggestions, each an explicit choice
    const sugg = [];
    if (ds) {
      const sg = ds.suggest;
      const offer = (label, apply) => sugg.push(el("button", {class: "btn small", type: "button", text: label, onclick: () => { apply(); draft.commit(); }}));
      if (sg.label && d.label !== sg.label) offer(`Use ${sg.label} as the label column`, () => { d.label = sg.label; });
      if (sg.type && d.type !== sg.type) offer(`Use ${sg.type} as the type column`, () => { d.type = sg.type; });
      if (sg.normal_type && d.normal_type !== sg.normal_type) offer(`Use ${sg.normal_type} as the normal type`, () => { d.normal_type = sg.normal_type; });
    }

    // categorical preprocessing: the Encode step of every stage that has one
    const stages = Object.entries(raw.stages || {});
    const encodes = stages.flatMap(([n, s]) => (s.pipeline || []).filter((p) => p.class === "idslab.prep.Encode").map((p) => [n, p]));
    const shared = encodes.length ? listText(encodes[0][1].params?.categorical || []) : "";
    const same = encodes.every(([, p]) => listText(p.params?.categorical || []) === shared);
    const cats = el("input", {class: "input mono", id: "ds-categorical", value: shared, placeholder: "proto, service, state"});
    const setCats = (list) => {
      for (const [, p] of encodes) p.params = {...(p.params || {}), categorical: [...list]};
      draft.commit();
    };
    cats.addEventListener("change", () => setCats(names(cats.value)));
    const fitted = stages.filter(([, s]) => (s.candidates || []).some((c) => !c.external) && !(s.pipeline || []).some((p) => p.class === "idslab.prep.Encode"));
    const textCols = ds?.suggest?.categorical || [];

    const tmap = el("textarea", {class: "textarea mono", id: "ds-type_map", rows: 3, placeholder: "Fuzzers: Scanning\nAnalysis: Scanning",
      value: Object.entries(d.type_map || {}).map(([k, v]) => `${k}: ${v}`).join("\n")});
    tmap.addEventListener("change", () => {
      const m = {};
      for (const line of tmap.value.split("\n")) {
        const x = /^\s*(.+?)\s*:\s*(.+?)\s*$/.exec(line);
        if (x) m[x[1]] = x[2];
      }
      if (Object.keys(m).length) d.type_map = m; else delete d.type_map;
      draft.commit();
    });
    const tmErr = fe("type_map"), tfErr = fe("test_fraction");

    inner.replaceChildren(
      el("p", {class: "muted", text: "Choose the flow files and tell IDSLab which columns hold the answer. Paths are written relative to the config file."}),
      errs.node,
      card("Files", null, cardBody(el("div", {class: "stack"},
        el("div", {class: "form-grid"},
          el("div", {}, field("Training file", train, d.train && !exists(d.train) ? "not found in the workspace" : null), fe("train").node),
          el("div", {}, field("Test file", test, "a separate file, or a share of the training file held out as TEST"), fe("test").node)),
        test.value === "__fraction" ? el("div", {}, field("Share held out as TEST", bound(draft, d, "test_fraction", {type: "number", placeholder: "0.3", id: "ds-test_fraction"}),
          "between 0 and 1; the split is stratified and fixed per seed"), tfErr.node) : null,
        ctx.datafiles.length ? null : note("No Parquet or CSV files were found in the workspace. Write the example data from Datasets, or copy your files in.", "info")))),
      card("Columns", ds ? `${ds.columns.length} columns; ${ds.rows != null ? `${ds.rows.toLocaleString()} rows` : `first ${ds.rows_in_sample.toLocaleString()} rows read`}` : null,
        cardBody(el("div", {class: "stack"},
          el("div", {class: "row"}, el("button", {class: "btn small", type: "button", text: ds ? "Read the columns again" : "Read the columns",
            disabled: !d.train, onclick: inspect}), el("span", {class: "muted small", text: "reads a sample, never the whole file"})),
          sugg.length ? el("div", {class: "stack"}, el("div", {class: "section-title", text: "Suggested from the data"}), el("div", {class: "row wrap"}, sugg)) : null,
          ds ? el("details", {class: "trace", open: ds.columns.length <= 24}, el("summary", {text: "Column list"}),
            el("div", {class: "table-wrap"}, el("table", {class: "table"},
              el("thead", {}, el("tr", {}, ["column", "type", "distinct in sample", "missing", "examples"].map((h) => el("th", {text: h})))),
              el("tbody", {}, ds.columns.map((c) => el("tr", {}, el("td", {class: "mono", text: c.name}),
                el("td", {text: c.numeric ? "number" : "text"}), el("td", {class: "num", text: String(c.distinct)}),
                el("td", {class: "num", text: String(c.missing)}),
                el("td", {class: "small muted", text: c.examples.slice(0, 4).map((x) => (c.numeric && x.length > 9 ? Number(x).toPrecision(4) : x)).join(", ")}))))))) : null,
          el("div", {class: "form-grid"},
            colField("label", "Label column (0 = benign, 1 = attack)", "or leave empty and derive it from the type column"),
            colField("type", "Attack type column", "needed for type stages, held-out families and per-type results"),
            el("div", {}, field("Normal type value", normal, "the type value of benign flows"), fe("normal_type").node,
              el("datalist", {id: "ds-normal-values"}, typeVals.map((v) => el("option", {value: v})))),
            colField("id_column", "Row id column", "optional; must be unique")),
          el("div", {}, field("Columns to drop", bound(draft, d, "drop", {parse: names, placeholder: "src_ip, dst_ip", id: "ds-drop"}),
            cols.length ? `comma separated; columns: ${cols.slice(0, 12).join(", ")}${cols.length > 12 ? ", ..." : ""}` : "comma separated"), fe("drop").node),
          el("details", {class: "trace", open: Boolean(d.type_map || d.type_fill)}, el("summary", {text: "Merge or fill attack types"}),
            el("div", {class: "stack"},
              el("div", {}, field("Merge types into groups (one per line, raw: group)", tmap), tmErr.node),
              el("div", {}, field("Name for rows with no type", bound(draft, d, "type_fill", {placeholder: "leave empty: a missing type is an error", id: "ds-type_fill"})), fe("type_fill").node)))))),
      card("Text columns", "How text columns are turned into numbers (the Encode step of each stage).", cardBody(el("div", {class: "stack"},
        encodes.length ? el("div", {class: "stack"},
          field(`Categorical columns${same ? "" : " (the stages differ; this sets them all)"}`, cats,
            `used by ${encodes.map(([n]) => n).join(", ")}`),
          textCols.length && listText(textCols) !== shared ? el("div", {class: "row"},
            el("button", {class: "btn small", type: "button", text: `Use the text columns found: ${textCols.join(", ")}`, onclick: () => setCats(textCols)})) : null)
          : el("p", {class: "muted small", text: "No stage has an Encode step yet."}),
        fitted.length && textCols.length ? el("div", {class: "row"}, el("button", {class: "btn small", type: "button",
          text: `Add an Encode step to ${fitted.map(([n]) => n).join(", ")}`, onclick: () => {
            for (const [, s] of fitted) s.pipeline = [{class: "idslab.prep.Encode", params: {categorical: [...(encodes[0]?.[1].params?.categorical || textCols)]}}, ...(s.pipeline || [])];
            draft.commit();
          }})) : null))));
  };
  const inspect = async () => {
    try { await draft.inspect(); } catch (e) { toast(`Could not read the columns: ${e.message}`); }
  };
  draw();
  if (!draft.dataset && draft.raw?.dataset?.train) inspect();
  return draft.on((what) => {
    if (what === "validation") for (const f of fills) f();
    else if (what !== "text" && what !== "layout") rerender(inner, draw);
  });
}

// ---------- Cascade ----------

function cascadeStep(box, ctx) {
  const {draft} = ctx;
  if (draft.raw && !Object.keys(draft.raw.stages || {}).length) {
    box.append(note("The cascade is empty. Add a first stage from the list on the left.", "info"));
  }
  const holder = el("div");
  box.append(holder);
  if (!draft.raw) { needsParse(box, draft); return null; }
  if (!draft.dataset && draft.raw.dataset?.train) draft.inspect().catch(() => {});
  const c = mountCascade(holder, draft, {catalog: ctx.catalog});
  return () => c.destroy();
}

// ---------- Evaluation ----------

const OBJECTIVE_TEXT = {
  accuracy: "share of flows labelled correctly",
  balanced_accuracy: "mean of the detection rate and the benign pass rate; fair when attacks are rare",
  mcc: "Matthews correlation; uses all four cells of the confusion matrix",
  f1: "balance of precision and detection rate on attacks",
  recall: "detection rate; ignores false alarms, so use it with care",
  precision: "share of alarms that are attacks",
  cost: "average cost per flow, from the cost of a missed attack and of a false alarm",
  recall_at_fpr: "highest detection rate among the options whose false-alarm rate stays under a cap",
};

function evaluationStep(box, ctx) {
  const {draft, meta} = ctx;
  if (needsParse(box, draft)) return null;
  const inner = el("div", {class: "stack"});
  box.append(inner);
  let fills = [];
  const draw = () => {
    fills = [];
    const raw = draft.raw;
    if (!raw) { inner.replaceChildren(); needsParse(inner, draft); return; }
    const p = raw.protocol = raw.protocol || {};
    const c = raw.controls = raw.controls || {};
    const mode = p.mode || "standard";
    const fe = (k) => { const f = fieldErrors(draft, `protocol.${k}`); fills.push(f.fill); return f.node; };
    const ce = (k) => { const f = fieldErrors(draft, `controls.${k}`); fills.push(f.fill); return f.node; };
    const errs = stepErrors(draft, "evaluation", {exclude: (l) => /^(protocol|controls)\.\w+/.test(l)});
    fills.push(errs.fill);

    const cur = p.objective;
    const kind = typeof cur === "object" && cur ? Object.keys(cur)[0] : (cur || "accuracy");
    const plugins = meta.objectives.filter((o) => !(o in OBJECTIVE_TEXT));
    const obj = el("select", {class: "select", id: "ev-objective"},
      el("optgroup", {label: "Metrics"}, meta.objectives.filter((o) => o in OBJECTIVE_TEXT).map((o) => el("option", {value: o, text: metricLabel(o)}))),
      el("optgroup", {label: "With a setting"}, el("option", {value: "cost", text: "Cost of errors"}),
        el("option", {value: "recall_at_fpr", text: "Recall at a false-alarm cap"})),
      plugins.length ? el("optgroup", {label: "From installed plugins"}, plugins.map((o) => el("option", {value: o, text: o}))) : null);
    if (![...obj.options].some((o) => o.value === kind)) obj.append(el("option", {value: kind, text: `${kind} (as written)`}));
    obj.value = kind;
    obj.addEventListener("change", () => {
      const v = obj.value;
      if (v === "accuracy") delete p.objective;
      else if (v === "cost") p.objective = {cost: {missed_attack: 10, false_alarm: 1}};
      else if (v === "recall_at_fpr") p.objective = {recall_at_fpr: 0.01};
      else p.objective = v;
      draft.commit();
    });
    let objExtra = null;
    if (cur?.cost) {
      objExtra = el("div", {class: "form-grid"},
        field("Cost of a missed attack", bound(draft, cur.cost, "missed_attack", {type: "number"})),
        field("Cost of a false alarm", bound(draft, cur.cost, "false_alarm", {type: "number"})));
    } else if (cur?.recall_at_fpr != null) {
      objExtra = field("Highest false-alarm rate (FPR) allowed", bound(draft, cur, "recall_at_fpr", {type: "number"}), "for example 0.01 for one in a hundred benign flows");
    }

    inner.replaceChildren(
      el("div", {class: "note info"}, el("b", {text: "How choices are made. "}),
        "Every choice (which model, which threshold, whether a stage stays on) is made on the VAL split. " +
        "TEST is read once, after the cascade is frozen, so the TEST figures are not tuned to TEST."),
      errs.node,
      card("Objective", "What the choices on VAL try to maximise.", cardBody(el("div", {class: "stack"},
        field("Objective", obj, OBJECTIVE_TEXT[kind] || (plugins.includes(kind) ? "an objective from an installed plugin" : null)), fe("objective"), objExtra))),
      card("Repeated runs", null, cardBody(el("div", {class: "stack"},
        el("div", {class: "form-grid"},
          el("div", {}, field("Seeds / repeated runs", bound(draft, p, "seeds", {parse: (v) => parseList(v).filter(Number.isInteger), placeholder: "0, 1, 2, 3, 4", id: "ev-seeds"}),
            "each seed draws new splits and refits everything; results report the spread over seeds"), fe("seeds")),
          el("div", {}, field("Protocol", bound(draft, p, "mode", {id: "ev-mode", options: [["", "standard: every attack type is seen in training"],
            ["heldout_family", "held-out family: one attack family is kept out of training and selection"],
            ["natural_novelty", "natural novelty: some types appear only in TEST"]],
          onSet: () => {
            if (p.mode !== "heldout_family") { delete p.families; delete p.family_level; }
            if (p.mode !== "natural_novelty") delete p.novel_types;
          }})), fe("mode"))),
        mode === "heldout_family" ? el("div", {class: "form-grid"},
          el("div", {}, field("Families to hold out", bound(draft, p, "families", {parse: names, placeholder: "every attack family"}),
            "one run unit per seed and family"), fe("families")),
          el("div", {}, field("Family level", bound(draft, p, "family_level", {options: [["", "mapped (after merging types)"], ["raw", "raw types"]]})), fe("family_level"))) : null,
        mode === "natural_novelty" ? el("div", {}, field("Types that appear only in TEST", bound(draft, p, "novel_types", {parse: names})), fe("novel_types")) : null))),
      card("Search over stages", null, cardBody(el("div", {class: "form-grid"},
        el("div", {}, field("Search", bound(draft, p, "search", {options: [["", "greedy: stages chosen one at a time, in order"],
          ["greedy_fixed", "greedy, with later stages fixed on while earlier ones are chosen"],
          ["joint", "greedy, then a check of every combination on VAL"]]})), fe("search")),
        p.search === "joint" ? el("div", {}, field("Most combinations checked", bound(draft, p, "joint_max", {type: "number", placeholder: "5000"})), fe("joint_max")) : null))),
      el("details", {class: "trace card-like"}, el("summary", {text: "Splits, folds and size"}), el("div", {class: "form-grid"},
        el("div", {}, field("VAL share", bound(draft, p, "val_fraction", {type: "number", placeholder: "0.2"}), "of the training partition"), fe("val_fraction")),
        el("div", {}, field("CAL share", bound(draft, p, "cal_fraction", {type: "number", placeholder: "0.2"}), "for calibration"), fe("cal_fraction")),
        el("div", {}, field("Out-of-fold folds", bound(draft, p, "oof_folds", {type: "number", placeholder: "3"}), "for scores passed between stages"), fe("oof_folds")),
        el("div", {}, field("Cores per model fit", bound(draft, p, "n_jobs", {type: "number", placeholder: "1"}), "-1 uses every core"), fe("n_jobs")),
        el("div", {}, field("Subsample training rows", subsample(draft, p, "train"), "empty: every row"), fe("subsample")),
        el("div", {}, field("Subsample TEST rows", subsample(draft, p, "test"), "empty: every row")))),
      el("details", {class: "trace card-like"}, el("summary", {text: "Controls (the baselines the cascade is compared with)"}), el("div", {class: "stack"},
        el("p", {class: "hint", text: "Every run also scores the first stage on its own, at the cascade's choices and at its own best VAL cutoff, and bootstraps the differences per run."}),
        el("div", {class: "form-grid"},
          field("First stage for the controls", bound(draft, c, "first_stage", {options: [["", "the entry stage"],
            ...Object.entries(raw.stages || {}).filter(([, s]) => s.kind === "classifier").map(([n]) => n)]})),
          field("Bootstrap replicates", bound(draft, c, "bootstrap", {type: "number", placeholder: "1000"})),
          field("Frontier replicates", bound(draft, c, "frontier_bootstrap", {type: "number", placeholder: "200"})),
          field("Cutoffs tried for the B2 control", bound(draft, c, "cutoff_grid", {parse: (v) => parseList(v).filter((x) => typeof x === "number"),
            placeholder: "0.05 to 0.95 in steps of 0.025"}))),
        ce("first_stage"), ce("bootstrap"), ce("frontier_bootstrap"), ce("cutoff_grid"))));
  };
  draw();
  return draft.on((what) => {
    if (what === "validation") for (const f of fills) f();
    else if (what !== "text" && what !== "layout" && what !== "dataset") rerender(inner, draw);
  });
}

function subsample(draft, p, key) {
  const input = el("input", {class: "input", type: "number", placeholder: "all", value: p.subsample?.[key] ?? ""});
  input.addEventListener("change", () => {
    const v = input.value.trim();
    const sub = {...(p.subsample || {})};
    if (v) sub[key] = Number(v); else delete sub[key];
    if (Object.keys(sub).length) p.subsample = sub; else delete p.subsample;
    draft.commit();
  });
  return input;
}

// ---------- Review & run ----------

function cliLine(path, o) {
  const parts = ["idslab"];
  if (o.debug) parts.push("--debug");
  parts.push("run", path || "<config.yaml>", "--out", o.out || "runs");
  if (o.seeds) parts.push("--seeds", ...o.seeds.split(/[\s,]+/).filter(Boolean));
  if (String(o.jobs) !== "1") parts.push("--jobs", String(o.jobs));
  if (o.force) parts.push("--force");
  if (o.no_report) parts.push("--no-report");
  return parts.join(" ");
}

function reviewStep(box, ctx) {
  const {draft, meta} = ctx;
  const summary = el("div");
  const status = el("div", {class: "stack"});
  // where this experiment's results already are, so a run reuses its finished units
  const out = el("input", {class: "input mono", id: "rv-out", value: ctx.info?.output?.out_root || store.get("out.run") || meta.default_out.run});
  const seeds = el("input", {class: "input mono", id: "rv-seeds", placeholder: "the seeds in the config"});
  const jobs = el("input", {class: "input mono", id: "rv-jobs", type: "number", value: "1", min: "-1", step: "1"});
  const force = checkbox("Run finished units again", "--force: ignore results whose fingerprints match");
  const noReport = checkbox("Skip the report", "--no-report: build it later from the Report tab");
  const debug = checkbox("Show full tracebacks", "--debug: for reporting a problem");
  const line = el("pre", {class: "code", "aria-label": "Command line"});
  const msg = el("div", {"aria-live": "polite"});
  const go = el("button", {class: "btn primary", type: "button"});
  const opts = () => ({out: out.value.trim(), seeds: seeds.value.trim(), jobs: jobs.value.trim() || "1",
    force: force.input.checked, no_report: noReport.input.checked, debug: debug.input.checked});

  const refresh = () => {
    line.textContent = cliLine(draft.path, opts());
    const v = draft.validation;
    const busy = job.snapshot?.state === "running";
    const invalid = !v || !v.ok;
    const missing = v?.ok ? (v.config.data || []).filter((d) => !d.exists) : [];
    const unsaved = draft.dirty || !draft.path;
    go.textContent = unsaved ? "Save and run" : "Run";
    go.disabled = busy || invalid || missing.length > 0;
    status.replaceChildren();
    if (busy) {
      const s = job.snapshot;
      status.append(el("div", {class: "note warn"}, `Another job is running (idslab ${s.command}${s.config ? ` ${s.config}` : ""}). ` +
        "One job runs at a time, so this run can start when it ends. ", el("a", {class: "link", href: "#/jobs", text: "View the running job"})));
    }
    if (invalid) status.append(note("The run is blocked until the problems above are fixed.", "bad"));
    else if (missing.length) status.append(note(`The run is blocked: ${missing.map((d) => d.path).join(", ")} not found.`, "bad"));
    else if (unsaved && !busy) {
      status.append(note(draft.path ? "This experiment has unsaved changes. A run reads the file on disk, so they are saved first." :
        "This design is not saved yet. Choose a file name; the run then reads that file.", "info"));
    }
  };
  const draw = () => {
    summary.replaceChildren(validationSummary(draft, ctx.href));
    const v = draft.validation;
    if (v?.ok) {
      const c = v.config, pr = c.protocol;
      summary.append(el("dl", {class: "kv"},
        el("dt", {text: "Saved file"}), el("dd", {class: "mono", text: draft.path ? `${draft.path}${draft.dirty ? " (unsaved changes)" : ""}` : "not saved yet"}),
        el("dt", {text: "Name"}), el("dd", {text: c.name}),
        el("dt", {text: "Data"}), el("dd", {}, (c.data || []).map((d) => el("div", {class: "mono small"}, `${d.role}: ${d.path}`, d.exists ? "" : badge("not found", "red")))),
        el("dt", {text: "Stages"}), el("dd", {text: c.stages.map((s) => `${s.name} (${KIND_SHORT[s.kind] || s.kind})`).join(" > ")}),
        el("dt", {text: "Objective"}), el("dd", {text: metricLabel(pr.objective)}),
        el("dt", {text: "Seeds / repeated runs"}), el("dd", {text: pr.seeds.join(", ")}),
        el("dt", {text: "Protocol"}), el("dd", {text: `${pr.mode.replace(/_/g, " ")}; search ${pr.search}`})),
      el("div", {class: "map-box", style: {maxWidth: `${Math.min(1000, 320 * c.stages.length)}px`}}, cascadeMap(c)));
    }
    refresh();
  };
  go.addEventListener("click", async () => {
    msg.replaceChildren();
    go.disabled = true;
    try {
      let path = draft.path;
      if (draft.dirty || !path) {
        path = await ctx.save(!path);
        if (!path) { refresh(); return; }
      }
      store.set("out.run", out.value.trim());
      await startJob({command: "run", config: path, ...opts()});
    } catch (e) {
      msg.append(note(e.message, "bad"));
      refresh();
    }
  });
  for (const n of [out, seeds, jobs, force.input, noReport.input, debug.input]) n.addEventListener("input", refresh);
  onJob(refresh);
  box.append(
    card("What will run", null, cardBody(summary)),
    card("Run", "The run uses the saved file, exactly as idslab run would from a terminal.", cardBody(el("div", {class: "stack"},
      el("div", {class: "form-grid"},
        field("Output folder", out, "relative to the workspace; results go to <folder>/<name>"),
        field("Seeds for this run", seeds, "space separated; empty runs every seed in the config"),
        field("Units in parallel", jobs, `-1 uses every core (${meta.cpu_count ?? "?"}); workers do not log their progress`)),
      el("details", {class: "trace"}, el("summary", {text: "Advanced"}), el("div", {class: "stack"}, force.node, noReport.node, debug.node)),
      el("p", {class: "hint", text: "Units already finished with the same config, data and splits are reused, not run again. " +
        "That is how a stopped run continues: nothing inside a unit is checkpointed."}),
      field("Same as", line), status, msg, el("div", {class: "row"}, el("span", {class: "spacer"}), go)))));
  draw();
  return draft.on((what) => { if (what !== "text") draw(); });
}

// ---------- YAML ----------

function yamlStep(box, ctx) {
  const {draft} = ctx;
  const area = el("textarea", {spellcheck: "false", "aria-label": "Config YAML", autocomplete: "off"});
  area.value = draft.text;
  const gutter = el("div", {class: "gutter", "aria-hidden": "true"});
  const status = el("div", {"aria-live": "polite"});
  const origin = el("div");
  const lines = () => {
    const n = area.value.split("\n").length;
    gutter.textContent = Array.from({length: n}, (_, i) => i + 1).join("\n");
    gutter.scrollTop = area.scrollTop;
  };
  area.addEventListener("scroll", () => { gutter.scrollTop = area.scrollTop; });
  area.addEventListener("keydown", (e) => {
    if (e.key === "Tab" && !e.shiftKey) {  // two spaces, as YAML wants; Shift+Tab still leaves the editor
      e.preventDefault();
      area.setRangeText("  ", area.selectionStart, area.selectionEnd, "end");
      area.dispatchEvent(new Event("input"));
    }
    if (e.key === "Escape") area.blur();
  });
  area.addEventListener("input", () => { lines(); draft.setText(area.value); });
  const drawStatus = () => {
    status.replaceChildren(validationSummary(draft, ctx.href));
    origin.replaceChildren(draft.source === "raw"
      ? note("This text was written from the visual settings by the same writer the CLI uses.", "info") : "");
  };
  box.append(
    el("p", {class: "muted", text: "The same draft as the other steps. Text typed here is saved exactly as written, comments included. " +
      "Tab inserts two spaces; press Escape, then Tab, to leave the editor."}),
    el("div", {class: "split"},
      card("YAML", null, cardBody(el("div", {class: "stack"}, origin, el("div", {class: "editor"}, gutter, area)))),
      card("Validation", "With the loader idslab validate uses.", cardBody(status))));
  lines();
  drawStatus();
  return draft.on((what) => {
    if (what !== "text" && document.activeElement !== area && area.value !== draft.text) { area.value = draft.text; lines(); }
    drawStatus();
  });
}

// a new stage for an empty blank design, so the canvas has something to start from
export function seedBlank(draft) {
  if (draft.raw && !Object.keys(draft.raw.stages || {}).length) addStage(draft, "classifier");
}

