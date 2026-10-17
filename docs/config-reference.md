# Config reference

A config is one YAML file. Unknown keys are errors, and every error found is reported at once.
Relative file paths are read from the folder of the config file.

Booleans follow YAML 1.2: only `true` and `false` are booleans, so `off`, `on`, `yes` and `no` stay
plain words (`off: benign` means what it says).

```yaml
name: my-cascade        # required; names the output folder runs/<name>
dataset: {...}          # required
protocol: {...}         # optional
entry: binary           # required; the stage every flow enters first
stages: {...}           # required; at least one stage
controls: {...}         # optional
```

## dataset

| Key             | Default    | Meaning                                                                       |
|-----------------|------------|-------------------------------------------------------------------------------|
| `train`         | required   | Parquet (`.parquet`, `.pq`) or CSV (`.csv`, also `.gz`, `.zip`, `.bz2`, `.xz`) file |
| `test`          |            | test file with the same columns; give exactly one of `test` and `test_fraction` |
| `test_fraction` |            | hold out this share of `train` as TEST (stratified, fixed per seed), between 0 and 1 |
| `label`         |            | binary label column, 0 benign and 1 attack                                    |
| `type`          |            | attack type column; with no `label`, the label is "type differs from `normal_type`" |
| `normal_type`   | `Normal`   | the type value of benign flows                                                |
| `type_map`      | `{}`       | merge raw types into groups, e.g. `{Fuzzers: Scanning, Analysis: Scanning}`   |
| `type_fill`     |            | name given to rows with a missing type; without it, missing types are an error |
| `drop`          | `[]`       | columns to remove before modelling (the label, type and id columns are always removed) |
| `id_column`     |            | a unique row id column; default ids are `train:<n>` and `test:<n>`           |

Give `label`, `type` or both. When both are given and disagree, the label decides the verdict and the
run summary counts the disagreeing rows. Anything learned from data (encoding, scaling, feature
selection, resampling) belongs in a stage `pipeline`, never here.

## protocol

| Key            | Default           | Meaning                                                               |
|----------------|-------------------|-----------------------------------------------------------------------|
| `seeds`        | `[0, 1, 2, 3, 4]` | one run per seed; each seed changes the splits and every random state |
| `val_fraction` | `0.2`             | share of the train partition used as VAL (between 0 and 0.5)          |
| `cal_fraction` | `0.2`             | share of FIT used as CAL for calibrated stages (between 0 and 0.5)    |
| `objective`    | `accuracy`        | `accuracy`, `mcc`, `f1`, `balanced_accuracy`, a cost or a recall cap (below), or a plugin's; every VAL choice uses it |
| `mode`         | `standard`        | `standard`, `heldout_family` or `natural_novelty` (see below)         |
| `n_jobs`       | `1`               | cores per model fit and search; `-1` for all                          |
| `subsample`    | `{}`              | `{train: N, test: M}`: stratified train sample, random TEST sample    |
| `oof_folds`    | `3`               | folds for out-of-fold scores (`extra_features`, `hard`), at least 2   |
| `search`       | `greedy`          | `greedy_fixed` keeps later fixed stages on while judging earlier ones; `joint` adds a check of every combination (see below) |
| `joint_max`    | `5000`            | largest number of combinations the joint check evaluates              |

Mode options:

| Key            | Mode              | Meaning                                                               |
|----------------|-------------------|-----------------------------------------------------------------------|
| `families`     | `heldout_family`  | families to hold out, one run unit each; default every attack type    |
| `family_level` | `heldout_family`  | `mapped` (after `type_map`, default) or `raw`                         |
| `novel_types`  | `natural_novelty` | raw types that occur in TEST only; required, checked against the train partition |

`seeds` and `n_jobs` are left out of the config fingerprint, so adding a seed or changing
parallelism keeps finished seeds. Any other change reruns them.

## entry and stages

`stages` maps a stage name to its definition. `BENIGN` and `ATTACK` are the two terminals and cannot
name a stage. Every stage must be reachable from `entry`, and the routes must not form a cycle.

```yaml
stages:
  router:
    kind: classifier
    pipeline: [...]
    candidates: [...]
    decision: {grid: [0.5, 0.7, 0.9], off: attack}
    routes: {attack: ATTACK, benign: gate}
```

### Keys of every stage

| Key          | Default                    | Meaning                                                      |
|--------------|----------------------------|--------------------------------------------------------------|
| `kind`       | required                   | `classifier`, `multiclass` or `detector`                     |
| `candidates` | required                   | models to choose from on VAL (a list, or one mapping)        |
| `decision`   | required                   | how the score becomes an output (see Decisions)              |
| `routes`     |                            | output to stage or terminal; required unless the stage annotates |
| `pipeline`   | `[]`                       | preprocessing steps fitted on the stage's own training rows  |
| `select_by`  | `auc` for deciding classifier and multiclass stages, `objective` otherwise | how candidates are compared on VAL |
| `min_val_rows` | `20`                     | fewer VAL rows reaching the stage switch it off (logged)     |

