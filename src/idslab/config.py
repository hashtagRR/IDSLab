# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Load and validate a cascade config (DESIGN.md sections 2 to 5).

Every check that can run without data runs here, so a wrong config fails before
any file is read: unknown keys, broken routing graph, missing ``off`` routes,
classes that do not import, parameters the estimator does not have, and
settings that do not fit together. Checks that need the data (multiclass route
names against the type values) run in ``check_against_data``.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import os
import re
import string
from dataclasses import dataclass, field
from typing import Any

import yaml

TERMINALS = ("BENIGN", "ATTACK")
OUTPUTS = {"classifier": ("attack", "benign"), "detector": ("anomalous", "normal")}
OBJECTIVES = ("accuracy", "mcc", "f1", "balanced_accuracy")
EXTRAS = {
    "xgboost": "xgboost",
    "lightgbm": "lightgbm",
    "pyod": "pyod",
    "imblearn": "imbalance",
    "torch": "torch",
}
DEFAULT_CUTOFFS = [round(0.05 + 0.025 * i, 3) for i in range(37)]

UNCERTAIN, DEFAULT, NEXT = "uncertain", "default", "next"
TYPE_OBJECTIVES = ("macro_f1", "accuracy", "auc")
DEFAULT_MIN_POOL = 20

MODES = ("standard", "heldout_family", "natural_novelty")

PLACEHOLDERS = {"seed", "family"}  # allowed in an external score path


class ConfigError(ValueError):
    """The config is invalid; the message lists every problem found."""


@dataclass(frozen=True)
class Candidate:
    cls: str
    params: dict
    search: dict | None
    score: dict | None
    label: str
    external: str | None = None  # path template of a score file made elsewhere (ExternalScore)


@dataclass(frozen=True)
class Decision:
    mode: str  # threshold | grid | budget | top_fraction | confidence
    values: tuple[float, ...]
    off: str  # output every flow takes when the stage is off ("next" for an annotate stage)
    listed: bool = False  # the config gave a list, so the value is chosen on VAL

    @property
    def tunable(self) -> bool:
        return self.mode == "grid" or (self.mode != "threshold" and self.listed and len(self.values) > 1)


@dataclass(frozen=True)
class Stage:
    name: str
    kind: str
    candidates: tuple[Candidate, ...]
    routes: dict
    decision: Decision
    train_on: Any
    calibrate: str
    fit_on: str
    pipeline: tuple[dict, ...]
    select_by: str
    min_val_rows: int
    role: str = "decide"  # decide | annotate
    extra_features: tuple[str, ...] = ()
    hard: dict | None = None  # {source, band} for train_on: hard
    type_objective: str = "macro_f1"
    prep_fit_on: str = "fit"  # rows that fit the preprocessing of a pool detector
    min_pool: int = DEFAULT_MIN_POOL

    @property
    def transductive(self) -> bool:
        return self.kind == "detector" and self.fit_on == "pool"


@dataclass(frozen=True)
class DatasetSpec:
    train: str
    test: str | None
    test_fraction: float | None
    label: str | None
    type: str | None
    normal_type: str
    type_map: dict
    type_fill: str | None
    drop: tuple[str, ...]
    id_column: str | None


@dataclass(frozen=True)
class Protocol:
    seeds: tuple[int, ...]
    val_fraction: float
    cal_fraction: float
    objective: str
    mode: str
    n_jobs: int
    subsample: dict
    oof_folds: int = 3
    search: str = "greedy"  # joint adds a check over every combination of decisions
    joint_max: int = 5000  # largest number of combinations the joint check evaluates
    family_level: str = "mapped"  # held-out families defined after (mapped) or before (raw) type_map
    families: tuple[str, ...] | None = None  # held-out families to run (None: every attack family)
    novel_types: tuple[str, ...] = ()  # natural novelty: raw types found only in TEST


@dataclass(frozen=True)
class Controls:
    first_stage: str
    bootstrap: int
    frontier_bootstrap: int
    cutoff_grid: tuple[float, ...]


@dataclass
class Config:
    name: str
    dataset: DatasetSpec
    protocol: Protocol
    entry: str
    stages: dict[str, Stage]
    controls: Controls
    base_dir: str
    raw: dict = field(repr=False)

    def order(self) -> list[str]:
        """Selection order: topological order of the routing graph joined with the score-dependency
        graph (a score source comes before every stage that uses it); ties keep config order."""
        return topo_order(self.entry, self.edges(), list(self.stages))

    def edges(self) -> dict[str, dict]:
        """Routing edges plus one edge from each score source to the stage using it."""
        out = {n: dict(s.routes) for n, s in self.stages.items()}
        for n in self.stages:
            for i, src in enumerate(self.sources(n)):
                if src in out:
                    out[src][f"__feeds_{n}_{i}"] = n
        return out

    def sources(self, name: str) -> tuple[str, ...]:
        """Stages whose scores the stage ``name`` uses: extra features and the hard-row source."""
        st = self.stages[name]
        hard = (st.hard["source"],) if st.hard else ()
        return tuple(dict.fromkeys(st.extra_features + hard))

    def fingerprint(self) -> str:
        """Hash of everything that can change one seed's result.

        ``protocol.seeds`` (which seeds exist) and ``protocol.n_jobs`` (how fast they
        run) are left out, so adding a seed or changing parallelism keeps finished
        seeds valid.
        """
        raw = copy.deepcopy(self.raw)
        proto = raw.get("protocol") or {}
        for k in ("seeds", "n_jobs"):
            proto.pop(k, None)
        raw["protocol"] = proto
        blob = json.dumps(raw, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()

    def path(self, p: str) -> str:
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.base_dir, p))


