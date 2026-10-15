# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Load and validate a cascade config (DESIGN.md sections 2 to 5).

Every check that can run without data runs here, so a wrong config fails before
any file is read: unknown keys, broken routing graph, missing ``off`` routes,
classes that do not import, parameters the estimator does not have, and
settings that do not fit together.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

TERMINALS = ("BENIGN", "ATTACK")
OUTPUTS = {"classifier": ("attack", "benign"), "detector": ("anomalous", "normal")}
OBJECTIVES = ("accuracy", "mcc", "f1", "balanced_accuracy")
EXTRAS = {"xgboost": "xgboost", "lightgbm": "lightgbm", "pyod": "pyod", "imblearn": "imbalance"}
DEFAULT_CUTOFFS = [round(0.05 + 0.025 * i, 3) for i in range(37)]

LATER = {  # feature -> phase that adds it (DESIGN.md section 10)
    "multiclass stages": 2,
    "extra_features": 2,
    "hard": 2,
    "next": 2,
    "role": 2,
    "type_objective": 2,
    "fit_on: pool": 2,
    "prep_fit_on": 2,
    "min_pool": 2,
    "top_fraction": 2,
    "confidence": 2,
    "mode heldout_family": 3,
    "mode natural_novelty": 3,
    "family_level": 3,
    "novel_types": 3,
    "external scores": 4,
    "search: joint": 4,
}


class ConfigError(ValueError):
    """The config is invalid; the message lists every problem found."""


@dataclass(frozen=True)
class Candidate:
    cls: str
    params: dict
    search: dict | None
    score: dict | None
    label: str


@dataclass(frozen=True)
class Decision:
    mode: str  # threshold | grid | budget
    values: tuple[float, ...]
    off: str

    @property
    def tunable(self) -> bool:
        return self.mode == "grid" or (self.mode == "budget" and len(self.values) > 1)


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
        """Topological order of the routing graph from the entry stage; ties keep config order."""
        return topo_order(self.entry, {n: s.routes for n, s in self.stages.items()}, list(self.stages))

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


def parse_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)  # noqa: S506  (_Loader is a SafeLoader subclass)


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
    _semantics(cfg, errs)
    if errs:
        raise ConfigError("\n".join(errs))
    return cfg


