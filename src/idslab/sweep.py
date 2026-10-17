# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Sweeps: one base config, several variants, one comparison.

A sweep file names a base config and either named ``variants`` (each a set of changes to dotted config
keys) or a ``grid`` (every combination of the listed values). Every variant is checked before anything
runs, written as a config of its own under ``<out>/<sweep>/configs/``, run like ``idslab run``, and
reported; the experiments are then compared in ``<out>/<sweep>/comparison/``.

```yaml
name: gate-models
base: r1_router_gate.yaml          # relative to the sweep file
variants:
  iforest: {}                      # the base as it is
  lof:
    stages.gate.candidates: [{class: sklearn.neighbors.LocalOutlierFactor, params: {n_neighbors: 50}}]
```
"""

from __future__ import annotations

import copy
import itertools
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from idslab.compare import compare, write_comparison
from idslab.config import ConfigError, dump_yaml, from_dict, load, parse_yaml
from idslab.report import write_report
from idslab.runner import data_fingerprints, run

log = logging.getLogger("idslab")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
MAX_VARIANTS = 64


class SweepError(ValueError):
    """An invalid sweep file; the message lists every problem found."""


@dataclass
class Variant:
    name: str
    label: str  # the changes, for people
    changes: dict
    raw: dict  # the variant's config, with paths still relative to the base config


@dataclass
class Sweep:
    name: str
    base_path: str
    variants: list[Variant]


def dotted_keys(raw: Any, prefix: str = "") -> list[str]:
    """Every dotted key of a config, for people writing sweep changes."""
    out = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            key = f"{prefix}{k}"
            out.append(key)
            out += dotted_keys(v, key + ".")
    elif isinstance(raw, list):
        for i, v in enumerate(raw):
            if isinstance(v, (dict, list)):
                out += dotted_keys(v, f"{prefix}{i}.")
    return out


def set_key(raw: dict, dotted: str, value: Any) -> None:
    """Set a dotted key; numeric parts index lists, missing mappings are created."""
    parts = dotted.split(".")
    node: Any = raw
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                raise SweepError(f"{dotted}: '{part}' is not an index of a list of {len(node)}")
            if last:
                node[int(part)] = value
            else:
                node = node[int(part)]
        elif isinstance(node, dict):
            if last:
                node[part] = value
            else:
                node = node.setdefault(part, {})
        else:
            raise SweepError(f"{dotted}: '{'.'.join(parts[:i])}' is not a mapping or a list")


def _apply(raw: dict, vname: str, changes: dict) -> list[str]:
    """Apply a variant's changes; returns the problems found."""
    errs = []
    for key, value in changes.items():
        try:
            set_key(raw, str(key), copy.deepcopy(value))
        except SweepError as e:  # noqa: PERF203  (report every bad key, not just the first)
            errs.append(f"variant {vname}: {e}")
    return errs


def _short(value: Any) -> str:
    text = json.dumps(value, separators=(",", ":"), default=str)
    return text if len(text) <= 40 else text[:37] + "..."


def load_sweep(path: str) -> Sweep:
    """Read and check a sweep file and build every variant's config (nothing runs)."""
    with open(path, encoding="utf-8") as f:
        return parse_sweep(f.read(), os.path.dirname(os.path.abspath(path)))