def topo_order(entry: str, routes: dict[str, dict], config_order: list[str]) -> list[str]:
    reach, todo = set(), [entry]
    while todo:
        n = todo.pop()
        if n in reach or n in TERMINALS or n not in routes:
            continue
        reach.add(n)
        todo.extend(routes[n].values())
    indeg = dict.fromkeys(reach, 0)
    for n in reach:
        for t in set(routes[n].values()):
            if t in indeg:
                indeg[t] += 1
    rank = {n: i for i, n in enumerate(config_order)}
    ready = sorted((n for n in reach if indeg[n] == 0), key=rank.__getitem__)
    out = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for t in set(routes[n].values()):
            if t in indeg:
                indeg[t] -= 1
                if indeg[t] == 0:
                    ready.append(t)
                    ready.sort(key=rank.__getitem__)
    return out


class _Loader(yaml.SafeLoader):
    """YAML 1.2 booleans: only true/false. YAML 1.1 would read the key ``off`` as False."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


class _Dumper(yaml.SafeDumper):
    """Writes configs the loader reads back unchanged: only true/false are booleans, so ``off``
    is written as a plain word."""


_Dumper.yaml_implicit_resolvers = _Loader.yaml_implicit_resolvers


class _Flow(dict):
    """A mapping written on one line, as in hand-written configs."""


class _FlowList(list):
    """A list written on one line."""


_Dumper.add_representer(_Flow, lambda d, v: d.represent_mapping("tag:yaml.org,2002:map", v, flow_style=True))
_Dumper.add_representer(
    _FlowList, lambda d, v: d.represent_sequence("tag:yaml.org,2002:seq", v, flow_style=True)
)


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _short(v: Any) -> bool:
    """A value that reads well on one line: a scalar, a list of scalars, or a small mapping of those."""
    if _scalar(v):
        return True
    if isinstance(v, list):
        return all(_scalar(x) for x in v) and len(v) <= 12
    if isinstance(v, dict):
        return len(v) <= 4 and all(_short(x) for x in v.values())
    return False


def _styled(v: Any, depth: int) -> Any:
    if isinstance(v, dict):
        out = {str(k): _styled(x, depth + 1) for k, x in v.items()}
        return _Flow(out) if depth >= 3 and _short(v) else out
    if isinstance(v, list):
        items = [_styled(x, depth + 1) for x in v]
        return _FlowList(items) if depth >= 2 and all(_scalar(x) for x in v) else items
    return v


def dump_yaml(raw: dict) -> str:
    """The config as YAML in the shape people write by hand: blocks for sections and stages, one-line
    maps for routes, decisions, parameters and pipeline steps. ``parse_yaml`` reads it back unchanged."""
    return yaml.dump(_styled(raw, 0), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def parse_yaml(text: str) -> Any:
    try:
        return yaml.load(text, Loader=_Loader)  # noqa: S506  (_Loader is a SafeLoader subclass)
    except yaml.YAMLError as e:
        raise ConfigError(f"not valid YAML: {e}") from None


def load(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = parse_yaml(f.read())
    return from_dict(raw, base_dir=os.path.dirname(os.path.abspath(path)))


def from_dict(raw: Any, base_dir: str = ".") -> Config:
    errs: list[str] = []
    if not isinstance(raw, dict):
        raise ConfigError("the config must be a mapping")
    raw = copy.deepcopy(raw)
    _keys(
        raw,
        {"name", "dataset", "protocol", "entry", "stages", "controls"},
        "config",
        errs,
        required={"name", "dataset", "entry", "stages"},
    )
    if errs:
        raise ConfigError("\n".join(errs))
    dataset = _dataset(raw["dataset"], errs)
    protocol = _protocol(raw.get("protocol") or {}, errs)
    stages_raw = raw["stages"]
    if not isinstance(stages_raw, dict) or not stages_raw:
        raise ConfigError("stages: give at least one stage as a mapping")
    stages = {}
    for name, d in stages_raw.items():
        st = _stage(str(name), d, errs)
        if st is not None:
            stages[str(name)] = st
    entry = str(raw["entry"])
    controls = _controls(raw.get("controls") or {}, entry, errs)
    if errs:
        raise ConfigError("\n".join(errs))
    cfg = Config(
        name=str(raw["name"]),
        dataset=dataset,
        protocol=protocol,
        entry=entry,
        stages=stages,
        controls=controls,
        base_dir=base_dir,
        raw=raw,
    )
    _graph(cfg, errs)
    if errs:
        raise ConfigError("\n".join(errs))
    _dependencies(cfg, errs)
    if errs:
        raise ConfigError("\n".join(errs))
    _semantics(cfg, errs)
    if errs:
        raise ConfigError("\n".join(errs))
    return cfg


def _is_int(v: Any) -> bool:
    """An integer that is not a boolean (YAML true would otherwise pass as 1)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _external(
    c: dict, i: int, cw: str, *, kind: str, fit_on: str, calibrate: str, errs: list[str]
) -> Candidate:
    """A candidate whose scores come from a file made elsewhere (DESIGN.md section 4.4)."""
    _keys(c, {"external", "label"}, cw, errs)
    ext = c["external"]
    path = ext.get("path") if isinstance(ext, dict) else ext
    if isinstance(ext, dict):
        _keys(ext, {"path"}, f"{cw} external", errs, required={"path"})
    if not isinstance(path, str) or not path:
        errs.append(f"{cw}: external needs a score file path (it may use {{seed}} and {{family}})")
        path = ""
    else:
        try:
            names = {f for _, f, _, _ in string.Formatter().parse(path) if f is not None}
        except ValueError as e:
            names = set()
            errs.append(f"{cw}: external path {path!r}: {e}")
        if names - PLACEHOLDERS:
            errs.append(
                f"{cw}: external path placeholders {sorted(names - PLACEHOLDERS)} not in {{seed}}, {{family}}"
            )
    if kind == "detector" and fit_on == "pool":
        errs.append(f"{cw}: a pool detector refits on each batch, which a score file cannot do")
    if kind == "multiclass" and calibrate != "none":
        errs.append(f"{cw}: external type scores cannot be calibrated here; set calibrate: none")
    label = str(c.get("label") or f"external#{i}")
    return Candidate(cls="external", params={}, search=None, score=None, label=label, external=path)


