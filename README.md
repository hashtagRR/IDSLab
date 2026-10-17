# IDSLab

**[hashtagRR.github.io/IDSLab](https://hashtagRR.github.io/IDSLab)**

[![tests](https://github.com/hashtagRR/IDSLab/actions/workflows/tests.yml/badge.svg)](https://github.com/hashtagRR/IDSLab/actions/workflows/tests.yml)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23435536.svg)](https://doi.org/10.5281/zenodo.23435536)

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/idslab-overview.svg" width="100%"
       alt="IDSLab overview. Question: does every stage of your IDS cascade earn its place? 1 Your design: a binary classifier, a router and an anomaly gate, each with its model and decision, written in one YAML file. 2 One fixed protocol: splits per seed with TEST locked, each stage fitted on its own rows, models and thresholds chosen on VAL with off as an option, TEST read once next to controls. 3 The answer on synthetic data: the cascade and the first stage alone reach almost the same TEST accuracy; the entry classifier carries the result and the router and gate add nothing here.">
</p>

IDSLab tests staged (cascade) network intrusion detection systems. You describe a design in one
YAML file: which stages it has, which model sits in each, and where each stage sends the flows it
judges. IDSLab fits it, makes every choice on validation data, and then tells you on untouched test
data whether the design is better than its own first stage alone, and what each stage adds.

## Why a cascade needs this

A staged IDS splits detection into steps. A first classifier sees every flow; later stages (a second
classifier, an anomaly detector, a type classifier) re-check the flows it sends them, to catch
attacks it missed or clear its false alarms. Many published designs report a gain over a single model,
and the gain is easy to overstate:

- thresholds or stages are chosen while looking at the test set;
- the cascade is compared with a different single model, not with its own first stage tuned as
  carefully;
- the same gain could come from moving the first stage's threshold, no extra stage needed;
- nobody checks what each stage contributes, so a stage that does nothing stays in the design.

IDSLab rules these out for any design you can describe, with the same protocol every time, so two
designs (or two papers) compare on equal terms.

## What a run does

**1. You describe the design.** Stages, models, decisions and routes:

```yaml
name: r1-router-gate
dataset: {train: data/train.parquet, test: data/test.parquet, label: label, type: attack_cat}
entry: binary
stages:
  binary:                        # sees every flow
    kind: classifier
    pipeline: [{class: idslab.prep.Encode, params: {categorical: [proto, service]}}]
    candidates: [{class: sklearn.ensemble.RandomForestClassifier}]
    decision: {threshold: 0.5, off: benign}
    routes: {attack: router, benign: BENIGN}
  router:                        # keeps confident attacks, sends the rest to the gate
    kind: classifier
    pipeline: [{class: idslab.prep.Encode, params: {categorical: [proto, service]}}]
    candidates: [{class: sklearn.ensemble.RandomForestClassifier}]
    decision: {grid: [0.5, 0.6, 0.7, 0.8, 0.9], off: attack}
    routes: {attack: ATTACK, benign: gate}
  gate:                          # anomaly detector trained on benign flows only
    kind: detector
    fit_on: benign
    pipeline: [{class: idslab.prep.Encode, params: {categorical: [proto, service]}}]
    candidates: [{class: sklearn.ensemble.IsolationForest}]
    decision: {budget: [0.01, 0.02, 0.05], off: normal}
    routes: {anomalous: ATTACK, normal: BENIGN}
```

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/design-r1-router-gate.svg" width="85%"
       alt="Cascade map of this design: binary sends benign flows to BENIGN and attacks to router; router sends attacks to ATTACK and the rest to gate; gate sends anomalous flows to ATTACK and normal ones to BENIGN.">
</p>

Any acyclic graph works: stages can re-check, clear alarms, name attack types, or feed their scores to
later stages. Any model can sit in a stage: scikit-learn, XGBoost, LightGBM, PyOD, PyTorch, your own
class, or scores from a model trained elsewhere.

**2. It splits the data per seed and locks TEST away.**

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/splits.svg" width="85%"
       alt="Splits per seed: FIT-base fits every stage, CAL calibrates calibrated stages, VAL makes every choice, TEST is read once after the configuration is frozen.">
</p>

**3. It makes every choice on VAL**, one stage at a time: which candidate model, which threshold, and
whether the stage stays on at all. Switching a stage off is always an option, and ties go to off.

**4. It reads TEST once**, for the frozen cascade and for controls built from the same fitted first
stage, over several seeds.

## What you learn

Every report answers three questions. The figures below are one seed of the design above on the
synthetic example data (`idslab example`).

**Does the cascade beat its first stage alone?** Here VAL said the extra stages help; TEST says they
cost 0.005. The report gives the difference with paired bootstrap intervals and the spread over seeds.

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/stage-steps.svg" width="75%"
       alt="Accuracy of the cascade cut after each stage. VAL rises from 0.8267 with the first stage to 0.8375 with the router and 0.8383 with the gate. TEST falls from 0.8443 to 0.8397 and 0.8390.">
</p>

**Could a threshold do the same?** The frontier gap compares the cascade's detection rate with the
best the first stage reaches at the same false alarm rate by moving its threshold. A positive gap means
the stages reach a point no threshold can; here the cascade sits just below the first stage's curve.

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/frontier-gap.svg" width="70%"
       alt="ROC curve of the first stage near the cascade's operating point. The cascade lies 0.010 below the curve's upper hull at its false positive rate.">
</p>

**What does each stage do?** Every flow's path is recorded. Here the router passes 73 attacks to the
gate, and the gate catches none of them; switching the gate off costs nothing, and the report's stage
table says so.

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/flow-map.svg" width="100%"
       alt="Flow map of 3,000 TEST flows through binary, router and gate, split into true attacks and true benign flows. Binary calls 2,125 flows benign, 352 of them attacks. Router flags 743, 687 of them attacks, and passes 132 to the gate, 73 of them attacks. Gate flags 2 benign flows and misses all 73 attacks.">
</p>

The same run also gives the configuration chosen in every seed, the full selection trace, recall on
attack families held out of training, and per-type results when the design names attack types.

## Quickstart

Download the code (green **Code** button, **Download ZIP**, then extract; or `git clone
https://github.com/hashtagRR/IDSLab.git`) and open a terminal in the folder that holds
`pyproject.toml`. [INSTALL.md](https://github.com/hashtagRR/IDSLab/blob/main/INSTALL.md) has
step-by-step guides for Windows, macOS and GNU/Linux.

**GNU/Linux and macOS**

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/idslab example demo                                  # creates demo/: data and three designs
.venv/bin/idslab run demo/r1_router_gate.yaml --out demo/runs  # fit, choose on VAL, report on TEST
.venv/bin/idslab dashboard --workspace demo --open             # optional web UI
```

**Windows (PowerShell)**: the programs are in `.venv\Scripts`, and PowerShell needs the leading `.\`

```powershell
py -3.12 -m venv .venv; .\.venv\Scripts\pip install -e .
.\.venv\Scripts\idslab example demo                      # creates the demo folder the dashboard opens
.\.venv\Scripts\idslab run demo\r1_router_gate.yaml --out demo\runs
.\.venv\Scripts\idslab dashboard --workspace demo --open   # keep the window open; Ctrl+C stops it
```

The result is `demo/runs/r1-router-gate/report.html`, or the same in the dashboard:

<p align="center">
  <img src="https://raw.githubusercontent.com/hashtagRR/IDSLab/main/docs/img/dashboard-run.png" width="90%"
       alt="The IDSLab dashboard showing the results of one experiment: the cascade against the first stage alone on the TEST objective, the bootstrap reading, stage effects and the comparisons with every control.">
</p>

Step-by-step Windows setup, optional model libraries and troubleshooting:
[INSTALL.md](https://github.com/hashtagRR/IDSLab/blob/main/INSTALL.md).

## Documentation

| Guide | What it covers |
|---|---|
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

- **`v0.3.0`** (latest): the dashboard redesigned around experiments: a guided setup (data, cascade,
  evaluation, review and run) sharing one draft with the YAML view, templates, datasets, jobs with
  real progress, a results page that reads the cascade against the first stage with the per-unit
  bootstrap, and reports. Clear messages for a missing workspace folder. Results are unchanged;
  runs from 0.2.0 rerun once.
- **`v0.2.0`**: visual pipeline builder; cost objectives and stage cost; `greedy_fixed`
  selection; plugins; `idslab compare` with paired per-seed differences and `idslab sweep`; fixes for
  chained score dependencies and external-score freshness. Runs from 0.1.0 rerun.
- **`v0.1.0`**: first public release. Any acyclic cascade of classifier, type and
  detector stages; greedy selection on VAL with an off option; first-stage controls, frontier gap
  and paired bootstrap; held-out family and natural novelty modes; external scores with provenance
  checks; Markdown, JSON and HTML reports; the web dashboard.

## Citing

If you use IDSLab in published work, please cite the version you used. Every release is archived on
Zenodo with its own DOI: [10.5281/zenodo.23435536](https://doi.org/10.5281/zenodo.23435536) opens the
latest release, and its list of versions leads to each one's DOI and a ready-made citation.
[CITATION.cff](https://github.com/hashtagRR/IDSLab/blob/main/CITATION.cff) holds the author and that
DOI, and GitHub shows them under "Cite this repository".

## License

AGPL-3.0-or-later. See [LICENSE](https://github.com/hashtagRR/IDSLab/blob/main/LICENSE).
