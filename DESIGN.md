# IDSLab design

IDSLab evaluates staged (cascade) intrusion detection systems. The user describes the dataset,
the stages, the model at each stage and the routing between stages in one YAML file. IDSLab fits,
selects and scores the cascade under a fixed protocol and reports every result next to controls that
show whether the extra stages help.

This document states the rules IDSLab applies and the reasons for them. The user guides in `docs/`
describe how to use it.

The guiding rule: models and routes can change freely, while the evaluation stays the same and can be
checked.

## 0. Architecture

| Component         | Responsibility                                             | What users replace                                   |
|-------------------|------------------------------------------------------------|------------------------------------------------------|
| Model adapter     | Fit a model and return scores in a standard form           | random forest, CNN, LOF, own class, external scores  |
| Stage             | Model + training policy + decision rule + role             | entry check, router, type classifier, gate, specialist |
| Routing graph     | Send each stage output to another stage or a terminal      | number of stages, connections, bypass routes         |
| Selection engine  | Choose candidates and decision values on VAL               | objective, search policy (greedy, joint check)       |
| Evaluation engine | Score the frozen design and its controls on TEST           | dataset, evaluation mode                             |

**A model is not a stage.** The same random forest can be an entry check, a router or a specialist.
The stage decides which rows it learns from, which flows reach it and what its output does.

### 0.1 Score contracts

A stage accepts any model that meets its score contract. An import path and a `fit()` method are not
enough; the adapter must state how its output maps onto the contract, and the tool checks it.

| Contract      | Used by            | Requirement                                                              |
|---------------|--------------------|--------------------------------------------------------------------------|
| AttackScore   | classifier stages  | one value per row; explicit mapping of the positive (attack) class; declared range (probability or raw score) |
| ClassProba    | multiclass stages  | probabilities aligned to named type classes; classes unseen in training are filled with 0 and reported |
| AnomalyScore  | inductive detectors| one value per row; declared direction (higher = more anomalous after the adapter) |
| BatchAnomaly  | transductive detectors | `fit_score(pool) -> scores` on one batch, features only, same direction rule |
| ExternalScore | any stage          | a file meeting the provenance contract (section 4.4), mapped to one of the contracts above |

Built-in adapters: scikit-learn classifiers (`predict_proba` + `classes_`), scikit-learn and PyOD
detectors (direction set per library), LOF in both modes (`novelty=True` for inductive,
`negative_outlier_factor_` for batch). Anything else implements a small adapter class.

Libraries behind optional extras (XGBoost, LightGBM, PyOD, imbalanced-learn) are imported when the
config is loaded. A missing one is a config error that names the extra to install
(`pip install idslab[xgboost]`), before any data is read.

### 0.2 Two graphs

- **Routing graph**: where a flow goes after a stage's decision. Acyclic.
- **Score-dependency graph**: which stage's score is a feature of another stage's model
  (`extra_features`). Acyclic.

Rules:
1. A score source produces scores for every row of every split, whatever the routing. A stage that
   is switched off (its decision set to off) still produces scores for its dependants; "off" changes
   routing only. Only inductive stages (classifier, multiclass, inductive detector) can be score
   sources: a transductive detector scores only the flows that reach it, so naming one in
   `extra_features` or `hard.source` is a config error.
2. Fitting order is a topological order of the dependency graph. Selection order is a topological order
   of the union of both graphs, so a score source is chosen before any stage that uses it.
3. The union must be acyclic; the config is rejected otherwise.
4. Removing a stage (ablation) and switching its decision off are different. Ablation reports use
   "decision off"; feature sources keep their scores. The report says so.
5. A stage whose scores feed another stage must not itself use other stages' scores (no chain such as
   A feeds B feeds C). B's out-of-fold scores are refitted per fold on B's own score features, and
   those features came from A models that saw B's held-out fold, so B's scores would carry that
   fold's labels. Such a config is rejected at load.

## 1. Data contract

| Split    | Source                       | Used for                                                       |
|----------|------------------------------|----------------------------------------------------------------|
| FIT-base | 80% of FIT                   | fitting (preprocessing, search, model) of calibrated stages    |
| CAL      | 20% of FIT                   | calibration of calibrated stages, nothing else                 |
| FIT      | FIT-base + CAL               | fitting of uncalibrated stages                                 |
| VAL      | 20% of the train partition   | candidate choice, decision values, switches                    |
| TEST     | test file, or a hold-out     | evaluation after the configuration is frozen                   |