def _keys(
    d: Any, allowed: set[str], where: str, errs: list[str], required: set[str] | frozenset[str] = frozenset()
) -> bool:
    if not isinstance(d, dict):
        errs.append(f"{where}: expected a mapping")
        return False
    unknown = sorted(set(map(str, d)) - allowed)
    if unknown:
        errs.append(f"{where}: unknown keys {unknown} (allowed: {sorted(allowed)})")
    errs.extend(f"{where}: '{k}' is required" for k in sorted(required - set(d)))
    return True


def _dataset(d: Any, errs: list[str]) -> DatasetSpec:
    allowed = {
        "train",
        "test",
        "test_fraction",
        "label",
        "type",
        "normal_type",
        "type_map",
        "type_fill",
        "drop",
        "id_column",
    }
    if not _keys(d, allowed, "dataset", errs, required={"train"}):
        d = {}
    test, frac = d.get("test"), d.get("test_fraction")
    if (test is None) == (frac is None):
        errs.append("dataset: give exactly one of 'test' (a file) or 'test_fraction'")
    if frac is not None and not (isinstance(frac, (int, float)) and 0 < frac < 1):
        errs.append("dataset.test_fraction: must be between 0 and 1")
    if d.get("label") is None and d.get("type") is None:
        errs.append(
            "dataset: give 'label', 'type', or both (the binary label is derived from 'type' if needed)"
        )
    tmap = d.get("type_map") or {}
    if not isinstance(tmap, dict):
        errs.append("dataset.type_map: expected a mapping from raw type to group")
        tmap = {}
    if tmap and d.get("type") is None:
        errs.append("dataset.type_map: needs dataset.type")
    return DatasetSpec(
        train=str(d.get("train", "")),
        test=None if test is None else str(test),
        test_fraction=None if frac is None else float(frac),
        label=d.get("label"),
        type=d.get("type"),
        normal_type=str(d.get("normal_type", "Normal")),
        type_map={str(k): str(v) for k, v in tmap.items()},
        type_fill=d.get("type_fill"),
        drop=tuple(map(str, d.get("drop") or ())),
        id_column=d.get("id_column"),
    )


