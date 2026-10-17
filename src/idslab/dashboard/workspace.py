# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""The workspace folder the dashboard serves: path checks, config and run discovery.

Every path the page sends is relative to the workspace root and must resolve inside it. Hidden
path parts are refused, so ``.git``, ``.venv`` and dot files are never read or written.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any

from idslab.config import MODES, Config, ConfigError, from_dict, parse_yaml

SKIP_DIRS = {"__pycache__", "node_modules", "venv", "site-packages"}
MAX_DEPTH = 6
MAX_CONFIG_BYTES = 1_000_000
MAX_ITEMS = 500
_SEED = re.compile(r"^seed_(-?\d+)$")


class PathError(ValueError):
    """A path outside the workspace, hidden, or of the wrong kind."""


def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Workspace:
    def __init__(self, root: str):
        self.root = os.path.realpath(root)
        if not os.path.isdir(self.root):
            raise PathError(f"workspace {root} is not a folder")
        self._loaded: dict[str, tuple[tuple[float, int], dict]] = {}  # config path: (stamp, record)

    # ---------- paths ----------

    def resolve(self, rel: str | None, *, kind: str | None = None) -> str:
        """Absolute path of ``rel`` inside the workspace; ``kind`` is "file" or "dir" to require one."""
        rel = (rel or "").strip().replace("\\", "/")
        if os.path.isabs(rel) or rel.startswith("~"):
            raise PathError(f"{rel}: give a path relative to the workspace")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p.startswith(".") for p in parts):
            raise PathError(f"{rel}: hidden or parent path parts are not allowed")
        if any(":" in p for p in parts):  # Windows drive-relative paths (C:x) and alternate data streams
            raise PathError(f"{rel}: a colon is not allowed in a workspace path")
        path = os.path.realpath(os.path.join(self.root, *parts))
        try:
            inside = os.path.commonpath([path, self.root]) == self.root
        except ValueError:  # Windows: a path on another drive
            inside = False
        if not inside:
            raise PathError(f"{rel}: outside the workspace")
        if kind == "file" and not os.path.isfile(path):
            raise PathError(f"{rel or '.'}: no such file")
        if kind == "dir" and not os.path.isdir(path):
            raise PathError(f"{rel or '.'}: no such folder")
        return path

    def rel(self, path: str) -> str:
        r = os.path.relpath(path, self.root).replace(os.sep, "/")
        return "" if r == "." else r

    def _walk(self):
        """os.walk over the workspace without hidden or tool folders, at most MAX_DEPTH deep."""
        base = self.root.count(os.sep)
        for d, dirs, files in os.walk(self.root):
            depth = d.count(os.sep) - base
            dirs[:] = sorted(
                x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS and depth < MAX_DEPTH
            )
            yield d, dirs, sorted(f for f in files if not f.startswith("."))

    # ---------- configs ----------

    def configs(self) -> list[dict]:
        """YAML files that look like IDSLab configs (a mapping with entry and stages)."""
        out = []
        for d, dirs, files in self._walk():
            if _is_mode_dir(d, dirs):
                dirs[:] = []  # run output: no configs inside
                continue
            for f in files:
                if not f.endswith((".yaml", ".yml")):
                    continue
                path = os.path.join(d, f)
                info = config_info(path)
                if info is not None:
                    out.append({"path": self.rel(path), **info})
                    if len(out) >= MAX_ITEMS:
                        return out
        return out

    def sweeps(self) -> list[dict]:
        """YAML files that look like sweep files (a mapping with base and variants or grid)."""
        out = []
        for d, dirs, files in self._walk():
            if _is_mode_dir(d, dirs):
                dirs[:] = []
                continue
            for f in files:
                if not f.endswith((".yaml", ".yml")):
                    continue
                path = os.path.join(d, f)
                try:
                    if os.path.getsize(path) > MAX_CONFIG_BYTES:
                        continue
                    with open(path, encoding="utf-8") as fh:
                        raw = parse_yaml(fh.read())
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
                if isinstance(raw, dict) and "base" in raw and ("variants" in raw or "grid" in raw):
                    n = len(raw.get("variants") or {}) if "variants" in raw else None
                    out.append(
                        {
                            "path": self.rel(path),
                            "name": str(raw.get("name", "")),
                            "base": str(raw.get("base")),
                            "variants": n,
                            "mtime": os.path.getmtime(path),
                        }
                    )
        return out

    def config_text(self, rel: str) -> str:
        path = self.resolve(rel, kind="file")
        if os.path.getsize(path) > MAX_CONFIG_BYTES:
            raise PathError(f"{rel}: larger than {MAX_CONFIG_BYTES} bytes")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def save_config(self, rel: str, text: str, *, overwrite: bool) -> str:
        if not rel.endswith((".yaml", ".yml")):
            raise PathError(f"{rel}: configs are saved as .yaml or .yml")
        if len(text.encode()) > MAX_CONFIG_BYTES:
            raise PathError(f"config larger than {MAX_CONFIG_BYTES} bytes")
        path = self.resolve(rel)
        if os.path.isdir(path):
            raise PathError(f"{rel}: is a folder")
        if os.path.exists(path) and not overwrite:
            raise PathError(f"{rel}: already exists")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return self.rel(path)

    # ---------- runs ----------

    def experiments(self) -> list[dict]:
        """Run outputs: folders holding ``<mode>/seed_<s>``, found anywhere in the workspace."""
        found = []
        for d, dirs, _ in self._walk():
            if _is_mode_dir(d, dirs):
                dirs[:] = []
                exp = os.path.dirname(d)
                if exp not in found:
                    found.append(exp)
                    if len(found) >= MAX_ITEMS:
                        break
        out = [self._experiment_row(e) for e in found]
        return sorted(out, key=lambda r: r["updated"] or 0, reverse=True)

    def _experiment_row(self, exp: str) -> dict:
        units = list(_units(exp))
        report = _read_json(os.path.join(exp, "report.json"))
        head = None
        if isinstance(report, dict):
            goal = report.get("objective")
            st = report.get("systems_test") or {}

            def mean(system: str) -> float | None:
                v = (st.get(system) or {}).get(goal) or {}
                return v.get("mean")

            head = {
                "objective": goal,
                "mode": report.get("mode"),
                "cascade": mean("cascade"),
                "B1_first_stage": mean("B1_first_stage"),
                "B2_first_stage_val_cutoff": mean("B2_first_stage_val_cutoff"),
                "n_units": len(report.get("units") or []),
                "left_out": len(report.get("seeds_left_out") or []),
                "idslab_version": report.get("idslab_version"),
            }
        stamps = [u["finished"] for u in units if u["finished"]]
        files = [os.path.join(exp, n) for n in ("report.json", "report.html")]
        mtimes = [os.path.getmtime(p) for p in files if os.path.exists(p)]
        report_files = {
            ext: os.path.exists(os.path.join(exp, f"report.{ext}")) for ext in ("md", "json", "html")
        }
        report_json = os.path.join(exp, "report.json")
        report_mtime = os.path.getmtime(report_json) if report_files["json"] else None
        done_mtimes = [u["done_mtime"] for u in units if u["done_mtime"] is not None]
        in_report = len(report.get("units") or []) if isinstance(report, dict) else 0
        n_done = sum(u["done"] for u in units)
        return {
            "path": self.rel(exp),
            "name": os.path.basename(exp),
            "modes": sorted({u["mode"] for u in units}),
            "units_done": sum(u["done"] for u in units),
            "units_total": len(units),
            "last_finished": max(stamps) if stamps else None,
            "updated": max([*mtimes, *(u["mtime"] for u in units)], default=None),
            "has_report": os.path.exists(os.path.join(exp, "report.html")),
            "report": head,
            "report_files": report_files,
            "report_mtime": report_mtime,
            "report_units": in_report,
            # finished units the report does not include yet, or units finished after it was written
            "report_behind": bool(
                report_mtime is not None
                and (in_report < n_done or any(t > report_mtime + 1 for t in done_mtimes))
            ),
            "config_fingerprints": sorted({u["config_fp"] for u in units if u["config_fp"]}),
        }

    def experiment(self, rel: str) -> dict:
        exp = self.resolve(rel, kind="dir")
        units = list(_units(exp))
        if not units:
            raise PathError(f"{rel}: no run output here")
        row = self._experiment_row(exp)
        report = _read_json(os.path.join(exp, "report.json"))
        configs = [c for c in self.configs() if c.get("name") == row["name"]]
        return {
            **row,
            "units": [{**u, "path": self.rel(u.pop("dir"))} for u in units],
            "report_full": report if isinstance(report, dict) else None,
            "configs": [c["path"] for c in configs],
            "out_root": self.rel(os.path.dirname(exp)),
        }

    def unit(self, rel: str) -> dict:
        d = self.resolve(rel, kind="dir")
        summary = _read_json(os.path.join(d, "summary.json"))
        if not isinstance(summary, dict):
            raise PathError(f"{rel}: no summary.json")
        return {"path": self.rel(d), "summary": summary, "done": _read_json(os.path.join(d, "DONE"))}

    # ---------- experiments: a config and the outputs it wrote ----------

    def config_record(self, rel: str) -> dict:
        """A config with what the experiment list needs: name, validity, fingerprint, data paths.
        Loading imports the model classes, so records are kept until the file changes."""
        path = self.resolve(rel, kind="file")
        st = os.stat(path)
        stamp = (st.st_mtime, st.st_size)
        hit = self._loaded.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
        rec: dict[str, Any] = {"path": self.rel(path), "mtime": st.st_mtime, "fingerprint": None}
        try:
            text = self.config_text(rel)
            raw = parse_yaml(text)
            rec["name"] = str(raw.get("name", "")) if isinstance(raw, dict) else ""
            cfg = from_dict(raw, base_dir=os.path.dirname(path))
        except (ConfigError, ImportError, ValueError) as e:
            rec.update(valid=False, error=str(e), data=[])
        else:
            rec.update(
                valid=True,
                error=None,
                name=cfg.name,
                fingerprint=cfg.fingerprint(),
                stages=len(cfg.stages),
                mode=cfg.protocol.mode,
                seeds=list(cfg.protocol.seeds),
                data=[
                    {
                        "role": role,
                        "path": f,
                        "file": self._rel_or_none(cfg.path(f)),
                        "exists": os.path.isfile(cfg.path(f)),
                    }
                    for role, f in (("train", cfg.dataset.train), ("test", cfg.dataset.test))
                    if f is not None
                ],
            )
        self._loaded[path] = (stamp, rec)
        return rec

    def _rel_or_none(self, path: str) -> str | None:
        real = os.path.realpath(path)
        try:
            inside = os.path.commonpath([real, self.root]) == self.root
        except ValueError:
            inside = False
        return self.rel(real) if inside else None

    def experiment_index(self) -> list[dict]:
        """Every config, with the run outputs it owns, and every run output no config owns.

        An output belongs to a config when the names match. When several configs share the
        name, the config fingerprints recorded by the output's finished units must match exactly
        one of them; otherwise the output is listed on its own with the reason, never guessed."""
        configs = [self.config_record(c["path"]) for c in self.configs()]
        outputs = self.experiments()
        by_name: dict[str, list[dict]] = {}
        for c in configs:
            by_name.setdefault(c.get("name") or "", []).append(c)
        owned: dict[str, list[dict]] = {c["path"]: [] for c in configs}
        alone = []
        for o in outputs:
            cands = by_name.get(o["name"], [])
            owner = cands[0] if len(cands) == 1 else None
            if len(cands) > 1:
                match = [c for c in cands if c["fingerprint"] in o["config_fingerprints"]]
                owner = match[0] if len(match) == 1 else None
            if owner is not None:
                owned[owner["path"]].append(o)
                continue
            if cands:
                reason = (
                    f"configs {', '.join(c['path'] for c in cands)} share the name {o['name']}, and the "
                    "finished units do not match exactly one of them"
                )
            else:
                reason = f"no config in the workspace is named {o['name']}"
            alone.append(
                {
                    "key": "o:" + o["path"],
                    "kind": "output",
                    "name": o["name"],
                    "config": None,
                    "record": None,
                    "outputs": [o],
                    "reason": reason,
                    "candidates": [c["path"] for c in cands],
                }
            )
        out = []
        for c in configs:
            outs = owned[c["path"]]
            out.append(
                {
                    "key": "c:" + c["path"],
                    "kind": "config",
                    "name": c.get("name") or "",
                    "config": c["path"],
                    "record": c,
                    "outputs": outs,
                    "reason": None,
                    "candidates": [],
                }
            )
        out += alone
        for e in out:
            e["updated"] = max(
                [*(o["updated"] or 0 for o in e["outputs"]), (e["record"] or {}).get("mtime") or 0]
            )
            e["state"] = _state(e)
            e["changed_since_run"] = bool(
                e["record"]
                and e["record"]["fingerprint"]
                and e["outputs"]
                and e["record"]["fingerprint"] not in e["outputs"][0]["config_fingerprints"]
                and e["outputs"][0]["config_fingerprints"]
            )
        return sorted(out, key=lambda e: e["updated"], reverse=True)

    def history(self, limit: int = 200) -> list[dict]:
        """Past activity read from files: finished units, reports and comparisons, newest first.
        This is what the workspace holds, not a log of dashboard jobs."""
        events = []
        for d, _dirs, files in self._walk():
            if "DONE" in files:
                done = _read_json(os.path.join(d, "DONE"))
                when = _epoch(done.get("finished")) if isinstance(done, dict) else None
                parts = self.rel(d).split("/")
                fam = parts[-1][4:] if parts[-1].startswith("fam_") else None
                seed_part = parts[-2] if fam else parts[-1]
                exp = "/".join(parts[: -3 if fam else -2])
                events.append(
                    {
                        "time": when or os.path.getmtime(os.path.join(d, "DONE")),
                        "kind": "unit",
                        "path": self.rel(d),
                        "experiment": exp,
                        "label": seed_part.replace("_", " ") + (f", held out {fam}" if fam else ""),
                    }
                )
            for name, kind in (("report.json", "report"), ("compare.json", "comparison")):
                if name in files:
                    events.append(
                        {
                            "time": os.path.getmtime(os.path.join(d, name)),
                            "kind": kind,
                            "path": self.rel(d),
                            "experiment": self.rel(d),
                            "label": kind,
                        }
                    )
        events.sort(key=lambda e: e["time"], reverse=True)
        return events[:limit]

    def datasets(self) -> list[dict]:
        """Data files with their size and the configs that read them."""
        users: dict[str, list[str]] = {}
        for c in self.configs():
            rec = self.config_record(c["path"])
            for dd in rec.get("data") or []:
                if dd.get("file"):
                    users.setdefault(dd["file"], []).append(c["path"])
        out = []
        for rel in self.data_files():
            st = os.stat(self.resolve(rel))
            out.append({"path": rel, "size": st.st_size, "mtime": st.st_mtime, "used_by": users.get(rel, [])})
        return out

    # ---------- data files ----------

    def data_files(self) -> list[str]:
        """Parquet and CSV files in the workspace, outside run outputs."""
        out = []
        for d, dirs, files in self._walk():
            if _is_mode_dir(d, dirs):
                dirs[:] = []
                continue
            out += [self.rel(os.path.join(d, f)) for f in files if f.lower().endswith(DATA_SUFFIXES)]
            if len(out) >= MAX_ITEMS:
                break
        return out[:MAX_ITEMS]

    def layout(self, config_rel: str) -> dict:
        path = self.resolve(config_rel) + ".layout.json"
        data = _read_json(path) if os.path.isfile(path) else None
        return data if isinstance(data, dict) else {}

    def save_layout(self, config_rel: str, positions: dict) -> None:
        if not config_rel.endswith((".yaml", ".yml")):
            raise PathError(f"{config_rel}: layouts belong to .yaml or .yml configs")
        clean = {
            str(k): [float(v[0]), float(v[1])]
            for k, v in positions.items()
            if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)
        }
        path = self.resolve(config_rel) + ".layout.json"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"positions": clean}, f, indent=2)

    # ---------- files ----------

    def listdir(self, rel: str) -> dict:
        d = self.resolve(rel, kind="dir")
        entries = []
        for name in sorted(os.listdir(d)):
            if name.startswith(".") or name in SKIP_DIRS:
                continue
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            entries.append({"name": name, "dir": os.path.isdir(p), "size": st.st_size, "mtime": st.st_mtime})
            if len(entries) >= MAX_ITEMS:
                break
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return {"path": self.rel(d), "entries": entries}


