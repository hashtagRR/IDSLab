// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Experiments: the home list, starting a new experiment, and one experiment's pages (Setup, Runs,
// Results, Report, Files). An experiment is a view over files, not a record of its own: a config
// file and the run outputs written under its name. Outputs that no config can be matched to
// without guessing are listed on their own, readable but not editable.

import {
  el, api, q, enc, ago, card, cardBody, pageHead, note, toast, badge, expBadge, job, onJob, startJob,
  store, askPath, objective, plainError,
} from "./core.js";
import {Draft, dirOf, relpath, ordered} from "./draft.js";
import {setupView, seedBlank} from "./setup.js";
import {stageDefaults} from "./builder.js";
import {runsTab, resultsTab, reportTab, filesTab} from "./results.js";
import {templateGallery} from "./library.js";

export const expHref = (key, tab, sub) => `#/e/${enc(key)}` + (tab ? `/${tab}` : "") + (sub ? `/${sub}` : "");
export const TABS = [["setup", "Setup"], ["runs", "Runs"], ["results", "Results"], ["report", "Report"], ["files", "Files"]];

// open drafts, kept while moving between an experiment's pages; dropped when leaving it
const drafts = new Map();
let current = null;  // key of the experiment on screen

export function leaveMessage(nextHash) {
  if (!current) return null;
  const d = drafts.get(current);
  if (!d?.dirty) return null;
  const prefix = `#/e/${enc(current)}`;
  if (nextHash === prefix || nextHash.startsWith(prefix + "/")) return null;  // another page of the same experiment
  return d.path ? `${d.path} has unsaved changes.` : "This new design is not saved.";
}
export function left(nextHash) {
  if (!current) return;
  const prefix = `#/e/${enc(current)}`;
  if (nextHash === prefix || nextHash.startsWith(prefix + "/")) return;
  drafts.delete(current);
  current = null;
}
export const unsavedOnClose = () => (current && drafts.get(current)?.dirty) || false;

// ---------- next actions ----------

const STEP_FOR = (err) => (/^dataset/.test(err) ? ["data", "Fix dataset"] : /^(protocol|controls)/.test(err) ? ["evaluation", "Fix evaluation settings"]
  : /^(stage |stages|entry|routing|routes and|chained)/.test(err) ? ["cascade", "Fix the cascade"] : ["yaml", "Fix the YAML"]);

export function nextActions(e) {
  const k = e.key;
  switch (e.state) {
    case "draft": return [["Continue setup", expHref(k, "setup", "review"), true], ["Open builder", expHref(k, "setup", "cascade")]];
    case "needs_data": return [["Fix dataset", expHref(k, "setup", "data"), true]];
    case "invalid": {
      const [step, label] = STEP_FOR(String(e.record?.error || "").split("\n")[0]);
      return [[label, expHref(k, "setup", step), true]];
    }
    case "running": return [["View progress", "#/jobs", true]];
    case "partial": return [["View runs", expHref(k, "runs"), true], ...(e.outputs[0]?.has_report ? [["View report", expHref(k, "report")]] : [])];
    case "completed": return [["View results", expHref(k, "results"), true], ["View report", expHref(k, "report")]];
    default: return [["Open", expHref(k), true]];
  }
}

function resultLine(o) {
  const h = o?.report;
  if (!h || h.cascade == null) return null;
  const f = objective(h.objective);
  const d = h.B1_first_stage != null ? h.cascade - h.B1_first_stage : null;
  return `${f.label}: cascade ${f.value(h.cascade)}, first stage alone ${f.value(h.B1_first_stage)} (${f.short(d)}, mean over ${h.n_units} unit${h.n_units === 1 ? "" : "s"})`;
}

// ---------- home ----------

