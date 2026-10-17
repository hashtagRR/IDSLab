// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 hashtagRR
//
// Jobs: the current (or last) job with its progress and log, and past activity read from files.
// Progress is what the job's own log says (phase, finished units); nothing is estimated.

import {
  el, api, enc, fileUrl, dur, ago, card, cardBody, pageHead, table, empty, note, toast, stateBadge,
  logView, fillLog, job, onJob, ask,
} from "./core.js";

// one line for the top bar and the sidebar
export function jobLine(s) {
  if (!s || s.state === "idle") return null;
  const p = s.progress || {};
  const bits = [`idslab ${s.command}`];
  if (s.state === "running") {
    if (p.variants_total) bits.push(`variant ${p.variant_index} of ${p.variants_total}`);
    const done = s.parallel ? s.units_done_on_disk : p.units_done;
    if (p.units_total) bits.push(`${done ?? 0} of ${p.units_total} units`);
    bits.push(dur(s.now - s.started_at));
  }
  return bits.join(", ");
}

function progressBlock(s) {
  const p = s.progress || {};
  const box = el("div", {class: "stack"});
  if (p.variants_total) box.append(el("div", {}, el("b", {text: "Sweep: "}), `variant ${p.variant_index} of ${p.variants_total} (${p.variant})`));
  if (p.units_total) {
    const done = s.parallel ? (s.units_done_on_disk ?? 0) : p.units_done;
    box.append(el("div", {class: "units-strip", role: "img", "aria-label": `${done} of ${p.units_total} units finished`},
      Array.from({length: p.units_total}, (_, i) => el("span", {class: i < done ? "done" : i === done && s.state === "running" ? "now" : null}))),
    el("div", {}, el("b", {text: "Units: "}), `${done} of ${p.units_total} finished` + (p.units_reused ? ` (${p.units_reused} reused from an earlier run)` : "")));
  }
  if (s.parallel && s.state === "running") {
    box.append(note("Units run in parallel worker processes, which do not log. The count above is read from the DONE files written since the job started.", "info"));
  } else if (p.phase && s.state === "running") {
    box.append(el("div", {}, el("b", {text: "Now: "}), `${p.unit ? `${p.unit}, ` : ""}${p.phase}`));
  }
  if (!p.units_total && !p.phase && s.state === "running") box.append(el("div", {class: "muted", text: "Started; waiting for the first progress line."}));
  return box;
}

function failure(s) {
  const err = [...s.log].reverse().find((l) => l.startsWith("idslab: "));
  const box = el("div", {class: "stack"});
  if (err) box.append(el("div", {class: "note bad"}, el("b", {text: "The command stopped with an error: "}), err.slice(8)));
  else box.append(note(`The command ended with exit code ${s.returncode}. The log below has the details.`, "bad"));
  const tips = [];
  if (/rerun with --debug/.test(err || "")) tips.push("Run again with Show full tracebacks (under Advanced on Review & run) for the full traceback.");
  if (/not found|No such file/.test(err || "")) tips.push("Check the data paths in the Data step; they are relative to the config file.");
  if (/MemoryError|Killed/.test(s.log.slice(-20).join("\n"))) tips.push("The machine ran out of memory: subsample the training rows, use fewer parallel units, or choose lighter models.");
  if (tips.length) box.append(el("ul", {class: "plain-list small"}, tips.map((t) => el("li", {text: t}))));
  if (s.config) box.append(el("div", {class: "row"}, el("a", {class: "btn small", href: `#/e/${enc(`c:${s.config}`)}/setup/review`, text: "Open the experiment"})));
  return box;
}