Rule: **TEST labels never influence fitting, selection or configuration changes. Every evaluation on
TEST (cascade, controls, ablations) is declared in the config or built in, and runs after the
configuration is frozen.** TEST features are used by transductive detectors (section 4.3) and by the
prevalence diagnostic; this is stated in every report that uses them.

Splits are stratified on the type label when the dataset has one, otherwise on the binary label.
Seeds change the splits and every model's random state.

Rare strata: every split and fold count (FIT/VAL, FIT-base/CAL, search folds, out-of-fold folds)
needs each stratum to hold at least as many rows as the largest number of parts it is cut into.
Types below that count are merged into one "rare" stratum for splitting only; their labels are
unchanged. If the merged stratum is still too small, its rows are split at random. Every merge is
logged in the run summary.

## 2. Data handling

Dataset-level steps are stateless only: read, drop columns, derive the binary label, apply `type_map`,
fill missing types. The config must give `label`, `type`, or both; with `type` only, the binary label
is "type differs from `normal_type`". A config with neither is rejected at load. Anything learned from data (categorical encoding, scaling, PCA, feature selection,
resampling) lives inside a stage's pipeline, so it is fitted on that stage's own training rows and
refitted inside every hyperparameter-search fold and every out-of-fold fold.

Unseen categories at VAL or TEST map to an "unknown" code inside the encoder (no error).

## 3. Stages

### 3.1 Kinds and outputs

| kind       | score                        | outputs                          | decision parameter                     |
|------------|------------------------------|----------------------------------|----------------------------------------|
| classifier | P(attack)                    | `attack`, `benign`               | `threshold` (fixed) or `grid`          |
| multiclass | class probabilities          | one per class, `uncertain`       | `confidence` (fixed or grid)           |
| detector   | anomaly score, higher = odd  | `anomalous`, `normal`            | `threshold`, `top_fraction`, `budget`  |

### 3.2 Roles

- `role: decide` (default for classifier and detector). Outputs route to stages or terminals and can
  change the binary verdict. Selected with the cascade objective on VAL.
- `role: annotate` (default for multiclass). Assigns an attack type and passes every flow on along a
  single `next` route; it cannot change the binary verdict. Selected with a type objective on the VAL
  rows reaching it (`type_objective`: macro_f1 | accuracy | auc). A multiclass stage may be declared
  `decide`, in which case its class outputs route like any other stage and it is selected with the
  cascade objective.
- The role is declared and checked against the routes. A stage with any route that can change the
  binary verdict (for example `Normal -> BENIGN`) must be `decide`; `annotate` with such a route is a
  config error. A `decide` multiclass stage may also report a type objective, but selection uses the
  cascade objective.

### 3.3 Off

Every decide stage declares `off`: the output every flow takes when the stage is switched off.
Selection (section 5) evaluates earlier stages with later stages off, and the prefix differences
(section 6) truncate the cascade, so every stage must have a defined off behaviour. The entry stage's
`off` defines the cascade with no stages at all (all flows to that output). An annotate stage needs
no declaration: its off is always "pass every flow along `next` without a type". A decide stage
without `off` is a config error. Stages with fixed decisions are still never switched off by selection; their `off` is used
only for evaluation and ablation.

### 3.4 Terminals and recorded type

Terminals: `BENIGN`, `ATTACK`. Recorded type per TEST flow:

| Situation                                                    | Recorded type     |
|--------------------------------------------------------------|-------------------|
| Verdict BENIGN                                               | the normal type   |
| ATTACK, last annotate/decide multiclass predicted attack c   | c                 |
| ATTACK, multiclass predicted the normal type                 | `untyped_normal`  |
| ATTACK, multiclass prediction below confidence               | `uncertain`       |
| ATTACK, no multiclass stage on the path                      | `untyped`         |

Type metrics are reported on all true attacks; the three non-class labels count as wrong.

## 4. Fitting

### 4.1 Training rows

`train_on`: `all` | `benign` | `attack` | a list of (mapped) types | `hard`. The set is fixed before
selection and never depends on routing thresholds chosen on VAL.