def _epoch(stamp: Any) -> float | None:
    try:
        return datetime.datetime.fromisoformat(str(stamp)).timestamp()
    except ValueError:
        return None


def _state(e: dict) -> str:
    """Where an experiment stands, for the next action the page offers. Finished results come
    before a missing data file, so moving the data never hides them."""
    rec = e["record"]
    outs = e["outputs"]
    if rec is not None and not rec["valid"]:
        return "invalid"
    if outs:
        o = outs[0]
        return "completed" if o["has_report"] and o["units_done"] == o["units_total"] else "partial"
    if rec is not None and any(not d["exists"] for d in rec["data"]):
        return "needs_data"
    return "draft"


def _is_mode_dir(d: str, dirs: list[str]) -> bool:
    return os.path.basename(d) in MODES and any(_SEED.match(x) for x in dirs)


def _units(exp: str):
    """Each seed (and held-out family) folder under the mode folders of one experiment."""
    for mode in MODES:
        mdir = os.path.join(exp, mode)
        if not os.path.isdir(mdir):
            continue
        seeds = [(int(m.group(1)), x) for x in os.listdir(mdir) if (m := _SEED.match(x))]
        for seed, name in sorted(seeds):
            sdir = os.path.join(mdir, name)
            fams = sorted(x for x in os.listdir(sdir) if x.startswith("fam_"))
            for d, fam in [(os.path.join(sdir, f), f[4:]) for f in fams] or [(sdir, None)]:
                yield _unit_row(d, mode, seed, fam)


