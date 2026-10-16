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

## idslab dashboard

Starts the web dashboard; see [Dashboard](dashboard.md).
