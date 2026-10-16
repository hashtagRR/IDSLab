# IDSLab

[![tests](https://github.com/hashtagRR/IDSLab/actions/workflows/tests.yml/badge.svg)](https://github.com/hashtagRR/IDSLab/actions/workflows/tests.yml)

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/idslab-overview.svg" width="100%"
       alt="IDSLab overview. Question: does every stage of your IDS cascade earn its place? 1 Your design: a binary classifier, a router and an anomaly gate, each with its model and decision, written in one YAML file. 2 One fixed protocol: splits per seed with TEST locked, each stage fitted on its own rows, models and thresholds chosen on VAL with off as an option, TEST read once next to controls. 3 The answer on synthetic data: the cascade and the first stage alone reach almost the same TEST accuracy; the entry classifier carries the result and the router and gate add nothing here.">
</p>

A config-driven framework for evaluating staged (cascade) network intrusion detection systems.
Describe the dataset, the stages, the model at each stage and the routes between them in one YAML
file; IDSLab fits the stages, chooses every model and threshold on validation data, freezes the
design, and reports it on the test set next to controls built from its own first stage, so you see
whether the extra stages earn their place.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/idslab example demo                                  # synthetic data, three reference designs
.venv/bin/idslab run demo/r1_router_gate.yaml --out demo/runs  # fit, choose on VAL, report on TEST
.venv/bin/idslab dashboard --workspace demo --open             # optional web UI
```

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/dashboard-run.png" width="90%"
       alt="The IDSLab dashboard showing one experiment: headline figures, a systems chart, cascade minus each control, stage effects and the frozen cascade of seed 0.">
</p>

Optional model libraries (XGBoost, LightGBM, PyOD, imbalanced-learn, PyTorch), Windows and
troubleshooting: [INSTALL.md](https://github.com/hashtagRR/IDSLab/blob/main/INSTALL.md).

## Documentation

| Guide | What it covers |
|---|---|
| [Getting started](https://github.com/hashtagRR/IDSLab/blob/main/docs/getting-started.md) | a first run on the synthetic data, reading the report, moving to your own data |
| [Example designs](https://github.com/hashtagRR/IDSLab/blob/main/examples/README.md) | the three reference cascades with diagrams, a custom model adapter, an outside score job |
| [Config reference](https://github.com/hashtagRR/IDSLab/blob/main/docs/config-reference.md) | every key of a config file, with defaults |
| [Protocol and controls](https://github.com/hashtagRR/IDSLab/blob/main/docs/protocol.md) | splits, greedy selection on VAL, the controls and what each reported number means |
| [Models in stages](https://github.com/hashtagRR/IDSLab/blob/main/docs/models.md) | score contracts, pipelines and search, PyTorch, your own adapter, scores from models trained elsewhere |
| [Outputs](https://github.com/hashtagRR/IDSLab/blob/main/docs/outputs.md) | the report pages and every file a run writes |
| [Command line](https://github.com/hashtagRR/IDSLab/blob/main/docs/cli.md) | `validate`, `run`, `report`, `export-splits`, `example`, `dashboard` |
| [Dashboard](https://github.com/hashtagRR/IDSLab/blob/main/docs/dashboard.md) | the local web UI: pages, jobs, security ([source reference](https://github.com/hashtagRR/IDSLab/blob/main/src/idslab/dashboard/README.md)) |
| [Architecture](https://github.com/hashtagRR/IDSLab/blob/main/docs/architecture.md) | what happens during a run, the modules, and where to extend |
| [Known limitations](https://github.com/hashtagRR/IDSLab/blob/main/docs/limitations.md) | what the numbers do and do not show |
| [Design notes](https://github.com/hashtagRR/IDSLab/blob/main/DESIGN.md) | the reasoning behind each protocol rule |
| [Install guide](https://github.com/hashtagRR/IDSLab/blob/main/INSTALL.md) | platform setup, extras, tests and checks |
| [Changelog](https://github.com/hashtagRR/IDSLab/blob/main/CHANGELOG.md) | what changed in each version |

## Versions

- **`v0.1.0`** (latest): first public release. Any acyclic cascade of classifier, type and
  detector stages; greedy selection on VAL with an off option; first-stage controls, frontier gap
  and paired bootstrap; held-out family and natural novelty modes; external scores with provenance
  checks; Markdown, JSON and HTML reports; the web dashboard.

## Citing

If you use IDSLab in published work, please cite the version you used;
[CITATION.cff](https://github.com/hashtagRR/IDSLab/blob/main/CITATION.cff) has the details, and GitHub
shows them under "Cite this repository".

## License

AGPL-3.0-or-later. See [LICENSE](https://github.com/hashtagRR/IDSLab/blob/main/LICENSE).