export async function jobsView(view) {
  const head = el("div");
  const prog = el("div");
  const info = el("div");
  const log = logView([]);
  let full = false;
  const stop = el("button", {class: "btn danger", type: "button", text: "Stop"});
  stop.addEventListener("click", async () => {
    if (!(await ask("Stop the job", "Stop ends the job and every worker process it started. Finished units stay on disk; the unit in progress is lost. " +
      "Running again later reuses the finished units (matched by fingerprint); nothing inside a unit is checkpointed.", "Stop", "danger"))) return;
    try { await api("/api/job/stop", {}); } catch (e) { toast(e.message); }
  });
  const fullBtn = el("button", {class: "btn small", type: "button", text: "Show the full log"});
  fullBtn.addEventListener("click", async () => {
    full = true;
    const s = await api("/api/job");
    fillLog(log, s.log);
    fullBtn.remove();
  });
  const show = (s) => {
    if (!s) return;
    if (s.state === "idle") {
      head.replaceChildren(pageHead("Jobs", "No job has run since the dashboard started."));
      prog.replaceChildren(card("Start a job", null, empty("Runs start from an experiment's Review & run step; reports, example data and exports from their pages.",
        el("a", {class: "btn primary", href: "#/", text: "Experiments"}))));
      info.replaceChildren();
      log.classList.add("hidden");
      return;
    }
    log.classList.remove("hidden");
    const end = s.finished_at || s.now;
    const target = s.target_kind === "experiment" ? `#/run/${enc(s.target)}` : `#/files/${enc(s.target || "")}`;
    head.replaceChildren(pageHead(`idslab ${s.command}`, null,
      [s.state === "running" ? stop : null,
        s.state !== "running" && s.target ? el("a", {class: "btn primary", href: target,
          text: s.target_kind === "experiment" ? "View results" : "Open the folder"}) : null],
      [stateBadge(s.state), el("span", {class: "muted small", text: `${s.state === "running" ? "running for" : "took"} ${dur(end - s.started_at)}` +
        (s.returncode != null ? `, exit code ${s.returncode}` : "")})]));
    prog.replaceChildren(card(s.state === "running" ? "Progress" : s.state === "failed" ? "What went wrong" : "Result", null,
      cardBody(s.state === "failed" ? failure(s) : s.state === "stopped"
        ? el("div", {class: "stack"}, note("Stopped from the dashboard. Finished units are kept; run again to continue, reusing them.", "warn"), progressBlock(s))
        : progressBlock(s))));
    info.replaceChildren(el("dl", {class: "kv"},
      el("dt", {text: "Command"}), el("dd", {class: "mono wrap", text: `idslab ${(s.argv || []).join(" ")}`}),
      s.config ? [el("dt", {text: "Config"}), el("dd", {class: "mono", text: s.config})] : null,
      el("dt", {text: "Writes to"}), el("dd", {class: "mono", text: s.target || "n/a"}),
      el("dt", {text: "Started"}), el("dd", {text: new Date(s.started_at * 1000).toLocaleString()}),
      el("dt", {text: "Log"}), el("dd", {}, `${s.log_lines} lines`, s.log_lines > s.log.length && !full ? [" (the last ", String(s.log.length), " shown) ", fullBtn] : null)));
    if (!full || s.state === "running") fillLog(log, s.log);
  };
  view.append(head, prog, card("Job details and log", null, cardBody(el("div", {class: "stack"}, info, log))));
  show(job.snapshot);
  onJob(show);

  const history = await api("/api/history");
  const label = {unit: "unit finished", report: "report written", comparison: "comparison written"};
  view.append(card("Past activity", "Read from the files in the workspace: finished units, reports and comparisons. " +
    "The dashboard keeps the log of the latest job only.", history.length ? el("div", {class: "card-body flush"}, table(["When", "What", "Where"],
    history.slice(0, 60).map((h) => [ago(h.time), label[h.kind] || h.kind,
      h.kind === "unit" ? el("a", {class: "link mono small", href: `#/unit/${enc(h.path)}`, text: `${h.experiment}, ${h.label}`})
        : h.kind === "report" ? el("a", {class: "link mono small", href: `#/run/${enc(h.path)}/report`, text: h.path})
          : el("a", {class: "link mono small", href: fileUrl(`${h.path}/compare.html`), target: "_blank", rel: "noopener", text: h.path})])))
    : empty("Nothing has been run in this workspace yet.")));
}