def _protocol(d: Any, errs: list[str]) -> Protocol:
    allowed = {
        "seeds",
        "val_fraction",
        "cal_fraction",
        "objective",
        "mode",
        "n_jobs",
        "subsample",
        "search",
        "family_level",
        "families",
        "novel_types",
        "oof_folds",
        "joint_max",
    }
    _keys(d, allowed, "protocol", errs)
    if not isinstance(d, dict):
        d = {}
    search_mode = d.get("search", "greedy")
    if search_mode not in ("greedy", "joint"):
        errs.append("protocol.search: greedy or joint")
        search_mode = "greedy"
    joint_max = d.get("joint_max", 5000)
    if not _is_int(joint_max) or joint_max < 1:
        errs.append("protocol.joint_max: a positive integer")
        joint_max = 5000
    mode = d.get("mode", "standard")
    if mode not in MODES:
        errs.append(f"protocol.mode: {mode} not in {MODES}")
    family_level = d.get("family_level", "mapped")
    if family_level not in ("mapped", "raw"):
        errs.append("protocol.family_level: mapped or raw")
    families = d.get("families")
    if families is not None and (
        not isinstance(families, list) or not families or not all(isinstance(x, str) for x in families)
    ):
        errs.append("protocol.families: a non-empty list of type names")
        families = None
    novel = d.get("novel_types") or []
    if not isinstance(novel, list) or not all(isinstance(x, str) for x in novel):
        errs.append("protocol.novel_types: a list of raw type names")
        novel = []
    if mode == "natural_novelty" and not novel:
        errs.append("protocol.novel_types: natural_novelty needs the raw types that appear only in TEST")
    if mode != "heldout_family":
        errs.extend(
            f"protocol.{k} applies to mode heldout_family only"
            for k in ("family_level", "families")
            if k in d
        )
    if mode != "natural_novelty" and "novel_types" in d:
        errs.append("protocol.novel_types applies to mode natural_novelty only")
    objective = d.get("objective", "accuracy")
    if objective not in OBJECTIVES:
        errs.append(f"protocol.objective: {objective} not in {OBJECTIVES}")
    seeds = d.get("seeds", [0, 1, 2, 3, 4])
    if not isinstance(seeds, list) or not seeds or not all(_is_int(s) for s in seeds):
        errs.append("protocol.seeds: give a non-empty list of integers")
        seeds = [0]
    if len(set(seeds)) != len(seeds):
        errs.append("protocol.seeds: seeds must be unique")
    fr = {}
    for k, default in (("val_fraction", 0.2), ("cal_fraction", 0.2)):
        v = d.get(k, default)
        if not isinstance(v, (int, float)) or not 0 < v < 0.5:
            errs.append(f"protocol.{k}: must be between 0 and 0.5")
            v = default
        fr[k] = float(v)
    sub = d.get("subsample") or {}
    if (
        not isinstance(sub, dict)
        or set(sub) - {"train", "test"}
        or not all(_is_int(v) and v > 0 for v in sub.values())
    ):
        errs.append("protocol.subsample: give {train: N, test: M} with positive integers")
        sub = {}
    oof_folds = d.get("oof_folds", 3)
    if not _is_int(oof_folds) or oof_folds < 2:
        errs.append("protocol.oof_folds: an integer of at least 2")
        oof_folds = 3
    n_jobs = d.get("n_jobs", 1)
    if not _is_int(n_jobs) or n_jobs == 0:
        errs.append("protocol.n_jobs: a non-zero integer (-1 means all cores)")
        n_jobs = 1
    return Protocol(
        seeds=tuple(seeds),
        val_fraction=fr["val_fraction"],
        cal_fraction=fr["cal_fraction"],
        objective=str(objective),
        mode=str(mode),
        n_jobs=n_jobs,
        subsample=dict(sub),
        oof_folds=int(oof_folds),
        search=str(search_mode),
        joint_max=int(joint_max),
        family_level=str(family_level),
        families=None if families is None else tuple(families),
        novel_types=tuple(novel),
    )


def _controls(d: Any, entry: str, errs: list[str]) -> Controls:
    _keys(d, {"first_stage", "bootstrap", "frontier_bootstrap", "cutoff_grid"}, "controls", errs)
    if not isinstance(d, dict):
        d = {}
    boot, fboot = d.get("bootstrap", 1000), d.get("frontier_bootstrap", 200)
    for k, v in (("bootstrap", boot), ("frontier_bootstrap", fboot)):
        if not _is_int(v) or v < 0:
            errs.append(f"controls.{k}: a non-negative integer")
    grid = d.get("cutoff_grid", DEFAULT_CUTOFFS)
    if (
        not isinstance(grid, list)
        or not grid
        or not all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in grid)
    ):
        errs.append("controls.cutoff_grid: a non-empty list of values in [0, 1]")
        grid = DEFAULT_CUTOFFS
    return Controls(
        first_stage=str(d.get("first_stage", entry)),
        bootstrap=int(boot) if _is_int(boot) else 0,
        frontier_bootstrap=int(fboot) if _is_int(fboot) else 0,
        cutoff_grid=tuple(float(x) for x in grid),
    )


def _routes(d: dict, kind: str, role: str, where: str, errs: list[str]) -> dict:
    """Routes as {output: target}; an annotate stage has the single output 'next'."""
    if kind == "multiclass" and role == "annotate":
        if "routes" in d:
            errs.append(f"{where}: an annotate stage cannot change the verdict; use 'next' (or role: decide)")
        if "next" not in d:
            errs.append(f"{where}: an annotate stage needs 'next' (the stage or terminal every flow goes to)")
        return {NEXT: str(d.get("next", "ATTACK"))}
    if "next" in d:
        errs.append(f"{where}: 'next' is for annotate stages; a {kind} stage with role decide uses routes")
    routes = d.get("routes")
    if not isinstance(routes, dict):
        errs.append(f"{where}: 'routes' must map each output to a stage or terminal")
        return {}
    routes = {str(k): str(v) for k, v in routes.items()}
    if kind in OUTPUTS and set(routes) != set(OUTPUTS[kind]):
        errs.append(f"{where}: routes must cover exactly {OUTPUTS[kind]} (got {sorted(routes)})")
    if kind == "multiclass" and DEFAULT not in routes:
        errs.append(f"{where}: a decide multiclass stage needs a '{DEFAULT}' route for types not listed")
    return routes


def _neighbours(cands: list[Candidate]) -> int | None:
    """Largest neighbour count among the candidates (explicit or the estimator default)."""
    best = None
    for c in cands:
        if c.external is not None:
            continue
        try:
            obj = import_class(c.cls)(**c.params)
            k = obj.get_params().get("n_neighbors") if hasattr(obj, "get_params") else None
        except Exception:  # noqa: BLE001  (reported by _check_class)
            k = None
        if isinstance(k, int):
            best = k if best is None else max(best, k)
    return best