def _unit_row(d: str, mode: str, seed: int, family: str | None) -> dict:
    done = _read_json(os.path.join(d, "DONE"))
    summary_path = os.path.join(d, "summary.json")
    row = {
        "dir": d,
        "mode": mode,
        "seed": seed,
        "family": family,
        "done": isinstance(done, dict),
        "finished": done.get("finished") if isinstance(done, dict) else None,
        "done_mtime": os.path.getmtime(os.path.join(d, "DONE")) if isinstance(done, dict) else None,
        "config_fp": ((done.get("fingerprints") or {}).get("config") if isinstance(done, dict) else None),
        "mtime": os.path.getmtime(d),
        "runtime_seconds": None,
        "objective": None,
        "cascade": None,
        "B1_first_stage": None,
    }
    summary = _read_json(summary_path) if os.path.exists(summary_path) else None
    if isinstance(summary, dict):
        ctl = summary.get("controls") or {}
        goal = ctl.get("objective")
        st = ctl.get("systems_test") or {}
        row.update(
            runtime_seconds=summary.get("runtime_seconds"),
            objective=goal,
            cascade=(st.get("cascade") or {}).get(goal),
            B1_first_stage=(st.get("B1_first_stage") or {}).get(goal),
        )
    return row


def config_info(path: str) -> dict | None:
    """Name and stage count of a YAML file if it looks like an IDSLab config, else None."""
    try:
        if os.path.getsize(path) > MAX_CONFIG_BYTES:
            return None
        with open(path, encoding="utf-8") as f:
            raw = parse_yaml(f.read())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict) or "stages" not in raw or "entry" not in raw:
        return None
    stages = raw.get("stages")
    proto = raw.get("protocol")
    if not isinstance(proto, dict):
        proto = {}
    return {
        "name": str(raw.get("name", "")),
        "stages": len(stages) if isinstance(stages, dict) else 0,
        "mode": str(proto.get("mode", "standard")),
        "mtime": os.path.getmtime(path),
    }


