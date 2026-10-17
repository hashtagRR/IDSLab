# Known limitations

What the numbers in a report do and do not show. Each point is also stated where it applies: in the
report statements, the selection trace or the run summary.

## Selection

- **Greedy selection can miss combinations.** Each stage is judged while the stages after it are off,
  so a stage that only helps together with a later one can be switched off. An off stage means "not
  chosen by this procedure", not "cannot help". `protocol.search: joint` measures the gap: on the R2
  example the joint check finds a better combination than greedy selection in every seed.
  `protocol.search: greedy_fixed` narrows the gap where the later stage has a fixed decision.
- **AUC selection is a heuristic.** `select_by: auc` (the default for deciding classifiers) keeps the
  candidate with the best AUC on the VAL rows reaching the stage, which need not be the one that helps
  the cascade most; `select_by: objective` chooses candidate and value together at a higher cost.
- **Few reaching rows give coarse choices.** A stage deep in the cascade may see only a few dozen VAL
  rows. Below `min_val_rows` it is switched off; above it, a detector budget is a quantile of a small
  benign sample (the run summary logs the share actually flagged).

## Uncertainty

- **Bootstrap intervals are conditional on the fitted system.** They describe TEST sample variation
  for the models as fitted, not variation from refitting. The spread over seeds is reported
  separately; with three seeds it is a rough guide.
- **The frontier gap is evidence on this TEST sample**, not a guarantee about other data.
- **The prevalence estimate assumes label shift only.** Held-out families change the attack mix, so
  it does not explain every difference between VAL and TEST.

## Detectors refitted on each batch

- Results with `fit_on: pool` assume the whole split is processed as one batch; a deployment that sees
  smaller batches behaves differently. The bootstrap resamples stored predictions and does not refit
  the detector on each resample.

## Fitting

- Out-of-fold refits for score features reuse the hyperparameters the source stage's search found
  on all of FIT, a small approximation. Repeating the search per fold is not implemented.
- With `--jobs` above 1, every worker process receives the whole data table, and workers do not log
  progress.

## External scores

- IDSLab checks that a score file matches the exported splits, covers the needed rows, and never
  carries a fitted score on a row the producer declares it trained on. It cannot see the outside
  training itself: the `producer` block is a declaration, and the result is only as clean as that job.

## Results across versions

- Every version that can change results gets a new number, and finished units are reused only when
  the version matches. Upgrading IDSLab therefore reruns existing experiments.

## The example data

- The synthetic dataset shows how IDSLab works. Its numbers say nothing about any real network or
  benchmark.

## Dashboard

- Built for one user on one machine: no login, one job at a time. Keep it on 127.0.0.1.