Rows come from FIT-base for calibrated stages and from FIT for uncalibrated stages. Calibration uses
the CAL rows of the same `train_on` subset. A calibrated stage's subset must hold at least two
classes in CAL (a classifier needs both benign and attack rows anyway); otherwise the config is
rejected. Detectors trained on one class are never calibrated (section 0.1).

`hard` (specialist stages): rows whose out-of-fold score from a named source stage falls in a band
fixed in the config (`hard: {source: router, band: [0.3, 0.7]}`). Band membership is computed from
out-of-fold scores over FIT, never from VAL. The usual split rule then applies to the band rows: an
uncalibrated specialist trains on the band rows of FIT; a calibrated one trains on the band rows of
FIT-base and calibrates on the band rows of CAL, so CAL is never used for both. The band is a config
value, not a VAL-chosen one, so the training set stays fixed.

Hyperparameter search (random or grid, k-fold, stratified) runs on the stage's training rows with the
whole pipeline (preprocessing, resampling, model) refitted per fold. The refit on all training rows
uses the best parameters.

### 4.2 Score-dependent stages

A stage may take upstream scores as features (`extra_features: [router]`). Stages are fitted in
dependency-graph order, interleaved with selection (section 0.2): a source stage is fitted and its
candidate chosen before any dependent stage is fitted.

Out-of-fold scores for the dependent stage's training rows: all FIT rows are assigned to k folds
(stratified as in section 1). For each fold, the source's complete score-producing procedure
(its own training subset within the other k-1 folds; preprocessing, resampling, model with the
selected hyperparameters) is refitted and scores every row of the held-out fold, whether or not that
row belongs to the source's training subset. Every training row therefore gets its feature from the
same kind of model, one that never saw it, and the feature carries no trace of the row's label
through the scoring path. Hyperparameters stay those found by the source's search (a small
approximation). Repeating the search per fold (`oof_research`) is not implemented.
The feature is the source's uncalibrated score at training and at inference, so both sides match.

VAL and TEST rows get the fitted source model's score.

### 4.3 Detectors: two execution paths

| Path         | `fit_on`          | Fitting and scoring                                                         |
|--------------|-------------------|-----------------------------------------------------------------------------|
| Inductive    | `fit` or `benign` | Pipeline and detector fitted on the FIT subset; VAL and TEST rows scored as unseen data (e.g. LOF with `novelty=True`, IsolationForest, PyOD) |
| Transductive | `pool`            | Preprocessing fitted on FIT (benign or all, declared). The detector is fitted afresh on each routed batch, using features only, and scores that batch (e.g. LOF fitted-pool scores via `negative_outlier_factor_`). Settings are chosen on the VAL batch and frozen before the TEST batch is processed. |

Transductive batch membership is "all flows of the split that reach the stage". A pool smaller than
`min_pool` is not scored and the stage acts as off for that batch (recorded). `min_pool` defaults to
the largest neighbour count among the stage's candidates plus 1, so a configured neighbour count is
never cut down silently and VAL and TEST batches use the same model settings; a `min_pool` set below
that is a config error. Reports state that transductive results assume batch processing of the
whole split.

`budget` thresholds need the inductive path (the cutoff is the (1 - b) quantile of VAL benign scores
reaching the stage).

### 4.4 External scores (models trained elsewhere, e.g. on a GPU)

Round trip:
1. `idslab export-splits` writes, per seed (and held-out fold), the row ids of FIT-base, CAL,
   FIT, VAL and TEST, plus the out-of-fold fold assignment of FIT, and a fingerprint of the split.
2. The external job trains on exactly those rows and writes one score file per seed.
3. The tool imports the file only if the provenance contract holds.

Provenance contract (one Parquet file per seed and fold, with a JSON sidecar):

| Field            | Requirement                                                              |
|------------------|--------------------------------------------------------------------------|
| `row_id`         | every row of every split the stage needs; no extras, no duplicates       |
| `split`          | must match the exported split of that row                                |
| `seed`, `fold`   | must match the run unit                                                  |
| `split_fingerprint` | must equal the exported fingerprint                                   |
| `score_kind`     | `oof` or `fitted`                                                        |
| class order      | named columns for ClassProba; positive class named for AttackScore       |
| producer         | config hash, code version, training rows used (`fit_base` or `fit`)      |

Rules: rows used as training features of another stage must have `score_kind: oof`; VAL and TEST rows
must come from a model that never saw them; a stage fitted on FIT-base must state so for calibration.
Any mismatch rejects the file with a message naming the field.

