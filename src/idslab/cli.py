# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Command line: idslab validate | run | report | example."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from importlib import resources

from idslab import __version__
from idslab.adapters import ContractError
from idslab.config import ConfigError, load
from idslab.data import DataError
from idslab.fitting import FitError
from idslab.report import write_report
from idslab.runner import data_fingerprints, run
from idslab.synthetic import write_example

EXAMPLES = ("r1_router_gate.yaml", "r2_anomaly_first.yaml")


def _validate(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    print(f"{args.config}: valid")
    print(f"  stages in selection order: {' > '.join(cfg.order())}")
    for name in cfg.order():
        st = cfg.stages[name]
        tune = "chosen on VAL" if st.decision.tunable else "fixed"
        cands = ", ".join(c.label for c in st.candidates)
        print(
            f"  {name}: {st.kind}, {st.decision.mode} {list(st.decision.values)} ({tune}), candidates {cands}"
        )
    print(f"  controls compare against: {cfg.controls.first_stage}; seeds {list(cfg.protocol.seeds)}")
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
            f"seed {r['seed']}: {tag}; TEST {goal} cascade {sys_['cascade'][goal]:.4f}, "
            f"first stage {sys_['B1_first_stage'][goal]:.4f}"
        )
    if not args.no_report:
        md, _ = write_report(args.out, cfg, args.seeds or (), data_fps)
        print(f"report: {md}")
    return 0


def _report(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    md, js = write_report(args.out, cfg, args.seeds or ())
    print(f"report: {md}\nreport data: {js}")
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

    e = sub.add_parser("example", help="write the synthetic dataset and the two reference configs")
    e.add_argument("directory")
    e.add_argument("--overwrite", action="store_true")
    e.set_defaults(func=_example)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, DataError, FitError, ContractError, ImportError, FileNotFoundError) as e:
        print(f"idslab: {e}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, IndexError) as e:
        if args.debug:
            raise
        print(f"idslab: {type(e).__name__}: {e} (rerun with --debug for the traceback)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