export async function homeView(view, meta) {
  const list = await api("/api/experiments");
  const running = el("div");
  const showJob = (s) => {
    running.replaceChildren(s?.state === "running" ? el("div", {class: "note info row"},
      el("span", {text: `A job is running: idslab ${s.command}${s.config ? ` ${s.config}` : ""}.`}), el("span", {class: "spacer"}),
      el("a", {class: "btn small", href: "#/jobs", text: "View progress"})) : "");
  };
  showJob(job.snapshot);
  onJob(showJob);
  view.append(pageHead("Experiments", "A config file and the results written under its name.",
    el("a", {class: "btn primary", href: "#/new", text: "New experiment"})), running);

  if (!list.length) {
    view.append(emptyHome(meta));
    return;
  }
  const filter = el("input", {class: "input", type: "search", placeholder: "Filter by name or file", "aria-label": "Filter experiments"});
  const rows = el("div", {class: "exp-list", role: "list"});
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    const shown = list.filter((e) => !f || e.name.toLowerCase().includes(f) || (e.config || "").toLowerCase().includes(f) ||
      e.outputs.some((o) => o.path.toLowerCase().includes(f)));
    rows.replaceChildren(...shown.map(expRow));
    if (!shown.length) rows.append(el("p", {class: "muted", text: "Nothing matches the filter."}));
  };
  filter.addEventListener("input", draw);
  draw();
  view.append(list.length > 6 ? el("div", {class: "row"}, filter) : "", rows);
}

function expRow(e) {
  const o = e.outputs[0];
  const where = e.config || o?.path;
  const units = o ? `${o.units_done} of ${o.units_total} unit${o.units_total === 1 ? "" : "s"} finished` : "not run yet";
  const actions = nextActions(e);
  return el("article", {class: "exp-row", role: "listitem"},
    el("div", {class: "exp-main"},
      el("div", {class: "row"}, el("a", {class: "exp-name", href: expHref(e.key), text: e.name || "(no name)"}),
        expBadge(e.state), e.kind === "output" ? badge("No config", "amber") : null, e.changed_since_run ? badge("Config changed since the run", "amber") : null),
      el("div", {class: "muted small mono wrap", text: where}),
      el("div", {class: "small", text: [units, o?.has_report ? `report ${ago(o.report_mtime)}` : null, e.outputs.length > 1 ? `${e.outputs.length} output folders` : null]
        .filter(Boolean).join("; ")}),
      e.state === "invalid" ? el("div", {class: "small bad-text", text: plainError(String(e.record.error).split("\n")[0])}) : null,
      e.state === "completed" || e.state === "partial" ? el("div", {class: "small", text: resultLine(o) || ""}) : null),
    el("div", {class: "exp-actions"}, actions.map(([label, href, primary]) => el("a", {class: `btn small${primary ? " primary" : ""}`, href, text: label}))));
}

function emptyHome(meta) {
  const go = el("button", {class: "btn primary", type: "button", text: "Write the example"});
  const msg = el("div");
  go.addEventListener("click", async () => {
    try { await startJob({command: "example", directory: "example"}); } catch (e) { msg.replaceChildren(note(e.message, "bad")); }
  });
  return card("Start with the example", null, cardBody(el("div", {class: "stack"},
    el("p", {text: "IDSLab tests staged intrusion detectors: a chain of models in which each stage passes flows on, " +
      "stops them as attacks or clears them as benign. A run splits the data, picks every model and threshold on a validation split, " +
      "and then scores the frozen chain once on TEST, next to the first stage on its own."}),
    el("ol", {class: "workflow"},
      el("li", {}, el("b", {text: "Data. "}), "Choose a flow file and the label and type columns."),
      el("li", {}, el("b", {text: "Cascade. "}), "Draw the stages and where each output goes."),
      el("li", {}, el("b", {text: "Evaluation. "}), "Pick the objective and how many repeated runs (seeds)."),
      el("li", {}, el("b", {text: "Run and read. "}), "Follow the job, then read the results and the report.")),
    el("p", {class: "muted small", text: `The example writes a small synthetic flow dataset (types Normal, DoS, Scan, Exploit, Worms) and three reference designs to the folder example/ in ${meta.workspace.split(/[\\/]/).pop() || "the workspace"}.`}),
    msg,
    el("div", {class: "row"}, go, el("a", {class: "btn", href: "#/templates", text: "Browse templates"}),
      el("a", {class: "btn", href: "#/new", text: "New experiment"})))));
}

// ---------- new experiment ----------

const BLANK = {name: "my-cascade", dataset: {train: "data/train.parquet", test: "data/test.parquet", label: "label", type: "attack_cat",
  normal_type: "Normal"}, protocol: {seeds: [0, 1, 2]}, entry: "", stages: {}};