## 5. Selection: greedy forward selection

Fixed traversal: topological order from the entry stage, ties broken by the order stages appear in
the config. For each stage in turn:

1. Fit its candidates (section 4).
2. Compute the VAL rows reaching it, with earlier stages at their chosen settings and later stages off.
3. Choose the candidate. `select_by: auc` (default for classifiers): AUC on the reaching rows, a
   heuristic that need not pick the best downstream behaviour. `select_by: objective`
   (default for detectors): candidate and decision value chosen jointly by the cascade objective.
4. For a stage whose decision is tunable, choose the decision value on a grid that always includes
   off, by the full VAL cascade objective (annotate stages: the type objective). Ties go to off, then
   to the first grid value. A stage with a fixed decision skips this step and is never switched off
   by selection.
5. The control first stage (`controls.first_stage`, default the entry stage) is never switched off by
   selection: its grid, if it has one, excludes off. The controls in section 6 are defined against
   it, so it must stay active.

Objectives: accuracy (default), mcc, f1, balanced_accuracy.

Sparse reaching sets: empty or below `min_val_rows` (default 20) -> stage off; one class only ->
`auc` selection falls back to the cascade objective. Each fallback is logged in the selection trace.

Limitation, stated in every report: greedy forward selection can reject a stage that would help in
combination with a later stage (for example a router whose reverted flows a later gate would
recover). A switched-off stage means "not chosen by this procedure", not "cannot help".
`search: joint` evaluates all decision combinations for graphs up to a configurable size, as a check.

## 6. Controls (always reported)

All controls use the same fitted first-stage model as the cascade.

- **B1**: first stage alone at the threshold it uses inside the cascade. **B2**: first stage alone at
  the cutoff that maximises the VAL objective on a fixed grid.
- **Frontier gap** on TEST: cascade TPR minus the first-stage ROC TPR at the cascade's FPR. The ROC is
  built from all distinct scores (tied scores form one point). The interpolated value uses the ROC's
  upper convex hull: randomising between any two thresholds reaches every point on the line between
  them, so the hull is the best the first stage can do by threshold choice alone. The step
  (non-randomised, single threshold) value is reported alongside. A positive gap is evidence, on this TEST sample, of an operating point that threshold
  movement alone does not reach; it is not a population guarantee.
- **Budget-matched first stage**: target = cascade VAL FPR. Rule: the highest threshold whose VAL FPR
  is at least the target. Target and achieved FPR are both reported.
- **Per-stage removal**: each stage with `off`, switched off with everything else as selected. Effects
  are contributions within the selected configuration and need not add up.
- **Prefix differences**: cascade truncated after each stage in traversal order (later stages off);
  consecutive differences give an additive decomposition of the objective.
- **Prevalence diagnostic**: attack share in FIT, VAL, TEST; EM estimate of the TEST share from
  calibrated first-stage scores (Saerens et al., 2002), valid under label shift only. Held-out
  families change attack composition, so the estimate does not explain every difference.
- **Uncertainty**: paired bootstrap 95% intervals of cascade minus each control, conditional on the
  fitted system (TEST-sample uncertainty), resampling benign and attack flows separately. Seed
  variation is reported separately (mean, sd, per-seed values). For transductive stages the bootstrap
  resamples stored predictions and does not reproduce refitting on a changed batch; the report says so.

## 7. Modes

- `standard`.
- `heldout_family`: for each family h, all rows of h leave the train partition before splitting;
  TEST is unchanged. With `test_fraction`, TEST is cut first from the full file (stratified, fixed
  per seed, identical across families), and only then is h removed from the remaining rows, so TEST
  always contains h. Families are defined after `type_map` by default (`family_level: mapped | raw`).
  Adds recall on h, recall on known families, benign FPR, frontier gap on {benign, h}.
- `natural_novelty`: nothing removed; TEST rows whose raw type is in `novel_types` are the novel set.
  The run fails if any declared novel type occurs in the train partition.

## 8. Outputs and provenance

`runs/<name>/<mode>/seed_<s>[/fam_<h>]/`: `summary.json` (selected configuration, selection trace,
fallbacks, metrics, controls), `split_ids.parquet` (row ids per split), `per_flow.parquet` (TEST:
scores, stage path, verdict, type, control predictions), `val_flow.parquet`, `DONE`.
`DONE` holds fingerprints of the config, the data files, every imported external score file and its
JSON sidecar, the split, and the package version; any mismatch reruns the unit, and a report leaves the
unit out until it is rerun. `runs/<name>/report.md` aggregates over seeds.