def _is_int(v: Any) -> bool:
    """An integer that is not a boolean (YAML true would otherwise pass as 1)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _later(what: str, errs: list[str], where: str) -> None:
    errs.append(f"{where}: {what} is planned for phase {LATER[what]} and is not available in this version")


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
        "novel_types",
    }
    _keys(d, allowed, "protocol", errs)
    if not isinstance(d, dict):
        d = {}
    for k in ("family_level", "novel_types"):
        if k in d:
            _later(k, errs, "protocol")
    if d.get("search") not in (None, "greedy"):
        _later("search: joint", errs, "protocol")
    mode = d.get("mode", "standard")
    if mode in ("heldout_family", "natural_novelty"):
        _later(f"mode {mode}", errs, "protocol")
    elif mode != "standard":
        errs.append(f"protocol.mode: {mode} is not a known mode")
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
    for k in ("extra_features", "hard", "next", "role", "type_objective", "prep_fit_on", "min_pool"):
        if k in d:
            _later(k, errs, where)
    kind = d.get("kind")
    if kind == "multiclass":
        _later("multiclass stages", errs, where)
        return None
    if kind not in OUTPUTS:
        errs.append(f"{where}: kind {kind!r} not in {sorted(OUTPUTS)}")
        return None
    routes = d.get("routes")
    if not isinstance(routes, dict):
        errs.append(f"{where}: 'routes' must map each output {OUTPUTS[kind]} to a stage or terminal")
        routes = {}
    routes = {str(k): str(v) for k, v in routes.items()}
    if set(routes) != set(OUTPUTS[kind]):
        errs.append(f"{where}: routes must cover exactly {OUTPUTS[kind]} (got {sorted(routes)})")

    calibrate = d.get("calibrate", "none")
    fit_on = d.get("fit_on", "fit")
    if kind == "classifier":
        if calibrate not in ("none", "sigmoid", "isotonic"):
            errs.append(f"{where}: calibrate {calibrate!r} not in none, sigmoid, isotonic")
        if "fit_on" in d:
            errs.append(f"{where}: fit_on applies to detectors only")
    else:
        if "calibrate" in d:
            errs.append(f"{where}: detectors are not calibrated")
        if fit_on == "pool":
            _later("fit_on: pool", errs, where)
        elif fit_on not in ("fit", "benign"):
            errs.append(f"{where}: fit_on {fit_on!r} not in fit, benign")

    decision = _decision(d.get("decision"), kind, where, errs)
    train_on = d.get("train_on", "all")
    if kind == "classifier" and train_on in ("benign", "attack"):
        errs.append(
            f"{where}: a classifier needs benign and attack rows; train_on {train_on!r} has one class"
        )
    if not (train_on in ("all", "benign", "attack") or (isinstance(train_on, list) and train_on)):
        if train_on == "hard":
            _later("hard", errs, where)
        else:
            errs.append(f"{where}: train_on must be all, benign, attack or a list of types")
    if kind == "detector" and "train_on" in d:
        errs.append(f"{where}: detectors choose their rows with fit_on (fit or benign), not train_on")

    select_by = d.get("select_by", "auc" if kind == "classifier" else "objective")
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
            if kind != "detector":
                errs.append(f"{cw}: 'score' overrides apply to detectors only")
            else:
                _keys(
                    score,
                    {"method", "higher_is_anomalous"},
                    f"{cw} score",
                    errs,
                    required={"method", "higher_is_anomalous"},
                )
        _check_class(c["class"], params, search, cw, errs, role=kind, calibrate=calibrate, score=score)
        label = f"{str(c['class']).rsplit('.', 1)[-1]}#{i}"
        cands.append(
            Candidate(cls=str(c["class"]), params=dict(params), search=search, score=score, label=label)
        )
    if pipeline and any(_is_custom_class(c.cls) for c in cands):
        errs.append(f"{where}: a custom adapter class does its own preprocessing; remove the pipeline steps")
    return Stage(
        name=name,
        kind=kind,
        candidates=tuple(cands),
        routes=routes,
        decision=decision,
        train_on=train_on,
        calibrate=str(calibrate),
        fit_on=str(fit_on),
        pipeline=tuple(pipeline),
        select_by=str(select_by),
        min_val_rows=int(mvr),
    )


def _is_custom_class(path: str) -> bool:
    try:
        return getattr(import_class(path), "idslab_contract", None) is not None
    except ImportError:
        return False


def _decision(d: Any, kind: str, where: str, errs: list[str]) -> Decision:
    fallback = Decision("threshold", (0.5,), OUTPUTS[kind][1])
    if not _keys(
        d, {"threshold", "grid", "budget", "off", "top_fraction", "confidence"}, f"{where} decision", errs
    ):
        return fallback
    for k in ("top_fraction", "confidence"):
        if k in d:
            _later(k, errs, f"{where} decision")
    modes = [k for k in ("threshold", "grid", "budget") if k in d]
    allowed = ("threshold", "grid") if kind == "classifier" else ("threshold", "grid", "budget")
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
    if mode == "budget" and not all(0 < x < 1 for x in values):
        errs.append(f"{where} decision.budget: alarm budgets are fractions in (0, 1)")
    off = d.get("off")
    if off is None:
        errs.append(
            f"{where} decision: 'off' is required (the output every flow takes when the stage is off; "
            f"one of {OUTPUTS[kind]})"
        )
        off = OUTPUTS[kind][1]
    elif off not in OUTPUTS[kind]:
        errs.append(f"{where} decision.off: must be one of {OUTPUTS[kind]}")
    return Decision(mode=mode, values=tuple(float(x) for x in values), off=str(off))


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
    contract = getattr(cls, "idslab_contract", None)
    if contract is not None:
        expect = {"classifier": "AttackScore", "detector": "AnomalyScore"}.get(role)
        if role != "step" and contract != expect:
            errs.append(f"{where}: adapter declares contract {contract!r}, a {role} stage needs {expect!r}")
        if role == "classifier" and calibrate != "none":
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
    elif role == "classifier":
        if not hasattr(obj, "predict_proba") and not hasattr(obj, "decision_function"):
            errs.append(f"{where}: {path} has neither predict_proba nor decision_function")
        elif not hasattr(obj, "predict_proba") and calibrate == "none":
            errs.append(
                f"{where}: {path} has no predict_proba; set calibrate: sigmoid so the stage outputs P(attack)"
            )
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


def _semantics(cfg: Config, errs: list[str]) -> None:
    fs = cfg.controls.first_stage
    if fs not in cfg.stages or cfg.stages[fs].kind != "classifier":
        errs.append(
            f"controls.first_stage '{fs}' must be a classifier stage (the controls compare against it)"
        )
    ds = cfg.dataset
    for name, st in cfg.stages.items():
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