export async function newView(view, meta, args) {
  const [kind, ...rest] = args;
  if (kind === "blank" || kind === "template" || kind === "yaml") {
    location.replace(expHref(kind === "blank" ? "n:blank" : kind === "template" ? `n:t:${rest.join("/")}` : "n:yaml",
      "setup", kind === "yaml" ? "yaml" : kind === "blank" ? "data" : "cascade"));
    return;
  }
  const configs = await api("/api/configs");
  const paste = el("textarea", {class: "textarea mono", rows: 6, placeholder: "name: my-cascade\ndataset: ...", "aria-label": "Paste a config"});
  const pasteGo = el("button", {class: "btn", type: "button", text: "Open the pasted YAML", onclick: () => {
    if (!paste.value.trim()) { toast("Paste a config first"); return; }
    drafts.set("n:yaml", Draft.fromText(paste.value).then((d) => { d.touched = true; return d; }));
    location.hash = expHref("n:yaml", "setup", "yaml");
  }});
  const pick = el("select", {class: "select", "aria-label": "Config file"}, configs.length
    ? configs.map((c) => el("option", {value: c.path, text: `${c.path} (${c.name || "no name"})`})) : el("option", {value: "", text: "No configs in the workspace"}));
  view.append(
    pageHead("New experiment", "Start from a reference design, a YAML file you already have, or an empty canvas."),
    card("From a template", "The three reference designs that ship with IDSLab.", cardBody(await templateGallery(meta))),
    el("div", {class: "grid-2"},
      card("From existing YAML", null, cardBody(el("div", {class: "stack"},
        el("div", {class: "row"}, pick, el("button", {class: "btn", type: "button", text: "Open", disabled: !configs.length,
          onclick: () => { location.hash = expHref(`c:${pick.value}`, "setup", "data"); }})),
        el("div", {class: "section-title", text: "Or paste a config"}), paste, el("div", {class: "row"}, pasteGo)))),
      card("Blank design", null, cardBody(el("div", {class: "stack"},
        el("p", {class: "small", text: "One classifier stage to build on, with the usual column names filled in. Nothing is written until you save."}),
        el("div", {class: "row"}, el("a", {class: "btn primary", href: expHref("n:blank", "setup", "data"), text: "Start a blank design"})))))));
}

async function newDraft(key, meta) {
  if (drafts.has(key)) return drafts.get(key);
  let d = null;
  if (key === "n:blank") {
    const files = await api("/api/datafiles");
    const raw = JSON.parse(JSON.stringify(BLANK));
    const train = files.find((f) => /(^|\/)train\.(parquet|csv)/.test(f)), test = files.find((f) => /(^|\/)test\.(parquet|csv)/.test(f));
    if (train) raw.dataset.train = train;
    if (test) raw.dataset.test = test;
    const st = stageDefaults("classifier");
    delete st.pipeline;
    raw.stages.first = st;
    raw.entry = "first";
    const r = await api("/api/dump", {config: ordered(raw)});
    d = Draft.fromText(r.yaml);
  } else if (key.startsWith("n:t:")) {
    const templates = await api("/api/templates");
    const text = templates[key.slice(4)];
    if (text == null) return null;
    d = Draft.fromText(text);
  } else {
    return null;  // a pasted draft that is gone after a reload
  }
  d = await d;
  drafts.set(key, d);
  return d;
}

// ---------- one experiment ----------

