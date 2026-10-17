// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Templates, Datasets, Reports and Help.

import {
  el, api, q, enc, fileUrl, ago, bytes, card, cardBody, pageHead, table, empty, note, toast, field, checkbox,
  badge, job, onJob, startJob, askPath, expBadge,
} from "./core.js";
import {cascadeMap} from "./charts.js";

const expHref = (key, tab, sub) => `#/e/${enc(key)}` + (tab ? `/${tab}` : "") + (sub ? `/${sub}` : "");

// ---------- templates ----------

// the comment block at the top of a shipped config, without the licence lines
function purposeOf(text) {
  const lines = [];
  for (const line of text.split("\n")) {
    if (!line.startsWith("#")) { if (lines.length) break; continue; }
    const t = line.replace(/^#\s?/, "");
    if (/^SPDX-License-Identifier|^Copyright/.test(t)) continue;
    lines.push(t);
  }
  return lines.join(" ").replace(/\s+/g, " ").trim();
}

const nameFromPath = (p) => p.split("/").pop().replace(/\.ya?ml$/, "").replace(/[^A-Za-z0-9.-]+/g, "-").replace(/^-+|-+$/g, "") || "my-cascade";

async function useTemplate(file, text) {
  const files = await api("/api/datafiles");
  const train = /^\s*train:\s*(\S+)/m.exec(text)?.[1];
  const hit = train && files.find((f) => f === train || f.endsWith("/" + train));
  const folder = hit && hit !== train ? hit.slice(0, -train.length - 1) : "";
  const stem = `my_${file.replace(/\.ya?ml$/, "")}`;
  const target = await askPath("Use this configuration",
    "A copy is saved as a separate file that you can change; the shipped template stays as it is. " +
    "Data paths in the template are relative to the config, so a file next to the data folder finds the data.",
    folder ? `${folder}/${stem}.yaml` : `${stem}.yaml`);
  if (!target) return;
  // its own name, so its results are never confused with another copy's
  const copy = text.replace(/^name:.*$/m, `name: ${nameFromPath(target)}`);
  try {
    const r = await api("/api/config/save", {path: target, yaml: copy, overwrite: false});
    toast(`Saved ${r.path}`);
    location.hash = expHref(`c:${r.path}`, "setup", "data");
  } catch (e) { toast(e.message); }
}

export async function templateGallery() {
  const templates = await api("/api/templates");
  const box = el("div", {class: "templates"});
  for (const [file, text] of Object.entries(templates)) {
    const v = await api("/api/validate", {yaml: text, path: null}).catch((e) => ({ok: false, error: e.message}));
    const c = v.ok ? v.config : null;
    box.append(el("article", {class: "template"},
      el("h4", {class: "mono", text: file}),
      el("p", {class: "small", text: purposeOf(text)}),
      c ? el("p", {class: "small muted", text: `${c.stages.length} stages: ${c.order.join(" > ")}. Objective ${c.protocol.objective}, seeds ${c.protocol.seeds.join(" ")}.`})
        : note(`Could not read the template: ${v.error}`, "warn"),
      c ? el("div", {class: "template-map", style: {maxWidth: `${Math.min(900, 300 * c.stages.length)}px`}}, cascadeMap(c)) : null,
      el("div", {class: "row wrap"},
        el("button", {class: "btn small", type: "button", text: "Use this configuration", onclick: () => useTemplate(file, text)}),
        el("a", {class: "btn small", href: `#/new/template/${enc(file)}`, text: "Preview in builder"}))));
  }
  return box;
}

export async function templatesView(view, meta) {
  view.append(pageHead("Templates", "Reference designs that ship with IDSLab. Using one saves a copy you can change."),
    await templateGallery(meta),
    note("The routes above are read from each template by the same loader the CLI uses. A preview opens the design without saving anything.", "info"));
}

// ---------- datasets ----------

export async function datasetsView(view, meta, path) {
  const list = await api("/api/datasets");
  view.append(pageHead("Datasets", "Parquet and CSV files in the workspace, outside run outputs."));
  const detail = el("div", {class: "stack"});
  if (!list.length) {
    view.append(card("No data files yet", null, cardBody(el("p", {text: "Copy flow files (Parquet or CSV) into the workspace, or write the example dataset below."}))));
  } else {
    view.append(card(`${list.length} file${list.length === 1 ? "" : "s"}`, null, el("div", {class: "card-body flush"}, table(["File", "Size", "Modified", "Read by"],
      list.map((d) => ({path: d.path, cells: [el("a", {class: "link mono", href: `#/datasets/${enc(d.path)}`, text: d.path}), bytes(d.size), ago(d.mtime),
        d.used_by.length ? el("div", {class: "row wrap"}, d.used_by.map((c) => el("a", {class: "badge", href: expHref(`c:${c}`, "setup", "data"), text: c})))
          : el("span", {class: "muted small", text: "no config"})]})),
      {onRow: (r) => { location.hash = `#/datasets/${enc(r.path)}`; }, numeric: [1]}))));
  }
  view.append(detail);
  if (path) {
    detail.append(note(`Reading ${path}...`, "info"));
    try {
      const ds = await api("/api/dataset" + q({path}));
      const sg = ds.suggest;
      detail.replaceChildren(card(path, `${ds.columns.length} columns; ${ds.rows != null ? `${ds.rows.toLocaleString()} rows` : `first ${ds.rows_in_sample.toLocaleString()} rows read`}`,
        el("div", {},
          cardBody(el("p", {class: "small", text: `Looks like: label ${sg.label || "not found"}, attack type ${sg.type || "not found"}` +
            `${sg.normal_type ? ` (benign value ${sg.normal_type})` : ""}, text columns ${sg.categorical.join(", ") || "none"}.`})),
          el("div", {class: "card-body flush"}, table(["Column", "Type", "Distinct in sample", "Missing", "Examples"],
            ds.columns.map((c) => [el("span", {class: "mono", text: c.name}), c.numeric ? "number" : "text", String(c.distinct), String(c.missing),
              el("span", {class: "small muted", text: c.examples.slice(0, 5).join(", ")})]), {numeric: [2, 3]})))));
    } catch (e) { detail.replaceChildren(note(`Could not read ${path}: ${e.message}`, "bad")); }
  }
  view.append(exampleCard());
}

export function exampleCard() {
  const dir = el("input", {class: "input mono", value: "example", "aria-label": "Folder"});
  const over = checkbox("Replace files that exist", "--overwrite");
  const line = el("pre", {class: "code"});
  const msg = el("div", {"aria-live": "polite"});
  const go = el("button", {class: "btn", type: "button", text: "Write the example"});
  const refresh = () => {
    line.textContent = `idslab example ${dir.value.trim() || "<folder>"}${over.input.checked ? " --overwrite" : ""}`;
    const busy = job.snapshot?.state === "running";
    go.disabled = busy;
    go.title = busy ? "Another job is running" : "";
  };
  dir.addEventListener("input", refresh);
  over.input.addEventListener("input", refresh);
  onJob(refresh);
  refresh();
  go.addEventListener("click", async () => {
    msg.replaceChildren();
    try { await startJob({command: "example", directory: dir.value.trim(), overwrite: over.input.checked}); }
    catch (e) { msg.append(note(e.message, "bad")); }
  });
  return card("Example data", "A synthetic flow dataset (types Normal, DoS, Scan, Exploit, Worms) and the three reference designs, ready to run.",
    cardBody(el("div", {class: "stack"}, field("Folder", dir, "relative to the workspace; the data goes to <folder>/data"), over.node,
      field("Same as", line), msg, el("div", {class: "row"}, go))));
}

// ---------- reports ----------

export async function reportsView(view) {
  const [list, history] = await Promise.all([api("/api/experiments"), api("/api/history")]);
  view.append(pageHead("Reports", "The report of every run output in the workspace, and saved comparisons."));
  const rows = list.flatMap((e) => e.outputs.map((o) => ({e, o})));
  if (!rows.length) {
    view.append(card("No run outputs yet", null, empty("Reports appear here once an experiment has run.", el("a", {class: "btn primary", href: "#/", text: "Experiments"}))));
  } else {
    const state = (o) => (!o.has_report ? (o.units_done ? badge("Not built", "amber") : badge("Nothing finished", ""))
      : o.report_behind ? badge("Out of date", "amber") : o.units_done < o.units_total ? badge("Partial", "amber") : badge("Built", "green"));
    view.append(card(`${rows.length} output folder${rows.length === 1 ? "" : "s"}`, null, el("div", {class: "card-body flush"}, table(
      ["Experiment", "Folder", "Report", "Built", "Units", ""],
      rows.map(({e, o}) => [el("div", {class: "row"}, el("a", {class: "link", href: expHref(e.key, "report"), text: e.name}), expBadge(e.state)),
        el("span", {class: "mono small", text: o.path}), state(o), o.report_mtime ? ago(o.report_mtime) : "n/a", `${o.units_done} of ${o.units_total}`,
        el("div", {class: "row"}, o.has_report ? el("a", {class: "btn small", href: fileUrl(`${o.path}/report.html`), target: "_blank", rel: "noopener", text: "Open"}) : null,
          rebuildButton(e, o))]), {numeric: [4]}))));
  }
  const cmp = history.filter((h) => h.kind === "comparison");
  view.append(card("Comparisons", "Written by Compare (Save as files) or by a sweep.", cmp.length ? el("div", {class: "card-body flush"},
    table(["Folder", "Written", ""], cmp.map((h) => [el("span", {class: "mono small", text: h.path}), ago(h.time),
      el("a", {class: "btn small", href: fileUrl(`${h.path}/compare.html`), target: "_blank", rel: "noopener", text: "Open"})])))
    : empty("No saved comparisons.", el("a", {class: "btn", href: "#/compare", text: "Compare experiments"}))));
}

function rebuildButton(e, o) {
  if (e.kind !== "config") return el("span", {class: "muted small", title: e.reason, text: "no config"});
  if (!o.units_done) return null;
  const b = el("button", {class: "btn small", type: "button", text: o.has_report ? "Rebuild" : "Build"});
  const refresh = () => {
    const busy = job.snapshot?.state === "running";
    const none = o.reportable_units === 0;  // no finished unit of this config and the current evaluation
    b.disabled = busy || e.state === "invalid" || none;
    b.title = busy ? "Another job is running" : e.state === "invalid" ? "The config has problems; fix it in Setup"
      : none ? "No finished units of this config and the current evaluation: run the experiment again first" : "";
  };
  b.addEventListener("click", async () => {
    try { await startJob({command: "report", config: e.config, out: o.path.split("/").slice(0, -1).join("/") || "."}); } catch (err) { toast(err.message); }
  });
  onJob(refresh);
  refresh();
  return b;
}

// ---------- help ----------

export function helpView(view, meta) {
  const docs = "https://github.com/hashtagRR/IDSLab/blob/main/docs/";
  const link = (file, text) => el("a", {class: "link", href: docs + file, target: "_blank", rel: "noopener", text});
  view.append(pageHead("Help", `IDSLab ${meta.version}. The dashboard runs the same commands as the idslab CLI and reads the same files.`),
    el("div", {class: "grid-2"},
      card("The workflow", null, cardBody(el("ol", {class: "workflow"},
        el("li", {}, el("b", {text: "New experiment. "}), "Start from a template, a YAML file, or a blank design."),
        el("li", {}, el("b", {text: "Data. "}), "Choose the files and the label and type columns."),
        el("li", {}, el("b", {text: "Cascade. "}), "Draw the stages and where each output goes."),
        el("li", {}, el("b", {text: "Evaluation. "}), "Choose the objective, the seeds and the protocol."),
        el("li", {}, el("b", {text: "Review & run. "}), "Save, start the run and follow it on Jobs."),
        el("li", {}, el("b", {text: "Results and Report. "}), "Read the cascade against the first stage alone.")))),
      card("Words used here", null, cardBody(el("dl", {class: "kv"},
        el("dt", {text: "Stage"}), el("dd", {text: "one model in the chain: a classifier (attack or benign), an anomaly detector (normal or anomalous) or a type stage (names the attack type)."}),
        el("dt", {text: "Route"}), el("dd", {text: "where one output of a stage sends a flow: another stage, ATTACK or BENIGN."}),
        el("dt", {text: "Seeds / repeated runs"}), el("dd", {text: "each seed draws new splits and refits everything; one seed is one run unit."}),
        el("dt", {text: "VAL and TEST"}), el("dd", {text: "every choice is made on VAL; TEST is read once, after the cascade is frozen."}),
        el("dt", {text: "First stage alone (B1)"}), el("dd", {text: "the entry model on its own: the baseline every cascade is compared with."}),
        el("dt", {text: "False-alarm rate (FPR)"}), el("dd", {text: "share of benign flows raised as attacks."}),
        el("dt", {text: "Percentage points (pp)"}), el("dd", {text: "the difference of two rates: 95% against 94% is +1 pp."}))))),
    el("div", {class: "grid-2"},
      card("Stopping and running again", null, cardBody(el("div", {class: "stack small"},
        el("p", {text: "Stop ends the job and every worker process it started. Units that finished stay on disk; the unit in progress is lost."}),
        el("p", {text: "Running again skips every unit whose config, data, split and package fingerprints match a finished one. " +
          "That is what resuming means here: there is no checkpoint inside a unit."}),
        el("p", {text: "One job runs at a time, because runs use a lot of memory and two runs of one config would write to the same folders."})))),
      card("In the builder", null, cardBody(el("ul", {class: "plain-list small"},
        el("li", {text: "Click a stage to edit it; drag it to move it. The layout is saved next to the config as a .layout.json file."}),
        el("li", {text: "Connect an output by dragging its port, by clicking the port and then the destination, or with the lists in the inspector."}),
        el("li", {text: "Keyboard: Tab to a port and press Enter, then Tab to the destination and press Enter. Escape cancels. Arrow keys move the selected stage; Delete removes it."}),
        el("li", {text: "The YAML view and the visual view edit the same draft. A visual edit that would drop comments asks first and shows the change."}))))),
    card("Documentation", null, cardBody(el("ul", {class: "plain-list"},
      el("li", {}, link("getting-started.md", "Getting started")), el("li", {}, link("config-reference.md", "Config reference")),
      el("li", {}, link("protocol.md", "The evaluation protocol")), el("li", {}, link("outputs.md", "Output files")),
      el("li", {}, link("dashboard.md", "The dashboard")), el("li", {}, link("limitations.md", "Limitations"))))));
}
