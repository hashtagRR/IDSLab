# Changelog

Finished run units are reused while their config, data and results version fingerprints match. A
release says when it raises the results version, which makes every experiment run once more; other
releases keep finished runs.

## Unreleased

## 0.3.2 (2026-10-17)

Documentation only; the code and the evaluation are unchanged, and finished runs are reused.

- A shorter README: overview, quickstart for every system, guides, versions and citation.
- `docs/how-it-works.md` explains a run with its figures: the design, the splits, the accuracy after
  each stage, the frontier gap and the flow map.
- The project site describes the current builder, results page and fingerprints, and links the DOI.

## 0.3.1 (2026-10-17)

- Finished run units survive releases that do not change the evaluation. Fingerprints carry a results
  version (`idslab.results_version.RESULTS_VERSION`, now 1) instead of the package version, and it
  changes only with a release that changes what a run computes. Units from 0.2.0 and 0.3.0 count as
  results version 1, so this release reuses them; `DONE` also records the release that wrote the unit.
- IDSLab is on PyPI: `pip install idslab`. The install guides for Windows, macOS and GNU/Linux use
  it, so they no longer need the source code; installing from source is described separately for
  changing IDSLab itself.

## 0.3.0 (2026-10-17)

The evaluation is unchanged: the same config and data give the same per-flow predictions, frozen
configurations and controls as in 0.2.0. The version is part of every unit's fingerprint, so finished
0.2.0 units are not reused: run each experiment once under 0.3.0 before `idslab report` can rebuild
its report (a report counts only units of the running version). The dashboard marks results written
by another version and says so before a rebuild.

Added:

- Dashboard redesign around experiments. The home page lists each config with the results written
  under its name, its state and the next step. Outputs are matched to configs by name, and by the
  stored config fingerprints when names collide; outputs that match no config, or several, stay
  readable on their own.
- New experiment from a template (a copy with its own name), from YAML, or from a blank design.
  Templates, Datasets, Jobs, Reports and Help pages.
- Guided setup in four steps (Data, Cascade, Evaluation, Review & run), sharing one draft with the
  YAML view. Typed YAML keeps its comments; a visual edit that would drop them asks first and shows
  the difference.
- Builder: click-to-route and keyboard routing, focusable ports and stages, missing routes, loops
  and unreached stages drawn and listed, and an inspector in sections. Parameters the forms do not
  cover are kept as written.
- Results page: the TEST objective against the first stage alone, read with the per-unit bootstrap,
  in percentage points or as a cost; stage effects, unseen attacks and types, comparisons with every
  control, reproducibility. The frozen cascade of a unit is drawn from the routes it stored.
- Jobs page with progress from the job's log (units to run, finished, reused, the current phase),
  a running-job indicator on every page, and past activity read from the workspace files.
- Runs log the unit count and each unit finished or reused; sweeps log the variant number.
  `summary.json` stores the cascade's routes (`cascade`), and `Config.outline()` returns them.
- Optional browser tests (`pip install -e .[browser-tests]`).

Changed:

- Earlier dashboard addresses (`#/config`, `#/builder`, `#/run`, `#/job`, `#/command/...`,
  `#/example`) redirect to the pages that now hold the same thing.
- `idslab dashboard` on a folder that does not exist says how to create it (`idslab example demo`)
  instead of an unexpected-error line. The install guides make creating the demo folder its own
  step before the dashboard, and the command-line run an optional step after it.

## 0.2.0 (2026-10-17)

Finished run units from 0.1.0 are not reused: the version is part of every unit's fingerprint, and the
fixes below change what a fingerprint covers.

Added:

- Visual pipeline builder in the dashboard: drag stages onto a canvas, wire outputs to stages or
  terminals, drag score edges for features, and edit every stage setting in a panel (models from a
  catalog with parameter forms, search spaces, pipeline steps, decisions, training rows). A dataset
  panel reads a sample of the data and suggests the label, type and categorical columns. The config is
  written with the loader's rules and validated on every change; node positions are saved next to the
  config (`idslab.config.dump_yaml` writes configs that read back unchanged).
- Cost objectives: `protocol.objective: {cost: {missed_attack: A, false_alarm: B}}` chooses on VAL by
  the lowest average cost per flow; `{recall_at_fpr: X}` by the highest recall within a false alarm
  cap. The objective appears as its own column in every report.