export async function experimentView(view, meta, key, tab, sub, setCrumbs) {
  tab = tab || null;
  const isNew = key.startsWith("n:");
  current = key;
  let info = null;
  if (!isNew) {
    try {
      info = await api("/api/experiment" + q({key, out: store.get(`out:${key}`) || ""}));
    } catch (e) {
      current = null;
      view.append(pageHead("Experiment not found", null), note(`${e.message}. The file may have been moved or renamed.`, "warn"),
        el("a", {class: "btn primary", href: "#/", text: "Back to experiments"}));
      return null;
    }
  }
  if (!tab) tab = isNew || info.state === "draft" || info.state === "needs_data" || info.state === "invalid" ? "setup"
    : info.state === "running" ? "runs" : info.outputs[0]?.has_report ? "results" : "runs";
  if (isNew && tab !== "setup") tab = "setup";

  // the draft: one per experiment, kept while moving between its pages
  let draft = await drafts.get(key);
  if (!draft) {
    if (isNew) draft = await newDraft(key, meta);
    else if (info.config) draft = await Draft.open(info.config);
  }
  if (draft) drafts.set(key, draft);
  if (isNew && !draft) {
    view.append(pageHead("New experiment", null), note("This unsaved draft is gone (the page was reloaded). Start again.", "warn"),
      el("a", {class: "btn primary", href: "#/new", text: "New experiment"}));
    current = null;
    return null;
  }
  if (key === "n:blank") seedBlank(draft);
  // text typed in the YAML view is parsed after a short pause; draw no step from the older settings
  if (draft) await draft.flush();

  const name = draft?.raw?.name || info?.name || "New design";
  setCrumbs([["Experiments", "#/"], [name, isNew ? null : expHref(key)], [TABS.find(([k]) => k === tab)?.[1] || tab]]);

  // header: name, state, where the files are, save controls while editing
  const status = el("span", {class: "badge", "aria-live": "polite"});
  const saveBtn = el("button", {class: "btn", type: "button", text: "Save"});
  const saveAs = el("button", {class: "btn", type: "button", text: "Save as"});
  const discard = el("button", {class: "btn hidden", type: "button", text: "Discard changes"});
  const drawStatus = () => {
    if (!draft) return;
    const errs = draft.errors().length;
    const [cls, text] = draft.dirty ? ["amber", draft.path ? "Unsaved changes" : "Not saved yet"] : ["green", "Saved"];
    status.className = `badge ${errs ? "red" : cls}`;
    status.textContent = errs ? `${text}; ${errs} problem${errs === 1 ? "" : "s"}` : text;
    saveBtn.disabled = !draft.dirty;
    const show = tab === "setup" || draft.dirty;  // outside Setup the controls appear only with unsaved edits
    for (const n of [status, saveAs, saveBtn]) n.classList.toggle("hidden", !show);
    saveBtn.classList.toggle("primary", draft.dirty && tab === "setup" && sub !== "review");
    discard.classList.toggle("hidden", !draft.dirty || !draft.path);
  };

  const save = async (asNew) => {
    let target = draft.path;
    if (asNew || !target) {
      const files = await api("/api/datafiles");
      const suggestion = draft.path ? draft.path.replace(/(\.ya?ml)$/, "_copy$1") : defaultPath(draft, files, key);
      target = await askPath(asNew && draft.path ? "Save as a new file" : "Save the design",
        "Relative to the workspace, ending in .yaml. Data paths in a config are relative to the config's folder.", suggestion);
      if (!target) return null;
    }
    try {
      const wasNew = !draft.path;
      if (wasNew && key.startsWith("n:t:")) await renameDraft(draft, nameFromPath(target));
      await rebaseIfClean(draft, target);
      const r = await draft.save(target, {overwrite: !asNew && target === draft.path && !wasNew});
      toast(`Saved ${r.path}`);
      const newKey = `c:${r.path}`;
      if (newKey !== key) {
        drafts.delete(key);
        drafts.set(newKey, draft);
        current = newKey;
        location.hash = expHref(newKey, "setup", sub || "data");
      }
      return r.path;
    } catch (e) {
      toast(e.message);
      return null;
    }
  };
  saveBtn.addEventListener("click", () => save(false));
  saveAs.addEventListener("click", () => save(true));
  discard.addEventListener("click", async () => { await draft.revert(); toast("Changes discarded"); });

  const outputs = info?.outputs || [];
  const outPick = outputs.length > 1 ? el("select", {class: "select small-select", "aria-label": "Output folder"},
    outputs.map((o) => el("option", {value: o.path, text: o.path}))) : null;
  if (outPick) {
    outPick.value = info.output?.path || outputs[0].path;
    outPick.addEventListener("change", () => { store.set(`out:${key}`, outPick.value); dispatchEvent(new HashChangeEvent("hashchange")); });
  }
  const head = pageHead(name, null,
    draft ? [status, discard, saveAs, saveBtn] : null,
    [info ? expBadge(info.state) : badge("New", "blue"),
      el("span", {class: "mono small muted wrap", title: info?.config || "", text: info?.config || (draft?.path ?? "not saved yet")}),
      outPick ? el("label", {class: "row small"}, "Results from", outPick) : (info?.output ? el("span", {class: "mono small muted", text: `results in ${info.output.path}`}) : null)]);
  view.append(head);
  if (info?.kind === "output") {
    view.append(note(`Read only: ${info.reason}. Setup, runs and report rebuilds need a config with this name` +
      (info.candidates.length ? "; give one of those configs a different name, or keep only the one that wrote these results." : "."), "warn"));
  } else if (info?.changed_since_run) {
    view.append(note("The config has changed since these results were written. They show the earlier settings; run again to update them.", "warn"));
  }
  const clash = info?.config && (await sameName(info));
  if (clash) view.append(note(`Another config (${clash.join(", ")}) has the same name, so results cannot always be matched to one of them. ` +
    "Give this experiment a different name in the Data step.", "warn"));

  view.append(el("nav", {class: "tabs", "aria-label": "Experiment pages"}, TABS.map(([k, label]) => {
    const disabled = isNew && k !== "setup";
    return disabled ? el("span", {class: "tab-off", title: "Save the design first", text: label})
      : el("a", {href: expHref(key, k), class: k === tab ? "active" : null, "aria-current": k === tab ? "page" : null, text: label});
  })));

  const body = el("div", {class: "stack"});
  view.append(body);
  let destroy = null;
  const ctx = {draft, meta, info, key, save};
  if (tab === "setup") {
    if (!draft) {
      body.append(note(info.kind === "output" ? `Setup needs a config: ${info.reason}.` : "No config for this experiment.", "warn"));
    } else {
      const wait = note("Loading the model catalog (the first visit imports the model libraries)...", "info");
      body.append(wait);
      const [catalog, datafiles] = await Promise.all([api("/api/catalog"), api("/api/datafiles")]);
      wait.remove();
      destroy = setupView(body, {...ctx, catalog, datafiles, step: sub || "data", href: (s) => expHref(key, "setup", s)});
    }
  } else if (tab === "runs") await runsTab(body, ctx);
  else if (tab === "results") await resultsTab(body, ctx);
  else if (tab === "report") await reportTab(body, ctx);
  else if (tab === "files") await filesTab(body, ctx);
  else body.append(note("No such page.", "warn"));

  if (draft) {
    drawStatus();
    const off = draft.on(() => drawStatus());
    const prev = destroy;
    destroy = () => { off(); prev?.(); };
  }
  return destroy;
}