def _stage(name: str, d: Any, errs: list[str]) -> Stage | None:
    where = f"stage {name}"
    if name in TERMINALS:
        errs.append(f"{where}: {TERMINALS} are terminal names and cannot name a stage")
    allowed = {
        "kind",
        "candidates",
        "routes",
        "decision",
        "train_on",
        "calibrate",
        "fit_on",
        "pipeline",
        "select_by",
        "min_val_rows",
        "extra_features",
        "hard",
        "next",
        "role",
        "type_objective",
        "prep_fit_on",
        "min_pool",
    }
    if not _keys(d, allowed, where, errs, required={"kind", "candidates", "decision"}):
        return None
    kind = d.get("kind")
    if kind not in ("classifier", "multiclass", "detector"):
        errs.append(f"{where}: kind {kind!r} not in classifier, multiclass, detector")
        return None
    role = d.get("role", "annotate" if kind == "multiclass" else "decide")
    if role not in ("decide", "annotate"):
        errs.append(f"{where}: role {role!r} not in decide, annotate")
        role = "decide"
    if role == "annotate" and kind != "multiclass":
        errs.append(f"{where}: only a multiclass stage can annotate; a {kind} stage decides")
        role = "decide"
    routes = _routes(d, kind, role, where, errs)

    calibrate = d.get("calibrate", "none")
    fit_on = d.get("fit_on", "fit")
    if kind in ("classifier", "multiclass"):
        if calibrate not in ("none", "sigmoid", "isotonic"):
            errs.append(f"{where}: calibrate {calibrate!r} not in none, sigmoid, isotonic")
        errs.extend(
            f"{where}: {k} applies to detectors only" for k in ("fit_on", "prep_fit_on", "min_pool") if k in d
        )
    else:
        if "calibrate" in d:
            errs.append(f"{where}: detectors are not calibrated")
        if fit_on not in ("fit", "benign", "pool"):
            errs.append(f"{where}: fit_on {fit_on!r} not in fit, benign, pool")
        if fit_on != "pool":
            errs.extend(
                f"{where}: {k} applies to detectors with fit_on: pool only"
                for k in ("prep_fit_on", "min_pool")
                if k in d
            )
    prep_fit_on = d.get("prep_fit_on", "fit")
    if prep_fit_on not in ("fit", "benign"):
        errs.append(f"{where}: prep_fit_on {prep_fit_on!r} not in fit, benign")

    decision = _decision(d.get("decision"), kind, role, routes, where=where, errs=errs)
    if fit_on == "pool" and decision.mode == "budget":
        errs.append(
            f"{where}: budget thresholds need a detector fitted once (fit_on fit or benign), not pool"
        )

    train_on = d.get("train_on", "all")
    hard = d.get("hard")
    if kind == "detector":
        if "train_on" in d or "hard" in d:
            errs.append(
                f"{where}: detectors choose their rows with fit_on (fit, benign or pool), not train_on"
            )
        train_on, hard = "all", None
    else:
        one_class = train_on == "benign" or (kind == "classifier" and train_on == "attack")
        if one_class and kind == "classifier":
            errs.append(
                f"{where}: a classifier needs benign and attack rows; train_on {train_on!r} has one class"
            )
        elif one_class:
            errs.append(f"{where}: a multiclass stage needs several types; train_on 'benign' has one")
        if train_on == "hard":
            if hard is None:
                errs.append(
                    f"{where}: train_on: hard needs a 'hard' block {{source: <stage>, band: [low, high]}}"
                )
            elif not _keys(hard, {"source", "band"}, f"{where} hard", errs, required={"source", "band"}):
                hard = None
            else:
                band = hard.get("band")
                if not (
                    isinstance(band, list)
                    and len(band) == 2
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in band)
                    and band[0] < band[1]
                ):
                    errs.append(f"{where} hard.band: two numbers [low, high] with low < high")
                hard = {
                    "source": str(hard.get("source")),
                    "band": [float(x) for x in band]
                    if isinstance(band, list) and len(band) == 2
                    else [0.0, 1.0],
                }
        elif hard is not None:
            errs.append(f"{where}: 'hard' needs train_on: hard")
            hard = None
        if not (
            one_class or train_on in ("all", "attack", "hard") or (isinstance(train_on, list) and train_on)
        ):
            errs.append(f"{where}: train_on must be all, attack, hard or a list of types")

    extra = d.get("extra_features") or []
    if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
        errs.append(f"{where}: extra_features must be a list of stage names")
        extra = []
    if kind == "detector" and fit_on == "pool" and extra:
        errs.append(
            f"{where}: a pool detector refits on raw features each batch; extra_features are not used"
        )

    type_objective = d.get("type_objective", "macro_f1")
    if type_objective not in TYPE_OBJECTIVES:
        errs.append(f"{where}: type_objective {type_objective!r} not in {TYPE_OBJECTIVES}")
    if "type_objective" in d and kind != "multiclass":
        errs.append(f"{where}: type_objective applies to multiclass stages only")

    select_by = d.get("select_by", "objective" if kind == "detector" or role == "annotate" else "auc")
    if select_by not in ("auc", "objective"):
        errs.append(f"{where}: select_by {select_by!r} not in auc, objective")
    mvr = d.get("min_val_rows", 20)
    if not _is_int(mvr) or mvr < 1:
        errs.append(f"{where}: min_val_rows must be a positive integer")
        mvr = 20

    pipeline = d.get("pipeline") or []
    if not isinstance(pipeline, list):
        errs.append(f"{where}: pipeline must be a list of steps")
        pipeline = []
    for i, step in enumerate(pipeline):
        if _keys(step, {"class", "params"}, f"{where} pipeline step {i}", errs, required={"class"}):
            _check_class(
                step["class"], step.get("params") or {}, None, f"{where} pipeline step {i}", errs, role="step"
            )

    cands_raw = d.get("candidates")
    if isinstance(cands_raw, dict):
        cands_raw = [cands_raw]
    if not isinstance(cands_raw, list) or not cands_raw:
        errs.append(f"{where}: candidates must be a non-empty list")
        cands_raw = []
    cands = []
    for i, c in enumerate(cands_raw):
        cw = f"{where} candidate {i}"
        if isinstance(c, dict) and "external" in c:
            cands.append(_external(c, i, cw, kind=kind, fit_on=fit_on, calibrate=calibrate, errs=errs))
            continue
        if not _keys(c, {"class", "params", "search", "score"}, cw, errs, required={"class"}):
            continue
        params = c.get("params") or {}
        search = c.get("search")
        score = c.get("score")
        if search is not None:
            if kind == "detector":
                errs.append(
                    f"{cw}: detectors have no labels to search on; list each setting as its own candidate"
                )
            elif _keys(search, {"space", "n_iter", "cv", "kind"}, f"{cw} search", errs, required={"space"}):
                if search.get("kind", "random") not in ("random", "grid"):
                    errs.append(f"{cw} search: kind must be random or grid")
                if not isinstance(search.get("space"), dict) or not all(
                    isinstance(v, list) and v for v in search["space"].values()
                ):
                    errs.append(f"{cw} search: space must map parameter names to non-empty lists")
                cv = search.get("cv", 3)
                if not _is_int(cv) or cv < 2:
                    errs.append(f"{cw} search: cv must be an integer of at least 2")
                n_iter = search.get("n_iter", 10)
                if not _is_int(n_iter) or n_iter < 1:
                    errs.append(f"{cw} search: n_iter must be a positive integer")
        if score is not None:
            if kind != "detector" or fit_on == "pool":
                errs.append(f"{cw}: 'score' overrides apply to detectors fitted once only")
            else:
                _keys(
                    score,
                    {"method", "higher_is_anomalous"},
                    f"{cw} score",
                    errs,
                    required={"method", "higher_is_anomalous"},
                )
        role_key = "pool" if fit_on == "pool" and kind == "detector" else kind
        _check_class(c["class"], params, search, cw, errs, role=role_key, calibrate=calibrate, score=score)
        label = f"{str(c['class']).rsplit('.', 1)[-1]}#{i}"
        cands.append(
            Candidate(cls=str(c["class"]), params=dict(params), search=search, score=score, label=label)
        )
    if pipeline and any(c.external is None and _is_custom_class(c.cls) for c in cands):
        errs.append(f"{where}: a custom adapter class does its own preprocessing; remove the pipeline steps")

    min_pool = DEFAULT_MIN_POOL
    if kind == "detector" and fit_on == "pool":
        k = _neighbours(cands)
        need = (k + 1) if k is not None else DEFAULT_MIN_POOL
        min_pool = d.get("min_pool", need)
        if not _is_int(min_pool) or min_pool < 2:
            errs.append(f"{where}: min_pool must be an integer of at least 2")
            min_pool = need
        elif k is not None and min_pool < k + 1:
            errs.append(f"{where}: min_pool {min_pool} is below the largest neighbour count + 1 ({k + 1})")
    return Stage(
        name=name,
        kind=kind,
        candidates=tuple(cands),
        routes=routes,
        decision=decision,
        train_on=train_on,
        calibrate=str(calibrate) if kind != "detector" else "none",
        fit_on=str(fit_on) if kind == "detector" else "fit",
        pipeline=tuple(pipeline),
        select_by=str(select_by),
        min_val_rows=int(mvr),
        role=role,
        extra_features=tuple(extra),
        hard=hard,
        type_objective=str(type_objective),
        prep_fit_on=str(prep_fit_on),
        min_pool=int(min_pool),
    )


