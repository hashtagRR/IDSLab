# IDSLab

**[hashtagRR.github.io/IDSLab](https://hashtagRR.github.io/IDSLab)**

[![PyPI](https://img.shields.io/pypi/v/idslab)](https://pypi.org/project/idslab/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23435536.svg)](https://doi.org/10.5281/zenodo.23435536)

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/idslab-overview.svg" width="100%"
       alt="IDSLab overview. Question: does every stage of your IDS cascade earn its place? 1 Your design: a binary classifier, a router and an anomaly gate, each with its model and decision, written in one YAML file. 2 One fixed protocol: splits per seed with TEST locked, each stage fitted on its own rows, models and thresholds chosen on VAL with off as an option, TEST read once next to controls. 3 The answer on synthetic data: the cascade and the first stage alone reach almost the same TEST accuracy; the entry classifier carries the result and the router and gate add nothing here.">
</p>

A framework for building and evaluating staged (cascade) network intrusion detection systems. You
describe a design in one YAML file, or draw it in the dashboard: the stages, the model in each, and
where each stage sends the flows it judges. IDSLab fits it, makes every choice on validation data,
and then shows on untouched test data whether the design beats its own first stage alone, and what
each stage adds.

## Quickstart

IDSLab needs Python 3.10 or newer and installs from [PyPI](https://pypi.org/project/idslab/).

```bash
python3 -m venv .venv && .venv/bin/pip install idslab
.venv/bin/idslab example demo                                  # synthetic data and three reference designs
.venv/bin/idslab run demo/r1_router_gate.yaml --out demo/runs  # fit, choose on VAL, report on TEST
.venv/bin/idslab dashboard --workspace demo --open             # optional web UI
```

On Windows, in PowerShell (the programs are in `.venv\Scripts`):

```powershell
py -3.12 -m venv .venv; .\.venv\Scripts\pip install idslab
.\.venv\Scripts\idslab example demo
.\.venv\Scripts\idslab run demo\r1_router_gate.yaml --out demo\runs
.\.venv\Scripts\idslab dashboard --workspace demo --open   # keep the window open; Ctrl+C stops it
```

The report is `demo/runs/r1-router-gate/report.html`. Step-by-step guides for Windows, macOS and
GNU/Linux, optional model libraries and troubleshooting are in
[INSTALL.md](https://github.com/hashtagRR/IDSLab/blob/main/INSTALL.md).

## Documentation

| Guide | What it covers |
|---|---|
| [How it works](https://github.com/hashtagRR/IDSLab/blob/main/docs/how-it-works.md) | one run in figures: the design, the splits, the accuracy after each stage, the frontier gap and the flow map |
| [Getting started](https://github.com/hashtagRR/IDSLab/blob/main/docs/getting-started.md) | a first run on the synthetic data, reading the report, moving to your own data |
| [Example designs](https://github.com/hashtagRR/IDSLab/blob/main/examples/README.md) | the three reference cascades with diagrams, a custom model adapter, an outside score job |
| [Config reference](https://github.com/hashtagRR/IDSLab/blob/main/docs/config-reference.md) | every key of a config file, with defaults |
| [Protocol and controls](https://github.com/hashtagRR/IDSLab/blob/main/docs/protocol.md) | splits, greedy selection on VAL, the controls and what each reported number means |
| [Models in stages](https://github.com/hashtagRR/IDSLab/blob/main/docs/models.md) | score contracts, pipelines and search, PyTorch, your own adapter, scores from models trained elsewhere |
| [Outputs](https://github.com/hashtagRR/IDSLab/blob/main/docs/outputs.md) | the report pages and every file a run writes |
| [Comparing designs and sweeps](https://github.com/hashtagRR/IDSLab/blob/main/docs/compare-and-sweeps.md) | two designs on the same flows, paired per seed; one base config with many variants |
| [Command line](https://github.com/hashtagRR/IDSLab/blob/main/docs/cli.md) | `validate`, `run`, `report`, `export-splits`, `example`, `compare`, `sweep`, `dashboard` |
| [Dashboard](https://github.com/hashtagRR/IDSLab/blob/main/docs/dashboard.md) | the local web UI: pages, jobs, security ([source reference](https://github.com/hashtagRR/IDSLab/blob/main/src/idslab/dashboard/README.md)) |
| [Architecture](https://github.com/hashtagRR/IDSLab/blob/main/docs/architecture.md) | what happens during a run, the modules, and where to extend |
| [Known limitations](https://github.com/hashtagRR/IDSLab/blob/main/docs/limitations.md) | what the numbers do and do not show |
| [Design notes](https://github.com/hashtagRR/IDSLab/blob/main/DESIGN.md) | the reasoning behind each protocol rule |
| [Install guide](https://github.com/hashtagRR/IDSLab/blob/main/INSTALL.md) | platform setup, extras, tests and checks |
| [Changelog](https://github.com/hashtagRR/IDSLab/blob/main/CHANGELOG.md) | what changed in each version |
| [Project site](https://hashtagRR.github.io/IDSLab) | the project's overview page |

## Versions

Every release and its changes are in the
[changelog](https://github.com/hashtagRR/IDSLab/blob/main/CHANGELOG.md) and on the
[Releases page](https://github.com/hashtagRR/IDSLab/releases). Releases from 0.3.0 on are archived
on Zenodo under the concept DOI above. From 0.3.1 on, a release that does not change the evaluation
keeps finished runs; upgrades to 0.2.0 and 0.3.0 ran them again.

- **`v0.3.0`**: the dashboard redesigned around experiments, with a guided setup, a results page
  that reads the cascade against its first stage, and job progress.
- **`v0.2.0`**: visual pipeline builder, cost objectives and stage cost, plugins, `idslab compare`
  and `idslab sweep`.
- **`v0.1.0`**: first public release.

## Citing

Cite the version you used. The concept DOI
[10.5281/zenodo.23435536](https://doi.org/10.5281/zenodo.23435536)
always resolves to the latest version, and lists each version's own DOI and citation.
[CITATION.cff](https://github.com/hashtagRR/IDSLab/blob/main/CITATION.cff) holds the same details,
and GitHub shows them under "Cite this repository".

## License

AGPL-3.0-or-later. See [LICENSE](https://github.com/hashtagRR/IDSLab/blob/main/LICENSE).