SAMPLE_ROWS = 5000
DATA_SUFFIXES = (".parquet", ".pq", ".csv", ".csv.gz", ".csv.zip", ".csv.bz2", ".csv.xz")
LABEL_NAMES = ("label", "is_attack", "attack", "malicious", "y", "target", "class")
TYPE_NAMES = ("attack_cat", "attack_type", "type", "category", "label_type", "traffic_type")
NORMAL_VALUES = ("normal", "benign", "background", "legitimate")


def read_sample(path: str, rows: int = SAMPLE_ROWS):
    """The first rows of a data file and its total row count (Parquet from metadata, CSV unknown)."""
    import pandas as pd  # noqa: PLC0415  (only the builder needs it)

    low = path.lower()
    if low.endswith((".parquet", ".pq")):
        import pyarrow.parquet as pq  # noqa: PLC0415

        f = pq.ParquetFile(path)
        batch = next(f.iter_batches(batch_size=rows), None)
        frame = batch.to_pandas() if batch is not None else f.schema_arrow.empty_table().to_pandas()
        return frame, f.metadata.num_rows
    return pd.read_csv(path, nrows=rows, low_memory=False), None


def inspect_data(path: str) -> dict:
    """Columns of a data file with suggestions for the dataset block: label, type, normal value,
    categorical columns. Reads only a sample."""
    import pandas as pd  # noqa: PLC0415

    frame, total = read_sample(path)
    cols = []
    for name in frame.columns:
        col = frame[name]
        numeric = pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col)
        values = col.dropna().unique()
        cols.append(
            {
                "name": str(name),
                "dtype": str(col.dtype),
                "numeric": bool(numeric),
                "distinct": len(values),
                "missing": int(col.isna().sum()),
                "examples": [str(v) for v in values[:6]],
            }
        )

    def binary(c: dict) -> bool:
        return c["distinct"] <= 2 and set(c["examples"]) <= {"0", "1", "0.0", "1.0", "True", "False"}

    lower = {c["name"].lower(): c for c in cols}
    label = next((lower[n]["name"] for n in LABEL_NAMES if n in lower and binary(lower[n])), None)
    label = label or next((c["name"] for c in cols if binary(c) and c["name"].lower() in LABEL_NAMES), None)
    typ = next(
        (
            lower[n]["name"]
            for n in TYPE_NAMES
            if n in lower and not lower[n]["numeric"] and lower[n]["distinct"] > 1
        ),
        None,
    )
    if typ is None:  # a text column with a normal-looking value and a handful of classes
        typ = next(
            (
                c["name"]
                for c in cols
                if not c["numeric"]
                and 2 < c["distinct"] <= 60
                and any(str(v).lower() in NORMAL_VALUES for v in frame[c["name"]].dropna().unique())
            ),
            None,
        )
    normal = None
    if typ is not None:
        vals = [str(v) for v in frame[typ].dropna().unique()]
        normal = next((v for v in vals if v.lower() in NORMAL_VALUES), None)
    categorical = [c["name"] for c in cols if not c["numeric"] and c["name"] not in (label, typ)]
    return {
        "rows_in_sample": len(frame),
        "rows": total,
        "columns": cols,
        "suggest": {"label": label, "type": typ, "normal_type": normal, "categorical": categorical},
    }


def describe(cfg: Config) -> dict:
    """What ``idslab validate`` prints, as data, plus the routes for the cascade preview."""
    p = cfg.protocol
    return {
        **cfg.outline(),
        "protocol": {
            "mode": p.mode,
            "seeds": list(p.seeds),
            "objective": p.objective,
            "search": p.search,
            "val_fraction": p.val_fraction,
            "cal_fraction": p.cal_fraction,
        },
        "data": [
            {"role": role, "path": f, "exists": os.path.isfile(cfg.path(f))}
            for role, f in (("train", cfg.dataset.train), ("test", cfg.dataset.test))
            if f is not None
        ],
        "fingerprint": cfg.fingerprint(),
    }


__all__ = ["ConfigError", "PathError", "Workspace", "config_info", "describe", "inspect_data"]