def _is_custom_class(path: str) -> bool:
    try:
        return getattr(import_class(path), "idslab_contract", None) is not None
    except ImportError:
        return False


def _decision(d: Any, kind: str, role: str, routes: dict, *, where: str, errs: list[str]) -> Decision:
    outputs = OUTPUTS.get(kind, tuple(routes))
    fallback = Decision("threshold", (0.5,), outputs[1] if len(outputs) > 1 else NEXT)
    if not _keys(
        d, {"threshold", "grid", "budget", "off", "top_fraction", "confidence"}, f"{where} decision", errs
    ):
        return fallback
    allowed = {
        "classifier": ("threshold", "grid"),
        "detector": ("threshold", "grid", "budget", "top_fraction"),
        "multiclass": ("confidence",),
    }[kind]
    modes = [k for k in ("threshold", "grid", "budget", "top_fraction", "confidence") if k in d]
    if len(modes) != 1 or modes[0] not in allowed:
        errs.append(f"{where} decision: give exactly one of {allowed}")
        return fallback
    mode = modes[0]
    v = d[mode]
    values = v if isinstance(v, list) else [v]
    if not values or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in values):
        errs.append(f"{where} decision.{mode}: numbers only")
        return fallback
    if mode == "threshold" and isinstance(v, list):
        errs.append(f"{where} decision.threshold: a single number (use grid for a list chosen on VAL)")
    if mode == "grid" and not isinstance(v, list):
        errs.append(f"{where} decision.grid: a list of values")
    if kind == "classifier" and not all(0 <= x <= 1 for x in values):
        errs.append(f"{where} decision.{mode}: classifier thresholds are probabilities in [0, 1]")
    if mode in ("budget", "top_fraction") and not all(0 < x < 1 for x in values):
        errs.append(f"{where} decision.{mode}: fractions in (0, 1)")
    if mode == "confidence" and not all(0 <= x <= 1 for x in values):
        errs.append(f"{where} decision.confidence: probabilities in [0, 1]")
    off = d.get("off")
    if kind == "multiclass" and role == "annotate":
        if off is not None:
            errs.append(f"{where} decision.off: an annotate stage is off by passing flows on without a type")
        off = NEXT
    elif off is None:
        errs.append(
            f"{where} decision: 'off' is required (the output every flow takes when the stage is off; "
            f"one of {tuple(outputs)})"
        )
        off = outputs[-1] if outputs else NEXT
    elif off not in outputs:
        errs.append(f"{where} decision.off: must be one of {tuple(outputs)}")
    return Decision(
        mode=mode, values=tuple(float(x) for x in values), off=str(off), listed=isinstance(v, list)
    )