`select_by: auc` picks the candidate with the best AUC on the VAL rows reaching the stage, then
tunes the decision. `select_by: objective` chooses candidate and decision value together by the
cascade objective. When the reaching rows hold one class only, `auc` falls back to the objective.

### Kinds and outputs

| kind         | Score                        | Outputs (route keys)                         | Decisions                                  |
|--------------|------------------------------|----------------------------------------------|--------------------------------------------|
| `classifier` | P(attack)                    | `attack`, `benign`                           | `threshold`, `grid`                        |
| `detector`   | anomaly score, higher is odder | `anomalous`, `normal`                      | `threshold`, `grid`, `budget`, `top_fraction` |
| `multiclass` | probability per type         | type names, `uncertain`, `default` (decide role) | `confidence`                           |

### Classifier and multiclass keys

| Key              | Default     | Meaning                                                            |
|------------------|-------------|--------------------------------------------------------------------|
| `calibrate`      | `none`      | `sigmoid` or `isotonic`: fit on FIT-base, calibrate on CAL         |
| `train_on`       | `all`       | `all`, `attack` (multiclass only), `hard`, or a list of types      |
| `hard`           |             | `{source: <stage>, band: [low, high]}` with `train_on: hard`       |
| `extra_features` | `[]`        | stages whose scores become extra input columns (out-of-fold on FIT); a source must not itself use other stages' scores |
| `role`           | `decide` (classifier), `annotate` (multiclass) | see Roles                         |
| `next`           |             | where an annotate stage sends every flow                          |
| `type_objective` | `macro_f1`  | multiclass only: `macro_f1`, `accuracy` or `auc` for annotate selection |

`train_on` fixes the training rows before selection: a classifier's list of types must include the
normal type and at least one attack type. `train_on: hard` trains a specialist on the rows whose
out-of-fold score from `source` lies inside `band`; the band is fixed in the config, never chosen on
VAL.

A stage whose scores are used by another stage (through `extra_features` or `hard.source`) must not
itself use other stages' scores: chains such as A feeds B feeds C are refused at load, because B's
out-of-fold scores could then carry labels of their own held-out folds through A's scores.

### Detector keys

| Key            | Default | Meaning                                                              |
|----------------|---------|----------------------------------------------------------------------|
| `fit_on`       | `fit`   | `fit` (all FIT rows), `benign` (benign FIT rows) or `pool`           |
| `prep_fit_on`  | `fit`   | `pool` only: rows that fit the preprocessing, `fit` or `benign`      |
| `min_pool`     | largest `n_neighbors` + 1, or 20 | `pool` only: smaller batches are not scored (stage off for that batch) |
| `extra_features` | `[]`  | as above; not with `fit_on: pool`                                    |

`fit_on: fit` and `benign` fit the detector once and score VAL and TEST as new data. `fit_on: pool`
refits the detector on each batch of flows that reaches it (every flow of the split) and scores that
batch; settings are chosen on the VAL batch and frozen before TEST. Detectors are never calibrated
and take no `train_on`.

### Roles

- `decide`: the outputs route to stages or terminals and can change the verdict. Selected by the
  cascade objective.
- `annotate` (multiclass only): names an attack type and sends every flow along `next`; it cannot
  change the verdict. Selected by `type_objective` on the VAL rows reaching it. It has no `off` key;
  off means "pass flows on without a type".

A deciding multiclass stage routes by type name. Types without a route take `default`; flows whose
top probability is below the confidence value take `uncertain` if it is routed, else `default`.

### Decisions

Exactly one mode, plus `off`:

| Mode           | Value                | Chosen on VAL                       | Meaning                                    |
|----------------|----------------------|-------------------------------------|--------------------------------------------|
| `threshold`    | one number           | no (fixed)                          | a score at or above it takes `attack` (or `anomalous`) |
| `grid`         | list of numbers      | yes, with off as an option          | the threshold is chosen from the list      |
| `budget`       | fraction or list     | yes when a list of two or more      | detectors fitted once: flag this share of the benign VAL rows reaching the stage |
| `top_fraction` | fraction or list     | yes when a list of two or more      | detectors: flag this share of the flows reaching the stage |
| `confidence`   | probability or list  | yes when a list of two or more      | multiclass: below it the flow is `uncertain` |

Classifier thresholds are probabilities in [0, 1]; budgets and fractions lie in (0, 1).

`off` is the output every flow takes when the stage is switched off, and is required for every
deciding stage (for example `off: benign` on an entry classifier, `off: normal` on a gate). Selection
can switch off any stage whose decision is chosen on VAL, except the control first stage. Stages
with a fixed decision are never switched off by selection; their `off` is used by the stage removal
control.

### Candidates

