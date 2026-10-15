# IDSLab

IDSLab evaluates staged (cascade) intrusion detection systems. You describe the dataset, the stages,
the model at each stage and the routing between stages in one YAML file. IDSLab fits the stages,
chooses every model and threshold on validation data, freezes the design, and reports it on the test
set next to controls that show whether the extra stages beat the first stage alone.

Status: version 0.0.1. Classifier and benign-trained
detector stages, greedy selection on VAL, all binary controls, standard train/test mode.

## Install

```bash
pip install -e .            # from a clone; Python 3.10 or newer
pip install -e ".[dev]"     # with the test and check tools
```

Optional model libraries: `pip install -e ".[xgboost]"`, `".[pyod]"`, `".[imbalance]"`.

## Quick start

```bash
idslab example demo                         # synthetic data and two reference configs
idslab validate demo/r1_router_gate.yaml    # checks the config without reading data
idslab run demo/r1_router_gate.yaml --out demo/runs
```

`demo/runs/r1-router-gate/report.md` holds the result. Each seed also writes `summary.json`
(selected configuration, selection trace, controls), `per_flow.parquet`, `val_flow.parquet` and
`split_ids.parquet`.

## What every run guarantees

- Splits per seed: FIT-base and CAL (calibration only) inside FIT, VAL for every choice, TEST last.
- TEST labels never influence fitting, selection or any configuration choice; the test suite checks
  this by shuffling TEST labels and requiring an identical selection.
- Every stage chosen on VAL has an "off" option, and ties go to off.
- Controls in every report: the first stage alone (its own threshold and the best VAL cutoff), the
  first stage at the cascade's VAL false-alarm rate, the frontier gap against the first stage's ROC
  curve, each stage switched off, additive prefix steps, a prevalence check and paired bootstrap
  intervals.

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
