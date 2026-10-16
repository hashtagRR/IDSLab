# IDSLab dashboard: source reference

A local web UI over one workspace folder. Standard library only (`http.server`), so it adds no
dependencies.

**Using it?** See [docs/dashboard.md](../../../docs/dashboard.md) for how to start it and what each
page does. This page is the source-level reference: file layout, the HTTP API and the security model,
for anyone reading or extending the implementation.

```bash
idslab dashboard --workspace demo            # http://127.0.0.1:8765/
```

![The config editor](../../../docs/img/dashboard-editor.png)

## Layout

| File | What it is |
|---|---|
| `server.py` | the HTTP server: `App` (workspace, job, validation), the request handler with its guards, and `make_server` |
| `workspace.py` | `Workspace`: path checks (relative, inside the root, no hidden parts), config discovery, run discovery by layout (`<name>/<mode>/seed_<s>`), file listing; `describe(cfg)` for the cascade preview |
| `catalog.py` | the models and pipeline steps the builder offers, with parameter forms read from each class's constructor, plus plugin entries |
| `jobs.py` | `build_command` turns a request into the argv of `python -m idslab.cli`, refusing what the CLI parser would; `Job` runs one subprocess at a time in its own process group, keeps the last 5,000 log lines and stops the whole group |
| `static/index.html` | the page shell: sidebar navigation, top bar, the `<main>` the pages render into |
| `static/core.js` | helpers: `el()` (DOM from `textContent`, never `innerHTML`), `api()`, formatting, tables, the shared job poller |
| `static/app.js` | the hash router (`#/`, `#/configs`, `#/config/<path>`, `#/builder/<path>`, `#/new`, `#/runs`, `#/run/<path>/<tab>`, `#/unit/<path>`, `#/job`, `#/command/<cmd>`, `#/example`, `#/files/<path>`), theme and footer |
| `static/editor.js` | configs list, new config, the YAML editor with validation as you type, and the command forms |
| `static/builder.js` | the visual builder: canvas, ports and wires, node moves, the stage, dataset, protocol and controls panels; edits the config as data and lets the server write the YAML |
| `static/views.js` | overview, runs, one run, one unit, the job log, example data, the file browser |
| `static/charts.js` | SVG charts: grouped bars with error bars, and the cascade map drawn from parsed routes |
| `static/styles.css` | all styling; colours are tokens on `:root` with a dark set |

Static files are served as they are, so a change shows up on a browser reload; a change to the Python
files needs a restart.

## HTTP API

| Method | Path | Returns or does |
|---|---|---|
| GET | `/`, `/static/<file>` | the page and its assets (only the files listed in `server.STATIC`) |
| GET | `/api/meta` | version, workspace, commands, default output folders, modes, objectives, installed extras, CPU count |
| GET | `/api/configs` | YAML files in the workspace that look like configs: path, name, stage count, mode |
| GET | `/api/config?path=` | the text of one config |
| GET | `/api/templates` | the shipped reference configs |
| GET | `/api/runs` | every experiment found: units done, report headline |
| GET | `/api/run?path=` | one experiment: units, `report.json`, matching configs |
| GET | `/api/unit?path=` | one unit's `summary.json` and `DONE` |
| GET | `/api/files?path=` | a folder listing |
| GET | `/api/catalog` | models per stage kind and pipeline steps, with parameter forms |
| GET | `/api/datafiles` | Parquet and CSV files in the workspace, outside run outputs |
| GET | `/api/dataset?path=` | columns of a data file from a sample, with suggested label, type, normal value and categorical columns |
| GET | `/api/layout?path=` | saved node positions of a config |
| GET | `/api/job[?tail=N]` | the job: state, command, target, timings, log |
| GET | `/files/<path>[?download=1]` | a workspace file: report pages as HTML under a no-script policy, text as plain text, anything else as a download |
| POST | `/api/validate` | `{yaml, path}`: the CLI's loader; returns the parsed design or the error |
| POST | `/api/config/save` | `{path, yaml, overwrite}`: writes a `.yaml` or `.yml` file, then validates it |
| POST | `/api/parse` | `{yaml}`: the config as data, for the builder |
| POST | `/api/dump` | `{config, path}`: the config written as YAML (the CLI's rules), then validated |
| POST | `/api/layout` | `{path, positions}`: writes `<config>.layout.json` |
| POST | `/api/job` | `{command, config, out, seeds, jobs, force, no_report, debug}` or `{command: example, directory, overwrite}`: starts a CLI job |
| POST | `/api/job/stop` | stops the running job |

## Security model

The dashboard runs commands and has no login, so it is built for one user on one machine:

- it binds to 127.0.0.1 unless told otherwise, and prints a warning for any other address;
- every request must carry the server's own `Host` (blocks DNS rebinding);
- POST takes JSON only, with a same-origin `Origin` when one is sent, so another site cannot send a
  request without a CORS preflight, which the server never approves;
- paths are relative to the workspace, resolved with symlinks, and refused if they leave it or contain
  a hidden part; configs are written only as `.yaml` or `.yml`;
- the page has a strict Content Security Policy (scripts and styles from the server only, no inline
  code); report pages get a policy that allows no scripts at all, and other text is served as
  `text/plain`;
- the browser receives data only; every string from a file reaches the DOM through `textContent`.

`tests/test_dashboard.py` covers the path rules, discovery, command building and these guards
through a live server.