def parse_sweep(text: str, folder: str) -> Sweep:
    """Check sweep text whose relative ``base`` is read from ``folder``; build every variant."""
    try:
        spec = parse_yaml(text)
    except ConfigError as e:
        raise SweepError(str(e)) from None
    errs: list[str] = []
    if not isinstance(spec, dict):
        raise SweepError("a sweep file is a mapping with name, base, and variants or grid")
    unknown = sorted(set(spec) - {"name", "base", "variants", "grid"})
    if unknown:
        errs.append(f"unknown keys {unknown} (allowed: name, base, variants, grid)")
    name = str(spec.get("name", ""))
    if not NAME.match(name):
        errs.append("name: letters, digits and the characters . - _, starting with a letter or digit")
    base_rel = spec.get("base")
    base_path = os.path.normpath(os.path.join(folder, str(base_rel or "")))
    if not base_rel or not os.path.isfile(base_path):
        errs.append(f"base: config file {base_rel!r} not found (relative to the sweep file)")
    has_v, has_g = "variants" in spec, "grid" in spec
    if has_v == has_g:
        errs.append("give exactly one of variants (named changes) or grid (every combination)")
    if errs:
        raise SweepError("\n".join(errs))
    with open(base_path, encoding="utf-8") as f:
        base_raw = parse_yaml(f.read())
    if not isinstance(base_raw, dict):
        raise SweepError(f"base: {base_rel} is not a config mapping")

    plans: list[tuple[str, dict]] = []
    if has_v:
        variants = spec["variants"]
        if not isinstance(variants, dict) or not variants:
            raise SweepError("variants: a mapping from variant name to its changes")
        for vname, changes in variants.items():
            if not NAME.match(str(vname)):
                errs.append(f"variants: name {vname!r} may use letters, digits and the characters . - _")
            given = {} if changes is None else changes
            if not isinstance(given, dict):
                errs.append(f"variants.{vname}: a mapping from dotted key to value")
                continue
            plans.append((str(vname), given))
    else:
        grid = spec["grid"]
        if (
            not isinstance(grid, dict)
            or not grid
            or not all(isinstance(v, list) and v for v in grid.values())
        ):
            raise SweepError("grid: a mapping from dotted key to a non-empty list of values")
        keys = list(grid)
        for i, combo in enumerate(itertools.product(*(grid[k] for k in keys)), start=1):
            plans.append((f"g{i:02d}", dict(zip(keys, combo, strict=True))))
    if len(plans) > MAX_VARIANTS:
        errs.append(f"{len(plans)} variants; a sweep runs at most {MAX_VARIANTS}")
    if errs:
        raise SweepError("\n".join(errs))

    out = []
    base_dir = os.path.dirname(base_path)
    for vname, changes in plans:
        raw = copy.deepcopy(base_raw)
        errs += _apply(raw, vname, changes)
        raw["name"] = f"{name}-{vname}"
        try:
            from_dict(raw, base_dir=base_dir)
        except ConfigError as e:
            errs.append(f"variant {vname}: " + str(e).replace("\n", f"\nvariant {vname}: "))
        label = ", ".join(f"{k}={_short(v)}" for k, v in changes.items()) or "the base config"
        out.append(Variant(name=vname, label=label, changes=changes, raw=raw))
    if errs:
        raise SweepError("\n".join(errs))
    return Sweep(name=name, base_path=base_path, variants=out)


def _moved(raw: dict, from_dir: str, to_dir: str) -> dict:
    """The config with relative data and score-file paths rewritten for a config in ``to_dir``."""
    raw = copy.deepcopy(raw)

    def move(p: Any) -> Any:
        if not isinstance(p, str) or os.path.isabs(p):
            return p
        return os.path.relpath(os.path.normpath(os.path.join(from_dir, p)), to_dir).replace(os.sep, "/")

    ds = raw.get("dataset") or {}
    for k in ("train", "test"):
        if k in ds:
            ds[k] = move(ds[k])
    for st in (raw.get("stages") or {}).values():
        for c in st.get("candidates") or [] if isinstance(st, dict) else []:
            if isinstance(c, dict) and "external" in c:
                ext = c["external"]
                if isinstance(ext, dict):
                    ext["path"] = move(ext.get("path"))
                else:
                    c["external"] = move(ext)
    return raw


def write_configs(sweep: Sweep, out_root: str) -> list[str]:
    """Write each variant as its own config file; returns the paths."""
    cdir = os.path.join(out_root, sweep.name, "configs")
    os.makedirs(cdir, exist_ok=True)
    paths = []
    for v in sweep.variants:
        p = os.path.join(cdir, f"{v.name}.yaml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# Sweep {sweep.name}, variant {v.name}: {v.label}\n")
            f.write(dump_yaml(_moved(v.raw, os.path.dirname(sweep.base_path), cdir)))
        paths.append(p)
    return paths


def run_sweep(
    path: str,
    *,
    out_root: str = "runs",
    seeds: list[int] | None = None,
    jobs: int = 1,
    force: bool = False,
    bootstrap: int = 1000,
) -> tuple[str, str, str]:
    """Run every variant of a sweep and compare them; returns the comparison files."""
    sweep = load_sweep(path)
    paths = write_configs(sweep, out_root)
    exp_dirs, names = [], {}
    total = len(sweep.variants)
    for i, (v, p) in enumerate(zip(sweep.variants, paths, strict=True), start=1):
        cfg = load(p)
        log.info("sweep %s: variant %d of %d, %s (%s)", sweep.name, i, total, v.name, v.label)
        fps = data_fingerprints(cfg)
        run(cfg, out_root=out_root, seeds=seeds, jobs=jobs, force=force, data_fps=fps)
        write_report(out_root, cfg, seeds or (), fps)
        d = os.path.normpath(os.path.join(out_root, cfg.name))
        exp_dirs.append(d)
        names[d] = f"{v.name}: {v.label}"
    c = compare(exp_dirs, bootstrap=bootstrap, names=names)
    c["sweep"] = {
        "name": sweep.name,
        "base": sweep.base_path,
        "variants": [{"name": v.name, "changes": v.changes} for v in sweep.variants],
    }
    return write_comparison(c, os.path.join(out_root, sweep.name, "comparison"))
