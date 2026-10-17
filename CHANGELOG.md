# Changelog

Every version that can change results gets a new number: finished run units are reused only while
the config, data and IDSLab version fingerprints match.

## Unreleased

- Install guide rewritten as three step-by-step guides (Windows, macOS, GNU/Linux) from the download
  (ZIP or Git) to the dashboard, each with its own troubleshooting table; a one-line install from
  GitHub (`pip install git+https://github.com/hashtagRR/IDSLab.git`) replaces the PyPI command until
  IDSLab is published there.
- Dashboard on Windows: Stop ends the job's worker processes too, and a path on another drive or
  with a colon is refused with a message instead of an error.
- Project site (`docs/index.html`, served by GitHub Pages from `docs/`).
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
- The builder offers these settings (objective, search, declared cost).
- Visual pipeline builder in the dashboard: drag stages onto a canvas, wire outputs to stages or
  terminals, drag score edges for features, and edit every stage setting in a panel (models from a
  catalog with parameter forms, search spaces, pipeline steps, decisions, training rows). A dataset
  panel reads a sample of the data and suggests the label, type and categorical columns. The config is
  written by the server with the loader's rules and validated on every change; node positions are
  saved next to the config.
- `idslab.config.dump_yaml` writes a config in the shape people write by hand and reads back unchanged.
- Documentation reorganised: a short README with an overview figure and a table of guides,
  `INSTALL.md`, `docs/architecture.md`, `docs/limitations.md`, and mini READMEs for the example designs
  and the dashboard source. Figures (overview, splits, selection, frontier gap, the three reference
  designs) are drawn by `docs/img/make_figures.py`; dashboard and report screenshots added.

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
