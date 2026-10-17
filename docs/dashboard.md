# Dashboard

```bash
idslab dashboard --workspace demo --open
```

| Option        | Default     | Meaning                                                            |
|---------------|-------------|--------------------------------------------------------------------|
| `--workspace` | `.`         | the folder the dashboard works in: configs, data and run outputs    |
| `--host`      | `127.0.0.1` | address to listen on                                               |
| `--port`      | `8765`      | port; `0` picks a free one                                         |
| `--open`      |             | open the page in the default browser                               |

![Results of an experiment in the dashboard](img/dashboard-run.png)

On Windows, see [Windows, step by step](../INSTALL.md#windows) for installing, starting
and stopping the dashboard.

The page works without a network connection and stores nothing outside the workspace. The browser
keeps only the theme, the last output folder and which output folder an experiment shows.

## Experiments

An experiment is a config file together with the results written under its name
(`<out>/<name>/`). The dashboard keeps no record of its own: it reads the files each time.

- An output folder belongs to the config with the same `name`.
- When several configs share a name, the config fingerprints stored by the finished units decide.
  If they match exactly one config, the folder belongs to it.
- If they match none or several, the folder is listed on its own with the reason, readable but not
  editable. The same happens to a folder whose config is gone. Give each config its own name to avoid this.

The home page lists every experiment with its state and the next thing to do:

| State          | Next action                       |
|----------------|-----------------------------------|
| Draft          | Continue setup, Open builder      |
| Data missing   | Fix dataset                       |
| Needs fixing   | Fix dataset, the cascade, the evaluation settings or the YAML, wherever the first problem is |
| Running        | View progress                     |
| Unfinished     | View runs                         |
| Completed      | View results, View report         |
| Needs a new run | Run again, View results (every finished unit is from another IDSLab version) |

**New experiment** starts from one of the three reference designs, from a YAML file already in the
workspace (or pasted), or from a blank design. **Use this configuration** on a template saves a copy
with its own name, next to the data folder when one matches the template's paths. **Preview in builder**
opens the template without saving anything.

Each experiment has five pages:

| Page    | What it holds |
|---------|---------------|
| Setup   | the guided steps below, and the YAML view |
| Runs    | one row per run unit (seed, or seed and held-out family) with its state and TEST figures; Export splits |
| Results | the TEST objective against the first stage alone, stage effects, unseen attacks and types, comparisons and uncertainty, reproducibility |
| Report  | the real `report.html`, links to `report.md` and `report.json`, Rebuild report, and the frozen cascade of any finished unit |
| Files   | the config, its builder layout, every unit and the output folder |

## Setup

The four steps are **Data**, **Cascade**, **Evaluation** and **Review & run**. Any step can be opened at
any time. Next lists the problems still on the current step before moving on. A run stays blocked
until the config validates.

- **Data**: the training file, and a test file or a share of the training file held out as TEST. It
  reads the columns from a sample. Label, attack type, benign value, row id and dropped columns are
  set here, as are merged types (`type_map`, `type_fill`) and the text columns that `Encode` turns
  into numbers. Suggestions read from the data are offered as buttons, never applied on their own.
  The loader's problems appear under the field they belong to.
- **Cascade**: the visual builder (below).
- **Evaluation**: the objective (every metric, cost of errors, recall at a false-alarm cap, and any
  objective an installed plugin adds), seeds, protocol, search, fractions and folds, and the controls.
  Every choice is made on VAL; TEST is read once, after the cascade is frozen.
- **Review & run**: what will run, the saved file it runs, the output folder (by default the one
  that already holds this experiment's results, so finished units are reused), seeds, parallel
  units, and under Advanced `--force`, `--no-report` and `--debug`. The same command is shown as a
  CLI line.
  With unsaved changes the button reads **Save and run**: a run always reads the file on disk.

The **YAML view** edits the same draft as the steps. Text typed there is saved exactly as typed,
comments included. A change made in the steps or the builder writes the YAML again with the
CLI's writer. When that would drop comments or hand formatting, the first such change asks first
and shows the difference. **Keep the YAML as written** undoes the change.

The header shows Saved, Unsaved changes or the number of problems, with **Save**, **Save as** and
**Discard changes**. Leaving an experiment with unsaved changes asks first; moving between its own
pages keeps the draft.

## Builder

![The builder: a four-stage design on the canvas, one stage open in the inspector](img/dashboard-builder.png)

- **Add a stage** by clicking a classifier, anomaly detector or type stage in the palette, or by
  dragging it onto the canvas. The first stage becomes the entry.
- **Route an output** in any of four ways:
  - drag its port (the dots on a stage's right edge) onto a stage or onto ATTACK or BENIGN;
  - click the port, then click the destination;
  - from the keyboard: Tab to the port, press Enter, Tab to the destination, press Enter;
  - choose the destination from the lists in the inspector.

  Escape cancels a route in progress.
- **Use a score as a feature** by dragging or clicking the orange diamond under a stage, then the
  later stage; or add it in the inspector. Click the dashed edge to remove it.
- **Move stages** by dragging them, or with the arrow keys on a selected stage. Delete removes the
  selected stage after asking. The layout is saved to `<config>.layout.json`, never into the config.

Problems in the drawing are listed above the canvas, and the draft stays editable while they exist:

- an output with no destination is drawn as a red stub;
- a loop is drawn in red and named stage by stage;
- a stage that no route reaches is listed by name.

**Show** selects the stage, and for a missing route it starts choosing the destination. Errors from
the loader that name a stage outline it and appear at the top of its inspector, in plain words with the
loader's own line underneath.

The inspector shows the essentials first: name, kind, entry, where each output goes and the decision.
Focused sections follow:

- models and parameters;
- hyperparameter search;
- preprocessing pipeline;
- calibration and selection;
- type stage settings;
- detector training;
- score features and training rows;
- declared cost.

Model and step lists come from the installed libraries and plugins. Any other class path (a custom
adapter, for example) can be typed in, and score files made elsewhere can be added as candidates.
Parameters the forms do not cover are listed as kept as written, and stay in the file.

## Results

The summary compares the cascade with the first stage alone (B1) on the TEST objective, and reads the
result with the paired bootstrap:

- **better in every unit** only when the 95% interval is above zero in every unit;
- **not a consistent improvement** when it is above zero in some units only;
- **no difference the bootstrap separates from zero** otherwise, with a note that a positive mean
  alone does not show the cascade is better.

Units of measure:

- differences of rates are in percentage points (pp), not relative percentages;
- a cost objective is stored as the negative average cost and shown as a cost, so lower is better.

The sd columns are the spread over units. In the standard protocol that is the spread over seeds.
With held-out families it mixes seeds and families, and the Unseen attacks section breaks results
down by family. The bootstrap counts are per unit, and no interval is pooled across units.

Results written by another IDSLab version stay readable, with a note. The version is part of every
unit's fingerprint, so a run computes those units again, and Rebuild report needs that run first: a
report counts only units of the running version.

The frozen cascade on the Report page is drawn from the routes the unit stored. Units written by
IDSLab 0.2.0 or earlier did not store them. For those, the current config is used only when its
fingerprint matches the unit's. Otherwise the page says the routes cannot be drawn and lists the
frozen choices instead.

## Jobs

Run, report, export splits, example, compare and sweep start `python -m idslab.cli ...` in the
workspace folder. A job therefore writes exactly what the same command in a shell would, and the
two resume each other's runs.

- One job runs at a time. While it runs, every Run button says so and links to it, and the top
  bar shows it on every page.
- The Jobs page shows the command, config, state and elapsed time. Progress is what the job's log
  says: the sweep variant, finished and reused units, the current unit and its phase (fitting a
  stage, selected on VAL, evaluating on TEST). There is no estimated percentage.
- Units running in parallel worker processes do not log. For those the count comes from the DONE
  files written since the job started.
- **Stop** ends the command and its worker processes. Finished units stay; the unit in progress is
  lost. Stopping the dashboard (Ctrl+C) also stops a running job.
- Running again skips every unit whose config, data, split and package fingerprints match a
  finished one. That is what resuming means here: nothing inside a unit is checkpointed.
- A failed job shows the error line, hints for common causes and the full log.
- **Past activity** lists finished units, reports and comparisons read from the files in the
  workspace. The dashboard keeps the log of the latest job only.

## Other pages

| Page      | What it does |
|-----------|--------------|
| Templates | the three reference designs with their purpose, route map, Use this configuration and Preview in builder |
| Datasets  | data files with size and the configs that read them; columns and suggestions for one file; write the example data |
| Reports   | every output folder with its report state (built, out of date, partial, not built), Open and Rebuild; saved comparisons |
| Help      | the workflow, the words used, stopping and resuming, builder keys, links to these docs |
| Compare   | experiments side by side, paired on the same TEST flows when they share data and splits ([guide](compare-and-sweeps.md)) |
| Sweeps    | write, check and run sweep files: one base config, several variants ([guide](compare-and-sweeps.md)) |
| Files     | read-only folder browser; text and report pages open in a tab, other files download |
| Unit      | frozen configuration, systems on TEST, every fitted candidate, the selection trace and the notes of one seed |

![The YAML view of an experiment, with validation](img/dashboard-editor.png)

Addresses from earlier versions still work and open the page that now holds the same thing:

| Earlier address              | Opens                    |
|------------------------------|--------------------------|
| `#/config/<file>`            | its YAML view            |
| `#/builder/<file>`           | its Cascade step         |
| `#/run/<folder>`             | its experiment           |
| `#/command/run/<file>`       | its Review & run step    |
| `#/job`                      | Jobs                     |
| `#/example`                  | Datasets                 |
| `#/configs`, `#/runs`        | Experiments              |

## Accessibility

- Every control has a label and can be reached with the keyboard, including the ports and stages
  of the builder.
- Page changes and job results are announced to screen readers.
- Dialogs keep focus inside them.
- Both themes follow the system setting until the theme button is used.
- The layout works down to phone width.

## Security

The [source reference](../src/idslab/dashboard/README.md) lists the HTTP API and how each guard is
implemented.

The dashboard runs commands and has no login, so it is built for one user on one machine:

- it listens on 127.0.0.1 unless `--host` says otherwise (it prints a warning then);
- requests must carry the dashboard's own host name, which blocks DNS rebinding;
- changes must come as same-origin JSON, which another web site cannot send;
- every path must lie inside the workspace; hidden files and folders are refused, and configs are
  saved only as `.yaml` or `.yml`;
- report pages are served with a policy that allows no scripts, and text files are served as plain
  text.

Do not expose it on a shared network.