## 9. Example config

The four-layer design: an entry classifier, a router whose score is a feature of a type stage, the
type stage itself (annotate), and an anomaly gate refitted on each batch. Without the two score
features it becomes the shipped `r1_router_gate.yaml`, with an inductive gate (`fit_on: benign`).

```yaml
name: four-layer
dataset:
  train: data/train.parquet
  test: data/test.parquet          # or test_fraction: 0.3
  label: label                     # optional when type is given
  type: attack_cat                 # optional
  normal_type: Normal
  type_map: {Fuzzers: Scanning, Analysis: Scanning}
  drop: [id]
protocol:
  seeds: [0, 1, 2, 3, 4]
  objective: accuracy
  mode: standard
entry: binary
stages:
  binary:
    kind: classifier
    calibrate: sigmoid
    pipeline: [{class: idslab.prep.Encode, params: {categorical: [proto, service, state]}},
               {class: sklearn.preprocessing.MinMaxScaler}]
    candidates:
      - class: sklearn.ensemble.RandomForestClassifier
        search: {space: {max_depth: [10, 20, 30]}, n_iter: 12, cv: 3}
      - class: xgboost.XGBClassifier
    decision: {threshold: 0.5, off: benign}
    routes: {attack: router, benign: BENIGN}
  router:
    kind: classifier
    candidates: [{class: sklearn.ensemble.RandomForestClassifier}]
    decision: {grid: [0.5, 0.6, 0.7, 0.8, 0.9], off: attack}
    routes: {attack: type, benign: gate}
  type:
    kind: multiclass
    role: annotate
    train_on: all
    calibrate: sigmoid
    extra_features: [router]
    candidates: [{class: sklearn.ensemble.HistGradientBoostingClassifier}]
    decision: {confidence: 0.2}
    next: ATTACK
  gate:
    kind: detector
    fit_on: pool
    prep_fit_on: fit
    pipeline: [{class: sklearn.preprocessing.StandardScaler}, {class: sklearn.decomposition.PCA, params: {n_components: 0.95}}]
    candidates: [{class: sklearn.neighbors.LocalOutlierFactor, params: {n_neighbors: 100}}]
    decision: {top_fraction: [0.02, 0.05, 0.10], off: normal}
    routes: {anomalous: ATTACK, normal: BENIGN}
```

Two reference designs with different shapes ship with IDSLab and run from configs alone: R1 (entry
classifier, router, gate; also in the four-layer form above) and R2 (anomaly detector first, with a
classifier that can clear its alarms). `idslab example` writes both with a synthetic dataset.

## 10. Implementation notes

**Configs and data**

- Configs are read with YAML 1.2 booleans: only `true` and `false` are booleans, so `off`, `on`, `yes`
  and `no` stay plain words (YAML 1.1 would read the key `off` as false). Configs written by the
  dashboard follow the same rule, and read back unchanged.
- YAML syntax errors are config errors (exit code 2), like every other invalid config.
- A detector with `novelty` in its parameters (LOF) is always built with `novelty=True` when fitted
  once, and with `novelty=False` when refitted per batch.
- The `n_jobs` of a model is only set when `protocol.n_jobs` is not 1.

**Fitting and selection**

- Selection asks for a stage's VAL scores only when it reaches the stage, so a stage that uses another
  stage's scores is fitted after that source is chosen. When a source is chosen (or switched off,
  which keeps its best candidate as the score model), its out-of-fold FIT scores and its VAL scores
  enter the feature store; TEST feature scores are added after the freeze, for the chosen candidate of
  each active stage only.
- Out-of-fold folds: `protocol.oof_folds` (default 3), stratified on the type label as in section 1.
  The refits reuse the source stage's search result; a per-fold search is not implemented.
- Multiclass searches score with balanced accuracy and fall back to unstratified folds when a type has
  fewer rows than folds. Calibrating a multiclass stage drops CAL rows of types the model never saw.
- A pool detector's batch is every flow of the split that reaches it; its scores are cached per batch,
  so controls that change the batch refit it on the new batch.
- Parallelism is used at one level only: seed workers fit single-threaded models, and a searched
  candidate spreads folds over cores while its model fits single-threaded.

