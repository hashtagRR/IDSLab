# Comparing designs and sweeps

Two questions come up once a design runs: is design B better than design A on the same data, and how
does one setting change the result? `idslab compare` answers the first, `idslab sweep` the second.

## Comparing experiments

```bash
idslab compare runs/r1-router-gate runs/r2-anomaly-first
```

Each argument is an experiment folder (`<out>/<name>`, holding `report.json`). The first one is the
baseline. The command prints a summary and writes `compare.md`, `compare.json` and `compare.html` to
`comparisons/<names>` (or `--out DIR`).

The comparison shows, per experiment:

- the cascade and its first stage alone on TEST, and their difference;
- the frontier gap;
- how often selection kept each stage on, and what switching it off costs;
- the expected cost per flow, when the reports carry stage cost.

**Paired differences.** When two experiments ran on the same data with the same splits (same data
files, same seeds and fractions), every unit evaluated both cascades on exactly the same TEST flows.
The comparison then gives, per unit, the experiment's cascade minus the baseline's, with a paired
bootstrap 95% interval (`--bootstrap N` replicates, 1000 by default). This is much more sensitive than
comparing two means: it removes the variation that both designs share. A unit whose data, split or TEST
rows differ is listed as skipped instead.

Only units that each report included take part, so stale units (changed config, data, score files or
version) never enter a comparison. A warning appears when the experiments use different objectives
(only the baseline's objective is paired), different modes, or different data files.

## Sweeps

A sweep file names a base config and either named variants or a grid:

```yaml
name: gate-models
base: ../r1_router_gate.yaml        # relative to the sweep file
variants:
  iforest: {}                       # the base config as it is
  lof:
    stages.gate.candidates: [{class: sklearn.neighbors.LocalOutlierFactor, params: {n_neighbors: 50}}]
  fixed-cutoff:
    stages.router.decision: {threshold: 0.5, off: attack}
```

```yaml
name: objectives
base: ../r1_router_gate.yaml
grid:                               # every combination: 2 x 2 = 4 variants (g01 to g04)
  protocol.objective: [accuracy, mcc]
  stages.gate.decision: [{budget: [0.01], off: normal}, {budget: [0.05], off: normal}]
```

Keys are dotted paths into the config. Numbers index lists: `stages.gate.candidates.0.class` changes
the first candidate's class. A key that does not exist yet is created, so `stages.gate.cost: 5` adds a
declared cost. A sweep runs at most 64 variants.

```bash
idslab sweep sweeps/gate-models.yaml --dry-run      # check every variant, run nothing
idslab sweep sweeps/gate-models.yaml --out runs     # run, report and compare
```

The sweep checks every variant with the config loader before anything runs, so a typo in variant 9
stops the sweep at once instead of after eight runs. Then, under the output root:

```text
runs/
  gate-models/
    configs/iforest.yaml, lof.yaml, fixed-cutoff.yaml   each variant as a config of its own
    comparison/compare.md, .json, .html                 all variants, the first one as baseline
  gate-models-iforest/                                  one experiment per variant, as idslab run makes
  gate-models-lof/
  gate-models-fixed-cutoff/
```

Every variant uses the same data and splits, so the comparison pairs them unit by unit. The variant
configs are ordinary configs: rerun one with `idslab run runs/gate-models/configs/lof.yaml`, or open it
in the dashboard. `--seeds`, `--jobs`, `--force` and `--bootstrap` work as for `run` and `compare`.
Rerunning a sweep reuses finished units, like `run`.

## In the dashboard

- **Compare:** tick two or more experiments (the first one ticked is the baseline; **Make baseline**
  changes it) and press **Compare**. The page shows the chart, the tables and the paired differences;
  **Save as files** writes them like `idslab compare`.
- **Sweeps:** lists the sweep files in the workspace. **New sweep** starts one from a base config. The
  sweep editor checks the file as you type, lists the variants with their changes, shows every dotted
  key of the base config, and runs the sweep as a job; when it finishes, **Open folder** leads to the
  comparison.
