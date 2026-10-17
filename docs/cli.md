# Command line

```text
idslab [--version] [--debug] <command> ...
```

`--debug` shows the full traceback of an unexpected error. Errors in the config, the data or a score
file print one message and exit with code 2.

## idslab validate CONFIG

Loads and checks the config without reading data: keys, values, routes, score dependencies, model
classes and their parameters, optional extras. Prints the stages in selection order with their
decisions and candidates.

## idslab run CONFIG

| Option        | Default | Meaning                                                               |
|---------------|---------|-----------------------------------------------------------------------|
| `--out`       | `runs`  | output root; results go to `<out>/<name>/`                            |
| `--seeds`     |         | run only these seeds (they need not be in the config)                 |
| `--jobs`      | `1`     | run units in parallel; `-1` for all cores                             |
| `--force`     |         | rerun units that are already done                                     |
| `--no-report` |         | skip writing the report                                               |

Finished units whose fingerprints match are skipped, so an interrupted run continues where it
stopped. Progress goes to stderr; with `--jobs` above 1 the worker processes do not log.

## idslab report CONFIG

| Option    | Default | Meaning                                             |
|-----------|---------|-----------------------------------------------------|
| `--out`   | `runs`  | output root used by the run                         |
| `--seeds` |         | also include these seeds beyond `protocol.seeds`    |

Rebuilds `report.md`, `report.json` and `report.html` from the finished units.

## idslab export-splits CONFIG

| Option    | Default  | Meaning                    |
|-----------|----------|----------------------------|
| `--out`   | `splits` | output root                |
| `--seeds` |          | only these seeds           |

Writes the rows of every split per run unit, for models trained outside IDSLab
([Scores made elsewhere](models.md#scores-made-elsewhere)).

## idslab example DIRECTORY

Writes the synthetic dataset to `DIRECTORY/data` and the three reference configs to `DIRECTORY`.
`--overwrite` replaces files that exist.

## idslab compare EXPERIMENT EXPERIMENT ...

| Option        | Default                  | Meaning                                          |
|---------------|--------------------------|--------------------------------------------------|
| `--out`       | `comparisons/<names>`    | folder for `compare.md`, `.json` and `.html`     |
| `--bootstrap` | `1000`                   | paired bootstrap replicates                      |

Compares finished experiments (`<out>/<name>` folders); the first is the baseline. See
[Comparing designs and sweeps](compare-and-sweeps.md).

## idslab sweep SWEEP_FILE

| Option        | Default | Meaning                                                  |
|---------------|---------|----------------------------------------------------------|
| `--out`       | `runs`  | output root for the variants and the comparison          |
| `--seeds`, `--jobs`, `--force` |  | as for `run`                                   |
| `--bootstrap` | `1000`  | paired bootstrap replicates for the comparison           |
| `--dry-run`   |         | check the sweep and list its variants, run nothing       |

Runs every variant of a base config and compares them. See
[Comparing designs and sweeps](compare-and-sweeps.md).

## idslab dashboard

Starts the web dashboard; see [Dashboard](dashboard.md).