**Modes**

- A held-out run is one unit per seed and family, written to `seed_<s>/fam_<h>/`; `protocol.families`
  limits the families (default: every attack type of the train partition at `family_level`). The
  family is removed after the TEST cut and after the train subsample, so TEST and the kept train rows
  are the same for every family of a seed.
- The unseen mask over TEST is built after the freeze. Each unit reports, for the cascade and the
  controls, recall on unseen and known attacks and benign FPR, plus the frontier gap and the
  first-stage AUC on the benign and unseen flows.
- Natural novelty checks, per seed, that no declared novel type occurs in FIT-base, CAL or VAL.

**External scores**

- A candidate `{external: {path: ...}}` reads its scores from a file; the path may use `{seed}` and
  `{family}`. `idslab export-splits` writes, per run unit, the row ids, the split, the out-of-fold
  fold of each FIT row (-1 for VAL and TEST) and a JSON sidecar with the split fingerprint.
- Beyond section 4.4: fitted scores on rows the producer trained on are rejected, out-of-fold scores
  are allowed on FIT rows only, a calibrated stage needs `trained_on: fit_base` (calibration then fits
  on the CAL scores), and external type scores are not calibrated. External files are part of the
  unit fingerprint.

**Reports and resume**

- A seed enters the report only if its DONE file carries this config's, data's and IDSLab version's
  fingerprints; the report covers `protocol.seeds` plus any seeds named with `--seeds`.
- Every report writes `report.md`, `report.json` and `report.html`, all from one aggregate. The HTML
  page is self-contained (inline CSS and SVG, no scripts, no network). Its cascade map ends each route
  to BENIGN or ATTACK under its stage, so no edge crosses a stage.
- Reports add an attack-type section when a type stage is active, and a statement on batch processing
  when a pool detector is active.
- `idslab run` and `export-splits` log progress to stderr (unit started, each stage fitted, the
  configuration frozen on VAL, TEST evaluation). With `--jobs` above 1 the worker processes do not log.
- The package version changes whenever a change can alter results, because finished seeds are reused
  while the config, data and version fingerprints match.

**Tests**

- A test shuffles the TEST labels and requires an identical frozen configuration, including designs
  with out-of-fold features and pool detectors.

## 11. Search variants

Greedy selection (section 5) judges a stage while the stages after it are off. In R2 the screen is
judged while the classifier after it is off, so the screen's setting is chosen without its partner.
Two variants address this:

- **`search: joint`** keeps greedy selection as the result and adds a check: every combination of the
  decide stages' VAL values (candidates as greedy chose them), greedy winning ties, capped by
  `protocol.joint_max`. The best combination is reported on TEST as the system `joint_check`. On the
  synthetic R2 example the check finds a better combination than greedy in every seed.
- **`search: greedy_fixed`** keeps later stages on, instead of off, while an earlier stage is judged,
  when they have a fixed threshold, one candidate, and no score features or hard rows. Stages with a
  tuned decision are still judged off. On the synthetic R2 example it raised VAL and TEST accuracy in
  each of three seeds.

## 12. Objectives and stage cost

- `protocol.objective` accepts a metric name or a mapping. `{cost: {missed_attack: A, false_alarm: B}}`
  is written as `cost(missed_attack=A,false_alarm=B)` and valued as the negative average cost per
  flow, so that higher is better for every objective and differences keep their sign.
  `{recall_at_fpr: X}` is recall where the false alarm rate is at most X, otherwise minus the false
  alarm rate, so options within the cap always rank above the others.
- Each run records, per active stage, the share of TEST flows reaching it and the time to score TEST
  with its chosen candidate. A stage may declare a relative `cost` (default 1). The declared cost is
  kept out of the config fingerprint, because it changes reports only: the report multiplies it by the
  share reaching each stage and compares the cascade's expected cost per flow with its first stage
  alone. The scoring time of a pool detector is not separable from routing and is not reported.

## 13. Plugins

Packages add models, pipeline steps and objectives through the entry point group `idslab.plugins`. A
plugin returns a mapping with `classifier`, `detector` and `step` lists (shown in the dashboard's model
catalog) and `objectives` (formulas over confusion counts, registered by name). A plugin that fails to
load is logged and skipped.

## 14. Dashboard

`idslab dashboard` serves a local web page over one workspace folder, with the standard library's HTTP
server and static files; it adds no dependencies.