- Stage cost: each run records the share of TEST flows reaching every stage and its scoring time; a
  stage may declare `cost:`. Reports add the expected cost per flow of the cascade against its first
  stage alone.
- `protocol.search: greedy_fixed`: later stages with a fixed decision stay on while earlier stages are
  judged. On the synthetic R2 example it raised VAL and TEST accuracy in each of 3 seeds.
- Plugins through the entry point group `idslab.plugins`: models and pipeline steps for the builder
  catalog, and named objectives.
- `idslab compare`: experiments side by side, and for experiments on the same data and splits a
  paired comparison per unit on the same TEST flows, with paired bootstrap intervals. Writes
  `compare.md`, `compare.json` and `compare.html`.
- `idslab sweep`: one base config with named variants or a grid of dotted-key changes; every variant
  is checked first, written as its own config, run, reported and compared.
- Dashboard: Compare and Sweeps pages; compare and sweep run as dashboard jobs.

Fixed:

- Chained score dependencies (a stage that feeds another stage while using a third stage's scores)
  are refused at config load. Their out-of-fold scores were not fully out of fold: the inner stage's
  fold refits used features from models that had seen the held-out fold.
- External score sidecars (`.json`) are part of the unit fingerprint, so editing a sidecar reruns the
  unit and its provenance is checked again; before, the finished unit was skipped.
- Reports check each unit's external score files against the current ones and leave out units whose
  files changed, naming the reason; before, stale results from replaced files stayed in the report.
- Dashboard on Windows: Stop ends the job's worker processes too, and a path on another drive or with
  a colon is refused with a message instead of an error.

Documentation:

- A short README with an overview figure and a table of guides; `INSTALL.md` with step-by-step guides
  for Windows, macOS and GNU/Linux from the download to the dashboard; `docs/architecture.md`,
  `docs/limitations.md`, `docs/compare-and-sweeps.md`; mini READMEs for the example designs and the
  dashboard source. Figures are drawn by `docs/img/make_figures.py` from a real run.
- A project site (`docs/index.html`, served by GitHub Pages from `docs/`).
- IDSLab is not on PyPI yet; the guides install it from GitHub
  (`pip install git+https://github.com/hashtagRR/IDSLab.git`).

## 0.1.0 (2026-10-16)

First public release.

- Documentation in `docs/`: getting started, config reference, protocol and controls, models, outputs,
  command line, dashboard.
- `examples/custom_adapter.py` (a model behind its own adapter class) and
  `examples/external_score_job.py` (an outside training job that writes an accepted score file),
  both covered by the test suite.
- Package metadata for PyPI, a citation file and a test workflow.

## 0.0.6 (2026-10-16)

- `idslab dashboard`: a local web dashboard over a workspace folder. Every CLI command from the
  browser as a background job with a live log and a stop button; config editor with validation as you
  type and a cascade preview; run and unit views; report pages; file browser.
- `idslab run` and `export-splits` log progress to stderr.
- YAML syntax errors are config errors (exit code 2) instead of a traceback.
- The report's cascade map ends each route to BENIGN or ATTACK under its stage, so no edge crosses a
  stage.

## 0.0.5 (2026-10-16)

- `report.html`: a self-contained page with headline figures, the cascade map, the systems table, the
  unseen-attack grid, attack types, the joint check and the frozen configuration per run.
- `idslab.torch_models`: `TorchMLPClassifier` and `TorchAutoencoder` (extra `torch`).

## 0.0.4 (2026-10-16)

- External score candidates (`external: {path}`) with provenance checks, and `idslab export-splits`.
- `protocol.search: joint`: a check of every decision combination next to greedy selection.

## 0.0.3 (2026-10-16)

- Modes `heldout_family` and `natural_novelty`, with recall on unseen attacks and the frontier gap on
  benign and unseen flows.

## 0.0.2 (2026-10-16)

- Score features from earlier stages (`extra_features`, out-of-fold on FIT).
- Multiclass stages with the annotate and decide roles, and type metrics.
- Detectors refitted on each batch (`fit_on: pool`), specialist training rows (`train_on: hard`),
  `top_fraction` decisions.

## 0.0.1 (2026-10-15)

- Config loading and checks, splits, classifier and detector stages, greedy selection on VAL with an
  off option, the controls, Markdown and JSON reports, resume by fingerprint, the CLI and the
  synthetic example.
