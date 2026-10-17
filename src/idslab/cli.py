# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Command line: idslab validate | run | report | export-splits | example | compare | sweep | dashboard."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from importlib import resources

from idslab import __version__
from idslab.adapters import ContractError
from idslab.compare import CompareError
from idslab.config import Config, ConfigError, load
from idslab.data import DataError
from idslab.fitting import FitError
from idslab.report import write_report
from idslab.runner import data_fingerprints, export_splits, run
from idslab.sweep import SweepError
from idslab.synthetic import write_example

EXAMPLES = ("r1_router_gate.yaml", "r2_anomaly_first.yaml", "r1_four_layer.yaml")


def validation_text(path: str, cfg: Config) -> str:
    """What ``idslab validate`` prints for a valid config (the dashboard shows the same lines)."""
    lines = [f"{path}: valid", f"  stages in selection order: {' > '.join(cfg.order())}"]
    for name in cfg.order():
        st = cfg.stages[name]
        tune = "chosen on VAL" if st.decision.tunable else "fixed"
        cands = ", ".join(c.label for c in st.candidates)
        lines.append(
            f"  {name}: {st.kind}, {st.decision.mode} {list(st.decision.values)} ({tune}), candidates {cands}"
        )
    lines.append(f"  controls compare against: {cfg.controls.first_stage}; seeds {list(cfg.protocol.seeds)}")
    return "\n".join(lines)


def _validate(args: argparse.Namespace) -> int:
    print(validation_text(args.config, load(args.config)))
    return 0


