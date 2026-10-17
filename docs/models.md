# Models in stages

A model is not a stage. The same random forest can be an entry check, a router or a specialist; the
stage decides which rows it learns from, which flows reach it and what its output does. Any model can
sit in a stage if its output meets the stage's **score contract**, which IDSLab checks on every call.

| Contract     | Stage                          | What the model must give                                    |
|--------------|--------------------------------|-------------------------------------------------------------|
| AttackScore  | classifier                     | one probability of attack per row, in [0, 1]                |
| ClassProba   | multiclass                     | per row, probabilities over named types                     |
| AnomalyScore | detector, `fit_on: fit` or `benign` | one score per row, higher means more anomalous         |
| BatchAnomaly | detector, `fit_on: pool`       | fitted on a batch, one score per row of that batch          |
| ExternalScore | any                           | a score file meeting the provenance rules below             |

## scikit-learn style models

Any class with the scikit-learn interface works without extra code:

- **Classifiers** (classifier and multiclass stages): `predict_proba` and `classes_`. The attack
  class must be 1. A model with only `decision_function` (for example `LinearSVC`) needs
  `calibrate: sigmoid`, which turns its scores into probabilities on CAL.
- **Detectors fitted once**: scikit-learn detectors are read through `score_samples` (negated, so
  higher means more anomalous); PyOD detectors through `decision_function`. `LocalOutlierFactor` is
  built with `novelty=True`. Another method can be named per candidate:
  `score: {method: decision_function, higher_is_anomalous: false}`.
- **Detectors refitted per batch** (`fit_on: pool`): `LocalOutlierFactor` (its
  `negative_outlier_factor_`), PyOD (`decision_scores_`), or `score_samples` after fitting.

Models get `random_state` from the seed and, when `protocol.n_jobs` is not 1, `n_jobs`, unless the
config sets them. Libraries from the optional extras (XGBoost, LightGBM, PyOD, imbalanced-learn,
PyTorch) are checked at config load.

## Pipelines, search and calibration

```yaml
binary:
  kind: classifier
  calibrate: sigmoid
  pipeline:
    - {class: idslab.prep.Encode, params: {categorical: [proto, service]}}
    - {class: imblearn.over_sampling.SMOTE}
  candidates:
    - class: sklearn.ensemble.RandomForestClassifier
      search: {space: {max_depth: [10, 20, null], min_samples_leaf: [1, 5]}, n_iter: 6, cv: 3}
```

The pipeline and the model are fitted together on the stage's training rows. A search refits the
whole pipeline in each fold (random or grid search, stratified folds) and then refits on all training
rows with the best parameters. Resampling steps run during fitting only. A calibrated stage fits on
FIT-base and calibrates on CAL, so calibration never sees the rows the model was trained on.

## PyTorch models

`pip install "idslab[torch]"` adds two models with the scikit-learn interface, so search, pipelines and
calibration work as for any other model:

```yaml
candidates:
  - class: idslab.torch_models.TorchMLPClassifier      # classifier and multiclass stages
    params: {hidden: [64, 32], epochs: 20, lr: 0.001, batch_size: 256, dropout: 0.1}
  - class: idslab.torch_models.TorchAutoencoder        # detector stages
    params: {hidden: [32, 8, 32], epochs: 30}
```

Both seed torch from the run seed and train on the CPU unless `device` says otherwise. Put a scaler in
the stage pipeline; the networks expect scaled inputs. The autoencoder's anomaly score is its
reconstruction error.

## Your own adapter class

A model without the scikit-learn interface needs a small class with an `idslab_contract` attribute
and the methods of that contract:

| `idslab_contract` | Methods                                       |
|-------------------|-----------------------------------------------|
| `AttackScore`     | `fit(X, y)`, `attack_score(X)`                 |
| `ClassProba`      | `fit(X, y)`, `class_proba(X)`, `classes_`      |
| `AnomalyScore`    | `fit(X)`, `anomaly_score(X)`                   |
| `BatchAnomaly`    | `fit_score(X)` (fit on the batch and score it) |

`X` is the stage's feature frame (pandas), `y` the binary label or the type. An adapter does its own
preprocessing, so its stage has no `pipeline`; it is not calibrated or searched (list each setting as
its own candidate). [examples/custom_adapter.py](../examples/custom_adapter.py) is a complete
AttackScore adapter:

```yaml
candidates:
  - class: custom_adapter.ZScoreRule
    params: {columns: [x0, x1, x2]}
```

```bash
PYTHONPATH=examples idslab run config.yaml     # the module must be importable
```

## Plugins

A package can add models, pipeline steps and objectives without changes to IDSLab, through the entry
point group `idslab.plugins`:

```toml
# pyproject.toml of your package
[project.entry-points."idslab.plugins"]
mytool = "mytool.idslab:plugin"
```

```python
# mytool/idslab.py
def plugin():
    return {
        "classifier": [{"class": "mytool.models.FlowNet", "label": "FlowNet", "note": "needs a GPU"}],
        "detector": [],
        "step": [{"class": "mytool.prep.LogScale", "label": "Log scale"}],
        "objectives": {
            # higher is better; tp, fp, tn, fn are NumPy arrays that broadcast
            "weighted_recall": lambda tp, fp, tn, fn: tp / (tp + fn + 1e-12) - 0.5 * fp / (fp + tn + 1e-12),
        },
    }
```

Catalog entries appear in the dashboard builder; the classes work in configs like any other class
path. Objectives are used by name: `protocol.objective: weighted_recall`. A plugin that fails to load is
logged and skipped.

## Scores made elsewhere

A model trained outside IDSLab, for example a CNN on a GPU machine, enters a stage through a score
file. IDSLab checks that the file was made on exactly its splits, so the protocol still holds.

1. Export the splits:

   ```bash
   idslab export-splits config.yaml --out splits
   ```

   One `splits_seed<s>.parquet` per run unit, with `row_id`, `split` and `oof_fold`, and a JSON
   sidecar with the split fingerprint.

2. Train on the FIT rows (`FIT-base` and `CAL`) of each unit, or on `FIT-base` only for a calibrated
   stage, and score the other rows. Write a Parquet file with:

   | Column       | Content                                                                    |
   |--------------|----------------------------------------------------------------------------|
   | `row_id`     | as exported                                                                |
   | `split`      | as exported                                                                |
   | `score_kind` | `fitted` (from the model trained on FIT) or `oof` (from a model that did not see the row) |
   | `score`      | P(attack) for a classifier, higher = more anomalous for a detector; a multiclass stage uses `proba__<type>` columns |

   and a sidecar `<file>.json`:

   ```json
   {"seed": 0, "family": null, "split_fingerprint": "<from the export>",
    "producer": {"config_hash": "...", "code_version": "cnn-1", "trained_on": "fit"}}
   ```

3. Point a candidate at the files:

   ```yaml
   candidates: [{external: {path: scores/cnn_{seed}.parquet}, label: cnn}]
   ```

The rules: VAL and TEST need `fitted` scores; FIT rows may only carry `oof` scores (a `fitted` score
on a row the model trained on is rejected); a stage whose scores feed another stage needs `oof`
scores for its FIT rows; a calibrated stage needs `trained_on: fit_base`, and then also `fitted`
scores on CAL; external type scores are not calibrated; pool detectors cannot use score files.
Any mismatch stops the run with a message naming the problem. Score files are part of the run
fingerprint, so a changed file reruns its unit.

[examples/external_score_job.py](../examples/external_score_job.py) is a complete job (a small
MLP standing in for the outside model), including the out-of-fold scores.