def import_class(path: str) -> Any:
    mod_name, _, cls_name = str(path).rpartition(".")
    if not mod_name:
        raise ImportError(
            f"'{path}' is not a dotted import path such as sklearn.ensemble.RandomForestClassifier"
        )
    try:
        mod = importlib.import_module(mod_name)
    except ModuleNotFoundError as e:
        top = mod_name.split(".")[0]
        hint = f" (install it with: pip install idslab[{EXTRAS[top]}])" if top in EXTRAS else ""
        raise ImportError(f"cannot import module '{mod_name}'{hint}") from e
    if not hasattr(mod, cls_name):
        raise ImportError(f"module '{mod_name}' has no attribute '{cls_name}'")
    return getattr(mod, cls_name)


def _check_class(
    path: Any,
    params: Any,
    search: Any,
    where: str,
    errs: list[str],
    *,
    role: str,
    calibrate: str = "none",
    score: Any = None,
) -> None:
    try:
        cls = import_class(str(path))
    except ImportError as e:
        errs.append(f"{where}: {e}")
        return
    if not isinstance(params, dict):
        errs.append(f"{where}: params must be a mapping")
        return
    missing = [m for m in getattr(cls, "idslab_requires", ()) if importlib.util.find_spec(m) is None]
    if missing:
        extras = ", ".join(f"idslab[{EXTRAS.get(m, m)}]" for m in missing)
        errs.append(f"{where}: {path} needs {missing} (install it with: pip install {extras})")
        return
    contract = getattr(cls, "idslab_contract", None)
    if contract is not None:
        expect = {
            "classifier": "AttackScore",
            "multiclass": "ClassProba",
            "detector": "AnomalyScore",
            "pool": "BatchAnomaly",
        }.get(role)
        if role != "step" and contract != expect:
            errs.append(f"{where}: adapter declares contract {contract!r}, this stage needs {expect!r}")
        if role in ("classifier", "multiclass") and calibrate != "none":
            errs.append(f"{where}: a custom adapter cannot be calibrated; set calibrate: none")
        if search is not None:
            errs.append(f"{where}: a custom adapter cannot be searched; list each setting as a candidate")
        return
    try:
        obj = cls(**params)
    except Exception as e:  # noqa: BLE001  (any constructor failure is a config error)
        errs.append(f"{where}: {path}({', '.join(map(str, params))}) failed: {e}")
        return
    get_params = getattr(obj, "get_params", None)
    if get_params is None:
        errs.append(
            f"{where}: {path} is not a scikit-learn style estimator (no get_params); "
            f"wrap it in an adapter class with idslab_contract"
        )
        return
    known = set(get_params())
    if isinstance(search, dict) and isinstance(search.get("space"), dict):
        bad = sorted(set(map(str, search["space"])) - known)
        if bad:
            errs.append(f"{where} search: {path} has no parameters {bad}")
    if role == "step":
        if not (hasattr(obj, "transform") or hasattr(obj, "fit_resample")):
            errs.append(f"{where}: pipeline step {path} has neither transform nor fit_resample")
    elif role in ("classifier", "multiclass"):
        if not hasattr(obj, "predict_proba") and not hasattr(obj, "decision_function"):
            errs.append(f"{where}: {path} has neither predict_proba nor decision_function")
        elif not hasattr(obj, "predict_proba") and calibrate == "none":
            errs.append(f"{where}: {path} has no predict_proba; set calibrate: sigmoid to get probabilities")
    elif role == "pool":
        if "novelty" in known and params.get("novelty") is True:
            errs.append(f"{where}: a pool detector scores the batch it was fitted on; novelty must be false")
        if not hasattr(obj, "fit"):
            errs.append(f"{where}: {path} has no fit method")
    elif role == "detector":
        if "novelty" in known and params.get("novelty") is False:
            errs.append(f"{where}: novelty must be true for a detector fitted once (fit_on fit or benign)")
        if score is None and not (
            hasattr(obj, "score_samples") or hasattr(obj, "decision_function") or "novelty" in known
        ):
            errs.append(f"{where}: {path} has no score_samples or decision_function; add a 'score' override")
        if (
            isinstance(score, dict)
            and isinstance(score.get("method"), str)
            and not hasattr(obj, score["method"])
            and "novelty" not in known
        ):
            errs.append(f"{where}: {path} has no method {score['method']!r}")


