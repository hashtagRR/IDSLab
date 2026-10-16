# Changelog

Every version that can change results gets a new number: finished run units are reused only while
the config, data and IDSLab version fingerprints match.

## Unreleased

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