async function sameName(info) {
  const all = await api("/api/configs");
  const others = all.filter((c) => c.name === info.name && c.path !== info.config).map((c) => c.path);
  return others.length ? others : null;
}

// a config name from a file name: my_r1_router_gate.yaml gives my-r1-router-gate
export const nameFromPath = (p) => p.split("/").pop().replace(/\.ya?ml$/, "").replace(/[^A-Za-z0-9.-]+/g, "-").replace(/^-+|-+$/g, "") || "my-cascade";

// Give a draft a new name. Typed text keeps its comments: only the name line changes.
export async function renameDraft(draft, newName) {
  if (draft.source === "text" && /^name:.*$/m.test(draft.text)) {
    draft.setText(draft.text.replace(/^name:.*$/m, `name: ${newName}`));
    await draft.flush();
  } else if (draft.raw) {
    draft.raw.name = newName;
    if (await draft.commit()) await draft.flush();
  }
}

// a default file name for a new design: next to the data it reads when that is clear
function defaultPath(draft, files, key) {
  const stem = key.startsWith("n:t:") ? `my_${key.slice(4).replace(/\.ya?ml$/, "")}` : (draft.raw?.name || "my-cascade").replace(/[^\w.-]/g, "_");
  const train = draft.raw?.dataset?.train;
  if (train && !files.includes(train)) {  // a template's relative path: find a folder where it resolves
    const hit = files.find((f) => f.endsWith("/" + train));
    if (hit) return `${hit.slice(0, -train.length - 1)}/${stem}.yaml`;
  }
  return `${stem}.yaml`;
}

// A new design's data paths are relative to the workspace until it is saved. When the text has
// no comments to lose, they are rewritten for the folder the file goes to.
async function rebaseIfClean(draft, target) {
  if (draft.path || !draft.raw?.dataset || draft.lossy) return;
  const to = dirOf(target);
  if (!to) return;
  const ds = draft.raw.dataset;
  let changed = false;
  for (const k of ["train", "test"]) {
    if (typeof ds[k] === "string" && !ds[k].startsWith("/")) { ds[k] = relpath(to, ds[k]); changed = true; }
  }
  if (changed && (await draft.commit())) await draft.flush();
}
