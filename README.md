# IDSLab

IDSLab evaluates staged (cascade) intrusion detection systems. You describe the dataset, the stages,
the model at each stage and the routing between stages in one YAML file. IDSLab fits the stages,
chooses every model and threshold on validation data, freezes the design, and reports it on the test
set next to controls that show whether the extra stages beat the first stage alone.

- **Any design from a config**: classifier, type (multiclass) and anomaly detector stages, routed
  into any acyclic graph; a stage's score can feed later stages as a feature.
- **Any model**: scikit-learn style estimators, XGBoost, LightGBM, PyOD, PyTorch, your own adapter
  class, or scores from a model trained elsewhere (for example a CNN on a GPU), checked against the
  exported splits.
- **One fixed protocol**: TEST labels never touch fitting or selection; every stage chosen on VAL can
  be switched off; the report always carries the controls.
- **Unseen attacks**: held-out family and natural novelty modes report recall on attacks the design
  never saw.
- **Reports and a dashboard**: Markdown, JSON and a self-contained HTML page per experiment, and a
  local web dashboard that runs every command.

## Install

```bash
pip install idslab                       # Python 3.10 or newer
pip install "idslab[xgboost,pyod,torch]" # optional model libraries
```

From a clone: `pip install -e ".[dev]"`.

## Quick start

```bash
idslab example demo                                   # synthetic data and three reference designs
idslab validate demo/r1_router_gate.yaml              # check the config without reading data
idslab run demo/r1_router_gate.yaml --out demo/runs   # fit, select on VAL, evaluate on TEST
idslab dashboard --workspace demo --open              # the same in the browser
```

Open `demo/runs/r1-router-gate/report.html`. The headline answers the first question about any
cascade: does it beat its first stage alone, and does it reach an operating point that moving the
first stage's threshold cannot?

## A config

```yaml
name: router-gate
dataset: {train: data/train.parquet, test: data/test.parquet, label: label, type: attack_cat}
protocol: {seeds: [0, 1, 2], objective: accuracy}
entry: binary
stages:
  binary:
    kind: classifier
    calibrate: sigmoid
    pipeline: [{class: idslab.prep.Encode, params: {categorical: [proto, service]}}]
    candidates:
      - class: sklearn.ensemble.RandomForestClassifier
        search: {space: {max_depth: [8, 16, null]}, n_iter: 3, cv: 3}
      - class: xgboost.XGBClassifier
    decision: {threshold: 0.5, off: benign}
    routes: {attack: ATTACK, benign: gate}        # the gate re-checks what the classifier lets through
  gate:
    kind: detector
    fit_on: benign
    pipeline: [{class: idslab.prep.Encode, params: {categorical: [proto, service]}}]
    candidates: [{class: sklearn.ensemble.IsolationForest}]
    decision: {budget: [0.01, 0.02, 0.05], off: normal}   # chosen on VAL, with "off" as an option
    routes: {anomalous: ATTACK, normal: BENIGN}
```

## What every run reports

- The cascade and three versions of its first stage alone on TEST: at its own threshold, at its best
  VAL cutoff, and at the cascade's false alarm rate.
- Paired bootstrap intervals of cascade minus each control, and the spread over seeds.
- The frontier gap: the cascade's detection rate minus the best the first stage reaches at the same
  false alarm rate by threshold choice alone.
- The effect of switching each stage off, and the step each stage adds in order.
- The configuration chosen on VAL and the full selection trace, per seed.

## Documentation

[Getting started](https://github.com/hashtagRR/IDSLab/blob/main/docs/getting-started.md) |
[Config reference](https://github.com/hashtagRR/IDSLab/blob/main/docs/config-reference.md) |
[Protocol and controls](https://github.com/hashtagRR/IDSLab/blob/main/docs/protocol.md) |
[Models in stages](https://github.com/hashtagRR/IDSLab/blob/main/docs/models.md) |
[Outputs](https://github.com/hashtagRR/IDSLab/blob/main/docs/outputs.md) |
[Command line](https://github.com/hashtagRR/IDSLab/blob/main/docs/cli.md) |
[Dashboard](https://github.com/hashtagRR/IDSLab/blob/main/docs/dashboard.md) |
[Design](https://github.com/hashtagRR/IDSLab/blob/main/DESIGN.md) |
[Changes](https://github.com/hashtagRR/IDSLab/blob/main/CHANGELOG.md)

## Citing

If you use IDSLab in published work, please cite it; [CITATION.cff](https://github.com/hashtagRR/IDSLab/blob/main/CITATION.cff) has the details
(GitHub shows them under "Cite this repository").

## License

GNU Affero General Public License v3.0 or later. See [LICENSE](https://github.com/hashtagRR/IDSLab/blob/main/LICENSE).
