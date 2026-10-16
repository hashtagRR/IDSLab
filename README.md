# IDSLab

IDSLab evaluates staged (cascade) intrusion detection systems. You describe the dataset, the stages,
the model at each stage and the routing between stages in one YAML file. IDSLab fits the stages,
chooses every model and threshold on validation data, freezes the design, and reports it on the test
set next to controls that show whether the extra stages beat the first stage alone.

Status: version 0.0.6. Classifier, type
(multiclass) and detector stages; upstream scores as features (out-of-fold); detectors fitted once or
refitted on each batch; greedy selection on VAL; all binary controls plus type metrics; standard, held-out
family (zero-day) and natural novelty modes; scores made elsewhere (for example a CNN trained on a
GPU) with provenance checks; a joint search check on greedy selection; PyTorch models; Markdown, JSON
and HTML reports; a local web dashboard.

## Install

```bash
pip install -e .            # from a clone; Python 3.10 or newer
pip install -e ".[dev]"     # with the test and check tools
```

Optional model libraries: `pip install -e ".[xgboost]"`, `".[lightgbm]"`, `".[pyod]"`, `".[imbalance]"`,
`".[torch]"` (for `idslab.torch_models.TorchMLPClassifier` and `TorchAutoencoder`).

## Quick start

```bash
idslab example demo                         # synthetic data and three reference configs
idslab validate demo/r1_router_gate.yaml    # checks the config without reading data
idslab run demo/r1_router_gate.yaml --out demo/runs
```

`demo/runs/r1-router-gate/report.md` and `report.html` hold the result (the HTML page adds a map of
the cascade). Each seed also writes `summary.json`
(selected configuration, selection trace, controls), `per_flow.parquet`, `val_flow.parquet` and
`split_ids.parquet`.

## Dashboard

```bash
idslab dashboard --workspace demo --open    # http://127.0.0.1:8765/
```

A local web page over one workspace folder. Everything the CLI does is there: validate and edit
configs (with a live cascade preview), run, rebuild reports, export splits and write the example
data. Commands run as the CLI in the background, one at a time, with a live log and a stop button,
so a run started in the browser and one started in a shell resume each other. Results open as
headline figures and charts, per-unit selection traces and the full report page. The server binds
to 127.0.0.1, reads and writes only inside the workspace, and refuses requests from other sites.

## What every run guarantees

- Splits per seed: FIT-base and CAL (calibration only) inside FIT, VAL for every choice, TEST last.
- TEST labels never influence fitting, selection or any configuration choice; the test suite checks
  this by shuffling TEST labels and requiring an identical selection.
- Every stage chosen on VAL has an "off" option, and ties go to off.
- Controls in every report: the first stage alone (its own threshold and the best VAL cutoff), the
  first stage at the cascade's VAL false-alarm rate, the frontier gap against the first stage's ROC
  curve, each stage switched off, additive prefix steps, a prevalence check and paired bootstrap
  intervals.

## Unseen attacks

Two modes test how a design copes with attacks it never saw:

```yaml
protocol:
  mode: heldout_family        # each attack family in turn leaves training; TEST keeps it
  families: [DoS, Worms]      # optional; default every attack family
---
protocol:
  mode: natural_novelty       # TEST holds types that never occur in training
  novel_types: [Zeroday]      # raw type names, checked against the train partition
```

The report then adds recall on the unseen attacks for the cascade and its controls, the benign false
alarm rate, and the frontier gap on the benign and unseen flows.

## Models trained elsewhere

```bash
idslab export-splits config.yaml --out splits   # row ids, folds and a fingerprint per run unit
```

Train on exactly the exported rows, then write one Parquet score file per unit with columns `row_id`,
`split`, `score_kind` (`oof` or `fitted`) and `score` (or `proba__<type>`), plus a `.json` sidecar with
`seed`, `family`, `split_fingerprint` and `producer`. Point a candidate at it:

```yaml
candidates: [{external: {path: scores/cnn_{seed}.parquet}}]
```

A file that does not match the exported splits, scores rows its model was trained on, or misses a
required row is rejected with a message naming the problem.

## A config in brief

```yaml
name: my-cascade
dataset: {train: data/train.parquet, test: data/test.parquet, label: label, type: attack_cat}
entry: binary
stages:
  binary:
    kind: classifier
    calibrate: sigmoid
    pipeline: [{class: idslab.prep.Encode, params: {categorical: [proto, service]}}]
    candidates: [{class: sklearn.ensemble.RandomForestClassifier, params: {n_estimators: 200}}]
    decision: {threshold: 0.5, off: benign}
    routes: {attack: ATTACK, benign: gate}       # the gate re-checks benign calls
  gate:
    kind: detector
    fit_on: benign
    pipeline: [{class: idslab.prep.Encode, params: {categorical: [proto, service]}}]
    candidates: [{class: sklearn.ensemble.IsolationForest}]
    decision: {budget: [0.01, 0.05], off: normal}
    routes: {anomalous: ATTACK, normal: BENIGN}
```

Any scikit-learn style model can sit in a stage if it meets the stage's score contract. The full
design is in [DESIGN.md](DESIGN.md).

## License

GNU Affero General Public License v3.0 or later. See [LICENSE](LICENSE).
