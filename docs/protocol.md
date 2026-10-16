# Protocol and controls

IDSLab runs the same protocol for every design, so two cascades are compared on equal terms and a
cascade is always compared with its own first stage. This page explains what happens in a run and
what each reported number means.

## Splits

For each seed:

| Split    | Taken from                      | Used for                                                    |
|----------|---------------------------------|-------------------------------------------------------------|
| TEST     | the test file, or `test_fraction` of the train file | evaluation after the configuration is frozen |
| VAL      | `val_fraction` of the train partition | every choice: candidates, thresholds, switching stages off |
| FIT      | the rest of the train partition | fitting stages that are not calibrated                      |
| FIT-base | FIT minus CAL                   | fitting calibrated stages                                   |
| CAL      | `cal_fraction` of FIT           | calibrating calibrated stages, nothing else                 |

Cuts are stratified on the type when the dataset has one, otherwise on the label. Types too small to
split are merged into one stratum for cutting only (their labels stay), and each merge is logged in
the run summary.

**TEST labels never influence fitting, selection or any configuration choice.** The test suite checks
this by shuffling the TEST labels and requiring an identical frozen configuration. TEST features are
used by detectors refitted on each batch (`fit_on: pool`) and by the prevalence check; reports say so
when that applies.

## Fitting

Each stage fits on its own training rows (`train_on`, `fit_on`), with its whole pipeline. A
hyperparameter search refits the pipeline in every fold. A stage that uses another stage's scores
(`extra_features`, or `hard` rows) gets out-of-fold scores for its training rows: FIT is cut into
`oof_folds` folds, the source stage is refitted on the other folds and scores the held-out fold, so no
training row's feature comes from a model that saw that row. VAL and TEST rows get the fitted source
model's score. The out-of-fold refits reuse the hyperparameters the source's search found.

## Selection: greedy forward selection

Stages are visited in order: a topological order of the routes and score dependencies from the entry
stage, ties in config order. For each stage:

1. Fit its candidates.
2. Route VAL with earlier stages at their chosen settings and later stages off; take the VAL rows
   that reach the stage.
3. Choose the candidate (`select_by`).
4. If the decision is chosen on VAL, try every value plus off, scoring the whole VAL cascade by the
   objective (annotate stages: the type objective on the rows reaching them). Ties go to off, then to
   the first value.

The control first stage is never switched off. A stage that fewer than `min_val_rows` VAL rows reach
is switched off. Every candidate score, option and fallback is recorded in the selection trace.

**Limitation.** Greedy selection judges a stage while the stages after it are off, so it can reject
a stage that would help together with a later one. An off stage means "not chosen by this procedure",
not "cannot help". Set `protocol.search: joint` to measure this: the check evaluates every combination
of decision values and reports the best one as the system `joint_check`.

## Systems on TEST

After the freeze, every system below is scored on TEST with the same fitted first-stage model:

| System                         | Definition                                                         |
|--------------------------------|--------------------------------------------------------------------|
| `cascade`                      | the frozen design                                                  |
| `B1_first_stage`               | the first stage alone, at the threshold it uses inside the cascade |
| `B2_first_stage_val_cutoff`    | the first stage alone, at the cutoff from `controls.cutoff_grid` that is best on VAL |
| `budget_matched_first_stage`   | the first stage alone, at the highest threshold whose VAL false alarm rate is at least the cascade's |
| `joint_check`                  | with `search: joint`, the best combination found by the check      |

Each system reports accuracy, precision, recall, false positive rate, F1, MCC, balanced accuracy and
the confusion counts.

## Controls

**Cascade minus each control.** Paired bootstrap 95% intervals of the difference on TEST, resampling
benign and attack flows separately (`controls.bootstrap` replicates). The interval describes TEST
sample uncertainty for the fitted system; seed variation is reported separately as the standard
deviation over seeds. A report line "CI above 0 in 2 of 3 units" counts the seeds whose interval
excludes zero on the positive side.

**Frontier gap.** The cascade's true positive rate minus the first stage's at the cascade's false
positive rate, read from the first stage's TEST ROC curve. The interpolated value uses the curve's
upper convex hull (the best any threshold choice, randomised or not, can reach); the step value uses
single thresholds only. A positive gap is evidence that the cascade reaches an operating point that
moving the first stage's threshold does not. It is evidence on this TEST sample, not a guarantee.

**Stage removal.** Each stage switched off (to its `off` output) with everything else as selected.
The effect is the cascade minus that variant. Effects are contributions inside the chosen design and
need not add up.

**Prefix steps.** The cascade truncated after each stage in order (later stages off). Consecutive
differences add up to the change from "no stages" (every flow takes the entry stage's `off`) to the
full cascade.

**Prevalence check.** Attack share in FIT, VAL and TEST, and an EM estimate of the TEST share from the
calibrated first-stage scores (Saerens et al., 2002). The estimate assumes label shift only.

## Unseen attacks

In `heldout_family` mode each run unit holds one family out of training. The report adds, per family,
recall on the unseen family and on known attacks, the benign false alarm rate, and the frontier gap
and first-stage AUC on the benign and unseen flows. `natural_novelty` reports the same for the
declared novel types.

## Types

When a multiclass stage is active, each TEST flow gets a recorded type: the normal type for a benign
verdict; the predicted type when a multiclass stage on its path named one; `uncertain` below the
confidence value; `untyped_normal` when the type stage said normal but the verdict is attack; and
`untyped` when no type stage saw the flow. Type metrics are computed over true attacks, and the
three non-type labels count as wrong.

## Resume

A finished run unit writes `DONE` with fingerprints of the config, the data files, the split, every
external score file and the IDSLab version. `idslab run` skips a unit whose fingerprints all match
and reruns it otherwise (`--force` reruns regardless). Reports include only units whose fingerprints
match the current config, data and version, and list any unit they leave out.