def _graph(cfg: Config, errs: list[str]) -> None:
    if cfg.entry not in cfg.stages:
        errs.append(f"entry: stage '{cfg.entry}' is not defined")
        return
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if n in TERMINALS:
            return
        if n not in cfg.stages:
            errs.append(f"stage {path[-1]}: route target '{n}' is neither a stage nor one of {TERMINALS}")
            return
        if state.get(n) == 1:
            errs.append(f"routing cycle: {' -> '.join([*path[1:], n])}")
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for t in cfg.stages[n].routes.values():
            visit(t, [*path, n])
        state[n] = 2

    visit(cfg.entry, ["(entry)"])
    unreachable = [n for n in cfg.stages if n not in state]
    if unreachable:
        errs.append(f"stages not reachable from the entry stage: {unreachable}")


def _dependencies(cfg: Config, errs: list[str]) -> None:
    """Score sources must exist, give a per-row score on every split, and form no cycle with the routes."""
    for name, st in cfg.stages.items():
        for src in cfg.sources(name):
            what = "hard.source" if st.hard and src == st.hard["source"] else "extra_features"
            if src == name:
                errs.append(f"stage {name}: {what} names the stage itself")
            elif src not in cfg.stages:
                errs.append(f"stage {name}: {what} '{src}' is not a stage")
            elif cfg.stages[src].transductive:
                errs.append(
                    f"stage {name}: {what} '{src}' is a pool detector, which scores only the flows reaching "
                    "it; a score source must score every row"
                )
            elif what == "hard.source" and cfg.stages[src].kind == "multiclass":
                errs.append(
                    f"stage {name}: hard.source '{src}' must give one score per row (not a multiclass stage)"
                )
    if errs:
        return
    edges = cfg.edges()
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if n not in edges:
            return
        if state.get(n) == 1:
            errs.append(f"routes and score dependencies form a cycle: {' -> '.join([*path, n])}")
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for t in edges[n].values():
            visit(t, [*path, n])
        state[n] = 2

    for n in cfg.stages:
        visit(n, [])


def check_against_data(cfg: Config, types: set[str] | None, raw_types: set[str] | None = None) -> None:
    """Checks that need the dataset's type values (mapped, and raw for the modes); raises ConfigError."""
    errs = []
    proto = cfg.protocol
    if proto.mode == "heldout_family" and proto.families and types is not None:
        known = (raw_types or set()) if proto.family_level == "raw" else types
        bad = sorted(set(proto.families) - known)
        if bad:
            errs.append(
                f"protocol.families {bad} are not {proto.family_level} types in the data ({sorted(known)})"
            )
        if cfg.dataset.normal_type in proto.families:
            errs.append("protocol.families: the normal type cannot be held out")
    if proto.mode == "natural_novelty" and raw_types is not None:
        bad = sorted(set(proto.novel_types) - raw_types)
        if bad:
            errs.append(f"protocol.novel_types {bad} are not raw types in the data")
    for name, st in cfg.stages.items():
        if st.kind == "multiclass" and types is None:
            errs.append(f"stage {name}: a multiclass stage needs dataset.type")
        if types is None:
            continue
        if st.kind == "multiclass" and st.role == "decide":
            bad = sorted(set(st.routes) - types - {UNCERTAIN, DEFAULT})
            if bad:
                errs.append(f"stage {name}: route names {bad} are not types in the data ({sorted(types)})")
            if st.decision.off not in st.routes:
                errs.append(f"stage {name}: decision.off '{st.decision.off}' is not one of its routes")
        if isinstance(st.train_on, list):
            bad = sorted(set(st.train_on) - types)
            if bad:
                errs.append(f"stage {name}: train_on types {bad} are not in the data ({sorted(types)})")
    if errs:
        raise ConfigError("\n".join(errs))


def _semantics(cfg: Config, errs: list[str]) -> None:
    fs = cfg.controls.first_stage
    if fs not in cfg.stages or cfg.stages[fs].kind != "classifier":
        errs.append(
            f"controls.first_stage '{fs}' must be a classifier stage (the controls compare against it)"
        )
    ds = cfg.dataset
    if cfg.protocol.mode != "standard" and ds.type is None:
        errs.append(f"protocol.mode {cfg.protocol.mode} needs dataset.type")
    for name, st in cfg.stages.items():
        if st.kind == "multiclass" and ds.type is None:
            errs.append(f"stage {name}: a multiclass stage needs dataset.type")
        if isinstance(st.train_on, list):
            if ds.type is None:
                errs.append(f"stage {name}: train_on as a list of types needs dataset.type")
            elif st.kind == "classifier" and (
                ds.normal_type not in st.train_on or not [t for t in st.train_on if t != ds.normal_type]
            ):
                errs.append(
                    f"stage {name}: a classifier's train_on list must include the normal type "
                    f"'{ds.normal_type}' and at least one attack type"
                )