```yaml
candidates:
  - class: sklearn.ensemble.RandomForestClassifier
    params: {n_estimators: 200}
    search: {space: {max_depth: [8, 16, null]}, n_iter: 6, cv: 3, kind: random}
  - class: xgboost.XGBClassifier
  - external: {path: scores/cnn_{seed}.parquet}
```

| Key      | Meaning                                                                         |
|----------|---------------------------------------------------------------------------------|
| `class`  | dotted import path of a scikit-learn style estimator or an adapter class        |
| `params` | constructor arguments                                                           |
| `search` | hyperparameter search on the stage's training rows: `space` (required), `n_iter` (10), `cv` (3), `kind` (`random` or `grid`); not for detectors |
| `score`  | detectors fitted once: `{method: <name>, higher_is_anomalous: true or false}` to read another scoring method |
| `external` | a score file made outside IDSLab; the path may use `{seed}` and `{family}` ([Models](models.md#scores-made-elsewhere)) |
| `label`  | external candidates only: the name shown in reports                             |

Each candidate is checked at load: the class must import, accept its parameters and offer the method
its stage needs (`predict_proba` for classifiers, a scoring method for detectors). Search parameters
must exist on the class. Detectors have no labels to search on; list each setting as its own
candidate instead.

### Pipeline

```yaml
pipeline:
  - {class: idslab.prep.Encode, params: {categorical: [proto, service, state]}}
  - {class: sklearn.preprocessing.StandardScaler}
  - {class: imblearn.over_sampling.SMOTE}
```

Steps are fitted on the stage's training rows, refitted in every search fold and every out-of-fold
fold. A step needs `transform` (or `fit_resample` for resampling). `idslab.prep.Encode` ordinal-encodes
the listed columns, maps categories first seen at VAL or TEST to an unknown code, and fails at fit time
on any other non-numeric column, so a forgotten text column is caught.

## controls

| Key                  | Default                     | Meaning                                             |
|----------------------|-----------------------------|-----------------------------------------------------|
| `first_stage`        | the entry stage             | the classifier stage every control is built from    |
| `bootstrap`          | `1000`                      | paired bootstrap replicates per seed (0 to skip)    |
| `frontier_bootstrap` | `200`                       | replicates for the frontier gap interval (0 to skip) |
| `cutoff_grid`        | 0.05 to 0.95 in steps of 0.025 | cutoffs for the B2 control (first stage at its best VAL cutoff) |

## Objectives

```yaml
protocol:
  objective: {cost: {missed_attack: 10, false_alarm: 1}}   # or
  objective: {recall_at_fpr: 0.01}
```

- `cost`: every VAL choice minimises the average cost per flow, `missed_attack` times the missed
  attacks plus `false_alarm` times the false alarms, divided by the flows. Reports show it as a
  negative number (`cost(missed_attack=10,false_alarm=1)`), so that higher is better like every other
  objective and the differences keep their sign.
- `recall_at_fpr`: every VAL choice maximises recall among the options whose false alarm rate is at
  most the cap; when no option meets the cap, the lowest false alarm rate wins. Reports show recall
  where the cap holds and minus the false alarm rate where it does not.
- Plugins can add named objectives ([Models in stages](models.md#plugins)).

## Stage cost

```yaml
stages:
  deep:
    cost: 20        # running this stage on a flow costs 20 times a stage with cost 1
```

A declared, relative cost per flow, 1 when not set. It changes reports only (not selection, and not
the run fingerprint): the report's stage cost section multiplies it by the share of TEST flows that
reach each stage and compares the cascade's expected cost per flow with its first stage alone, next to
the measured scoring time.

## Greedy with fixed stages on

`protocol.search: greedy_fixed` is greedy selection where, while an earlier stage is judged, later
stages that cannot be switched off anyway stay on instead of off: a fixed threshold, one candidate, and
no score features or hard rows. In designs where a later stage repairs an earlier one's mistakes (for
example R2, where a classifier clears a detector's alarms), the earlier stage is then judged with its
partner in place. Stages with a tuned decision are still judged off, as in plain greedy selection.

## Joint search check

```yaml
protocol: {search: joint, joint_max: 5000}
```

Greedy selection stays the result. The check evaluates every combination of the deciding stages' VAL
values (with the candidates greedy chose) and reports the best one on TEST as the system
`joint_check`, so a stage that greedy switched off but that helps in combination becomes visible.

## Unseen attack modes

```yaml
protocol:
  mode: heldout_family
  families: [DoS, Worms]     # optional
  family_level: mapped       # optional
```

Each family in turn leaves the train partition (all its rows) while TEST keeps it; one run unit per
seed and family. With `test_fraction`, TEST is cut first, so it holds the family in every unit.

```yaml
protocol:
  mode: natural_novelty
  novel_types: [Zeroday]
```

Nothing is removed; TEST rows of the listed raw types form the unseen set. The run stops if any of
them occurs in the train partition. Both modes need `dataset.type`.