- **Commands run as the CLI.** Run, report, export-splits and example start `python -m idslab.cli`
  as a subprocess, one job at a time, with a live log and a stop button that ends the job and its
  worker processes. Results are the files the CLI writes, so a run from the browser and one from the
  shell resume each other. Validation uses the CLI's loader in process.
- **Workspace only.** Every path the page sends is relative to the workspace and must resolve inside
  it; hidden parts, parent parts and colons are refused, and configs are saved only as `.yaml` or
  `.yml`. Run outputs are found by their layout (`<name>/<mode>/seed_<s>`) anywhere in the workspace.
- **Local only.** It binds to 127.0.0.1 by default; the Host header must match, and POST takes
  same-origin JSON only, so another site cannot drive it. Report pages are served under a policy that
  allows no scripts, and the page itself loads nothing from the network.
- **Experiments are views over files.** An experiment is a config and the run outputs written under
  its name; nothing else is stored. An output belongs to a config when the names match, and when
  several configs share a name only if the config fingerprints in its `DONE` files match exactly one
  of them. Anything else is shown on its own, read only, with the reason, so results are never
  attributed by a guess.
- **One draft, several editors.** The guided setup steps (Data, Cascade, Evaluation, Review & run),
  the visual builder and the YAML view edit one draft. Typed YAML is saved as typed. A visual edit
  changes the config as data and the server writes the YAML with the CLI's writer and validates it, so
  the YAML stays the source of truth. A visual edit that would drop comments or hand formatting asks
  first and shows the difference. A run always reads the saved file, so unsaved changes are saved
  first.
- **Visual builder.** Stages on an SVG canvas; routes drawn by dragging an output port, by clicking
  the port and then the destination, from the keyboard, or from lists. Score features are dashed
  edges. Missing routes, loops and unreached stages are drawn and listed while the draft stays
  editable. The inspector has a model catalog whose parameter forms come from each class's
  constructor; unknown classes and parameters are kept as written. Node positions are kept in
  `<config>.layout.json`, never in the config.
- **Progress from the job's own words.** Runs log the number of units, each unit's phase, and each
  unit finished or reused; the Jobs page shows those and nothing estimated. Worker processes of a
  parallel run do not log, so their count is read from the DONE files written since the job
  started. Each unit stores the cascade's routes in `summary.json`, so a finished run can be drawn
  after its config changes.
- **Dataset inspector.** Reads a sample of a data file (never the whole file) and suggests the label,
  the type column and its normal value, and the categorical columns for `idslab.prep.Encode`.

## 15. Comparison and sweeps

- **Comparison** (`idslab compare`, the dashboard's Compare page) reads each experiment's report and
  the units the report included. Headline figures come from the reports. For experiments that ran on
  the same data with the same splits (equal data and split fingerprints per unit), the units are also
  paired: on the same TEST flows, the paired bootstrap (section 6) gives each unit's difference of the
  cascade objective against the first experiment, the baseline. Units that differ in data, splits or
  TEST rows are listed as skipped, not compared. Different objectives, modes or data files raise a
  warning; only experiments with the baseline's objective are paired.
- **Sweeps** (`idslab sweep`, the dashboard's Sweeps page) take a base config and either named
  variants, each a set of dotted-key changes, or a grid, the product of listed values. Every variant is
  built and validated before anything runs, written as a config of its own (relative data and score
  paths rewritten so they still point to the same files), run and reported like `idslab run`, and the
  experiments are compared with the first variant as the baseline. Variant configs are ordinary
  configs, so each can be rerun or edited on its own.

## 16. Open work

- **Speed and memory.** The frontier interval rebuilds the convex hull per bootstrap replicate in
  Python; a vectorised hull would cut the time on large TEST sets. Parallel seeds send the whole data
  table to each worker; loading it once per worker would save memory.
- **Chained score dependencies.** Rule 5 of section 0.2 refuses them. Supporting them needs nested
  folds: within each outer fold of B, A refitted on the remaining folds to give B's training rows
  their features, which multiplies the fits by the number of folds at each level of the chain.
- **One budget helper.** Two budget rules exist on purpose: a budget stage flags about b of the benign
  VAL rows reaching it (a quantile), and the budget-matched control takes the highest threshold whose
  VAL false alarm rate is at least the target. A shared helper could hold both.