def _run(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    data_fps = data_fingerprints(cfg)  # hash the data once for the run and the report
    results = run(
        cfg, out_root=args.out, seeds=args.seeds, jobs=args.jobs, force=args.force, data_fps=data_fps
    )
    goal = cfg.protocol.objective
    for r in results:
        s = r["summary"]
        sys_ = s["controls"]["systems_test"]
        tag = "skipped (already done)" if r["skipped"] else f"{s['runtime_seconds']}s"
        print(
            f"seed {r['seed']}{' family ' + r['family'] if r.get('family') else ''}: {tag}; "
            f"TEST {goal} cascade {sys_['cascade'][goal]:.4f}, "
            f"first stage {sys_['B1_first_stage'][goal]:.4f}"
        )
    if not args.no_report:
        md, _ = write_report(args.out, cfg, args.seeds or (), data_fps)
        print(f"report: {md} (and report.html next to it)")
    return 0


def _report(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    md, js = write_report(args.out, cfg, args.seeds or ())
    print(f"report: {md}\nreport page: {os.path.splitext(md)[0]}.html\nreport data: {js}")
    return 0


def _export(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    paths = export_splits(cfg, args.out, args.seeds)
    print(
        f"wrote {len(paths)} split files (each with a .json sidecar) under {os.path.join(args.out, cfg.name)}"
    )
    return 0


def _example(args: argparse.Namespace) -> int:
    target = args.directory
    data_dir = os.path.join(target, "data")
    targets = [os.path.join(target, n) for n in EXAMPLES]
    targets += [os.path.join(data_dir, n) for n in ("train.parquet", "test.parquet")]
    existing = [t for t in targets if os.path.exists(t)]
    if existing and not args.overwrite:
        print(f"already present: {', '.join(existing)}; use --overwrite to replace them", file=sys.stderr)
        return 2
    os.makedirs(target, exist_ok=True)
    for name in EXAMPLES:
        with resources.as_file(resources.files("idslab.examples") / name) as src:
            shutil.copyfile(src, os.path.join(target, name))
    train, test = write_example(data_dir)
    print(f"wrote {train}, {test} and {', '.join(EXAMPLES)} in {target}")
    print(f"try: idslab run {os.path.join(target, EXAMPLES[0])} --out {os.path.join(target, 'runs')}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    from idslab.compare import compare, write_comparison  # noqa: PLC0415

    c = compare(args.experiments, bootstrap=args.bootstrap)
    out = args.out or os.path.join(
        "comparisons", "-vs-".join(os.path.basename(os.path.normpath(p)) for p in args.experiments)[:120]
    )
    md, _, html = write_comparison(c, out)
    goal = c["objective"]
    for h in c["experiments"]:
        cas, first = (h["cascade"] or {}).get("mean"), (h["first_stage"] or {}).get("mean")
        print(f"{h['name']}: TEST {goal} cascade {_num(cas)}, first stage alone {_num(first)}")
    for p in c["paired"]:
        n = len(p["per_unit"])
        print(
            f"{p['other']} minus {p['base']}: {_num(p['mean_diff'], signed=True)} over {n} paired units "
            f"(CI above 0 in {p['units_ci_above_zero']}, below 0 in {p['units_ci_below_zero']})"
        )
    for w in c["warnings"]:
        print(f"warning: {w}")
    print(f"comparison: {md} (and {html})")
    return 0


def _num(v: float | None, signed: bool = False) -> str:
    return "n/a" if v is None else (f"{v:+.4f}" if signed else f"{v:.4f}")


def _sweep(args: argparse.Namespace) -> int:
    from idslab.sweep import load_sweep, run_sweep  # noqa: PLC0415

    if args.dry_run:
        sw = load_sweep(args.sweep)
        print(f"{args.sweep}: valid; {len(sw.variants)} variants of {sw.base_path}")
        for v in sw.variants:
            print(f"  {v.name}: {v.label}")
        return 0
    md, _, html = run_sweep(
        args.sweep,
        out_root=args.out,
        seeds=args.seeds,
        jobs=args.jobs,
        force=args.force,
        bootstrap=args.bootstrap,
    )
    print(f"comparison: {md} (and {html})")
    return 0


def _dashboard(args: argparse.Namespace) -> int:
    from idslab.dashboard.server import App, make_server  # noqa: PLC0415  (only this command needs it)

    app = App(args.workspace)
    server = make_server(args.host, args.port, app)
    host, port = server.server_address[:2]
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"  # noqa: S104
    print(f"IDSLab dashboard for {app.ws.root} at {url} (Ctrl+C to stop)", flush=True)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: the dashboard runs commands and has no login; keep it on a trusted network")
    if args.open:
        import webbrowser  # noqa: PLC0415

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.job.stop()
        server.server_close()
    return 0


def _jobs(text: str) -> int:
    v = int(text)
    if v == 0 or v < -1:
        raise argparse.ArgumentTypeError("--jobs must be a positive integer or -1")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="idslab", description="Evaluate staged intrusion detection designs.")
    p.add_argument("--version", action="version", version=f"idslab {__version__}")
    p.add_argument("--debug", action="store_true", help="show the full traceback on errors")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="check a config without reading data")
    v.add_argument("config")
    v.set_defaults(func=_validate)

    r = sub.add_parser("run", help="fit, select on VAL, evaluate on TEST, write results and the report")
    r.add_argument("config")
    r.add_argument("--out", default="runs", help="output root (default: runs)")
    r.add_argument("--seeds", type=int, nargs="+", help="run only these seeds")
    r.add_argument("--jobs", type=_jobs, default=1, help="seeds run in parallel (default 1, -1 = all cores)")
    r.add_argument("--force", action="store_true", help="rerun seeds that are already done")
    r.add_argument("--no-report", action="store_true", help="skip writing report.md")
    r.set_defaults(func=_run)

    rep = sub.add_parser("report", help="aggregate finished seeds into report.md and report.json")
    rep.add_argument("config")
    rep.add_argument("--out", default="runs")
    rep.add_argument("--seeds", type=int, nargs="+", help="also include these seeds")
    rep.set_defaults(func=_report)

    x = sub.add_parser("export-splits", help="write row ids, folds and fingerprints for external score jobs")
    x.add_argument("config")
    x.add_argument("--out", default="splits", help="output root (default: splits)")
    x.add_argument("--seeds", type=int, nargs="+", help="only these seeds")
    x.set_defaults(func=_export)

    e = sub.add_parser("example", help="write the synthetic dataset and the two reference configs")
    e.add_argument("directory")
    e.add_argument("--overwrite", action="store_true")
    e.set_defaults(func=_example)

    cp = sub.add_parser("compare", help="compare finished experiments side by side, paired where possible")
    cp.add_argument(
        "experiments", nargs="+", help="experiment folders (<out>/<name>); the first is the baseline"
    )
    cp.add_argument("--out", help="folder for compare.md/.json/.html (default: comparisons/<names>)")
    cp.add_argument("--bootstrap", type=int, default=1000, help="paired bootstrap replicates (default 1000)")
    cp.set_defaults(func=_compare)

    sw = sub.add_parser("sweep", help="run every variant of a sweep file and compare them")
    sw.add_argument("sweep", help="sweep file (YAML: name, base, and variants or grid)")
    sw.add_argument("--out", default="runs", help="output root (default: runs)")
    sw.add_argument("--seeds", type=int, nargs="+", help="run only these seeds")
    sw.add_argument("--jobs", type=_jobs, default=1, help="seeds run in parallel (default 1, -1 = all cores)")
    sw.add_argument("--force", action="store_true", help="rerun seeds that are already done")
    sw.add_argument("--bootstrap", type=int, default=1000, help="paired bootstrap replicates (default 1000)")
    sw.add_argument(
        "--dry-run", action="store_true", help="check the sweep and list its variants, run nothing"
    )
    sw.set_defaults(func=_sweep)

    d = sub.add_parser("dashboard", help="open the web dashboard over a workspace folder")
    d.add_argument("--workspace", default=".", help="folder with configs, data and runs (default: .)")
    d.add_argument("--host", default="127.0.0.1", help="address to bind (default: 127.0.0.1)")
    d.add_argument("--port", type=int, default=8765, help="port (default 8765, 0 = any free port)")
    d.add_argument("--open", action="store_true", help="open the page in the default browser")
    d.set_defaults(func=_dashboard)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in ("run", "export-splits", "sweep"):  # progress lines on stderr; results stay on stdout
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log = logging.getLogger("idslab")
        if not log.handlers:  # main() can run more than once in a process (tests)
            log.addHandler(handler)
        log.setLevel(logging.INFO)
    try:
        return int(args.func(args))
    except (
        ConfigError,
        DataError,
        FitError,
        ContractError,
        CompareError,
        SweepError,
        ImportError,
        OSError,
    ) as e:
        print(f"idslab: {e}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, IndexError) as e:
        if args.debug:
            raise
        print(f"idslab: {type(e).__name__}: {e} (rerun with --debug for the traceback)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
