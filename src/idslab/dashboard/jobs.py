# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""CLI commands started from the dashboard, one at a time, as ``python -m idslab.cli`` subprocesses.

The page sends a command name and options; ``build_command`` checks them the way the CLI parser
would and turns them into an argv list. Running the real CLI keeps one code path: a run started in
the browser writes the same files, and resumes the same way, as one started in a shell.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any

from idslab.config import load
from idslab.dashboard.workspace import PathError, Workspace

LOG_LINES = 5000
COMMANDS = ("run", "report", "export-splits", "example", "compare", "sweep")
DEFAULT_OUT = {
    "run": "runs",
    "report": "runs",
    "export-splits": "splits",
    "compare": "comparisons",
    "sweep": "runs",
}


_STAMP = re.compile(r"^\d\d:\d\d:\d\d ")
_UNITS = re.compile(r"^(\d+) units? to run: ")
_VARIANT = re.compile(r"^sweep \S+: variant (\d+) of (\d+), (\S+)")
_UNIT_LINE = re.compile(r"^(seed -?\d+(?: family .+?)?): (.+)$")


def progress(lines: list[str]) -> dict:
    """What a job's own log says about its progress: the phase, the current unit and how many
    units are finished. Nothing is estimated; a count that the log does not give stays None."""
    out: dict[str, Any] = {
        "phase": None,
        "unit": None,
        "units_total": None,
        "units_done": 0,
        "units_reused": 0,
        "variant": None,
        "variant_index": None,
        "variants_total": None,
    }
    for raw in lines:
        line = _STAMP.sub("", raw)
        if m := _VARIANT.match(line):
            out.update(variant=m.group(3), variants_total=int(m.group(2)), variant_index=int(m.group(1)))
            out.update(units_total=None, units_done=0, units_reused=0, unit=None, phase="starting variant")
        elif m := _UNITS.match(line):
            out["units_total"] = int(m.group(1))
            out["phase"] = "loading data"
        elif m := _UNIT_LINE.match(line):
            unit, rest = m.groups()
            out["unit"] = unit
            if rest.startswith("already done"):
                out["units_done"] += 1
                out["units_reused"] += 1
                out["phase"] = "reused a finished unit"
            elif rest.startswith("finished in"):
                out["units_done"] += 1
                out["phase"] = "unit finished"
            elif rest == "started":
                out["phase"] = "splitting and fitting"
            elif rest.startswith("fitting stage"):
                out["phase"] = rest
            elif rest.startswith("frozen on VAL"):
                out["phase"] = "selected on VAL"
            elif rest.startswith("evaluating on TEST"):
                out["phase"] = "evaluating on TEST"
        elif line.startswith(("report: ", "comparison: ")):
            out["phase"] = "writing the report" if line.startswith("report") else "comparison written"
    return out


class JobError(ValueError):
    """Options the CLI would refuse, or a job already running."""


def _seeds(v: Any) -> list[int]:
    if v in (None, "", []):
        return []
    items = v.replace(",", " ").split() if isinstance(v, str) else v
    if not isinstance(items, list):
        raise JobError("seeds: give a list of integers")
    out = []
    for s in items:
        if isinstance(s, bool):
            raise JobError(f"seeds: {s!r} is not an integer")
        try:
            out.append(int(s))
        except (TypeError, ValueError):
            raise JobError(f"seeds: {s!r} is not an integer") from None
    return out


def _jobs(v: Any) -> int:
    if isinstance(v, bool):
        raise JobError("jobs must be a positive integer or -1")
    try:
        n = int(v if v not in (None, "") else 1)
    except (TypeError, ValueError):
        raise JobError("jobs must be a positive integer or -1") from None
    if n == 0 or n < -1:
        raise JobError("jobs must be a positive integer or -1")
    return n


def build_command(ws: Workspace, body: dict) -> tuple[list[str], dict]:
    """argv (after ``python -m idslab.cli``) and a description of what the job will write.

    Paths in argv are relative to the workspace, which is the job's working folder, so the log
    shows the command as it would be typed there.
    """
    command = body.get("command")
    if command not in COMMANDS:
        raise JobError(f"command must be one of {', '.join(COMMANDS)}")
    try:
        if command == "example":
            target = ws.resolve(body.get("directory") or "example")
            argv = ["example", _arg(ws, target), *(["--overwrite"] if body.get("overwrite") else [])]
            return argv, {"command": command, "target": ws.rel(target), "target_kind": "folder", "argv": argv}
        if command == "compare":
            return _compare_command(ws, body)
        if command == "sweep":
            return _sweep_command(ws, body)
        config = ws.resolve(body.get("config"), kind="file")
        out = ws.resolve(body.get("out") or DEFAULT_OUT[command])
    except PathError as e:
        raise JobError(str(e)) from None
    cfg = load(config)  # ConfigError reaches the page before anything starts
    argv = [command, _arg(ws, config), "--out", _arg(ws, out)]
    seeds = _seeds(body.get("seeds"))
    if seeds:
        argv += ["--seeds", *map(str, seeds)]
    parallel = False
    if command == "run":
        n_jobs = _jobs(body.get("jobs"))
        parallel = n_jobs != 1
        argv += ["--jobs", str(n_jobs)]
        if body.get("force"):
            argv.append("--force")
        if body.get("no_report"):
            argv.append("--no-report")
    if body.get("debug"):
        argv.insert(0, "--debug")
    target = os.path.join(out, cfg.name)
    kind = "folder" if command == "export-splits" else "experiment"
    return argv, {
        "command": command,
        "config": ws.rel(config),
        "target": ws.rel(target),
        "target_kind": kind,
        "parallel": parallel,
        "argv": argv,
    }


def _bootstrap(v: Any) -> int:
    try:
        n = int(v if v not in (None, "") else 1000)
    except (TypeError, ValueError):
        raise JobError("bootstrap must be a whole number") from None
    if n < 0 or n > 100000:
        raise JobError("bootstrap must be between 0 and 100000")
    return n


def _compare_command(ws: Workspace, body: dict) -> tuple[list[str], dict]:
    exps = body.get("experiments")
    if not isinstance(exps, list) or len(exps) < 2:
        raise JobError("choose at least two experiments")
    try:
        paths = [ws.resolve(str(p), kind="dir") for p in exps]
        out = ws.resolve(
            body.get("out") or "comparisons/" + "-vs-".join(os.path.basename(p) for p in paths)[:120]
        )
    except PathError as e:
        raise JobError(str(e)) from None
    argv = ["compare", *(_arg(ws, p) for p in paths), "--out", _arg(ws, out)]
    argv += ["--bootstrap", str(_bootstrap(body.get("bootstrap")))]
    return argv, {"command": "compare", "target": ws.rel(out), "target_kind": "folder", "argv": argv}


def _sweep_command(ws: Workspace, body: dict) -> tuple[list[str], dict]:
    from idslab.sweep import load_sweep  # noqa: PLC0415

    try:
        path = ws.resolve(body.get("sweep"), kind="file")
        out = ws.resolve(body.get("out") or DEFAULT_OUT["sweep"])
    except PathError as e:
        raise JobError(str(e)) from None
    sweep = load_sweep(path)  # SweepError reaches the page before anything starts
    argv = ["sweep", _arg(ws, path), "--out", _arg(ws, out)]
    seeds = _seeds(body.get("seeds"))
    if seeds:
        argv += ["--seeds", *map(str, seeds)]
    argv += ["--jobs", str(_jobs(body.get("jobs"))), "--bootstrap", str(_bootstrap(body.get("bootstrap")))]
    if body.get("force"):
        argv.append("--force")
    target = os.path.join(out, sweep.name, "comparison")
    parallel = _jobs(body.get("jobs")) != 1
    return argv, {
        "parallel": parallel,
        "argv": argv,
        "command": "sweep",
        "config": ws.rel(path),
        "target": ws.rel(target),
        "target_kind": "folder",
    }


def _arg(ws: Workspace, path: str) -> str:
    rel = ws.rel(path) or "."
    return "./" + rel if rel.startswith("-") else rel  # never read as an option


def _kill_tree_windows(proc: subprocess.Popen) -> None:
    """End a job and the worker processes it started (Windows has no process groups to signal)."""
    taskkill = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "taskkill.exe")
    try:
        subprocess.run(  # noqa: S603  (fixed program path, the job's own pid)
            [taskkill, "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        proc.terminate()  # taskkill missing or stuck: end at least the main process


class Job:
    """At most one job: runs are CPU and memory heavy, and two runs of one config would race on
    the same seed folders."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self.state = "idle"
        self.info: dict = {}
        self.returncode: int | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.stopped = False

    def start(self, argv: list[str], info: dict) -> None:
        with self.lock:
            if self.state == "running":
                raise JobError("a job is already running; stop it or wait for it to finish")
            cmd = [sys.executable, "-m", "idslab.cli", *argv]
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            self.log.clear()
            self.log.append("$ idslab " + " ".join(argv))
            self.proc = subprocess.Popen(  # noqa: S603  (argv list, no shell; options checked first)
                cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=env,
                start_new_session=os.name == "posix",  # its own group, so stop reaches worker processes
            )
            self.state, self.info, self.returncode, self.stopped = "running", info, None, False
            self.started_at, self.finished_at = time.time(), None
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None  # noqa: S101  (Popen was given stdout=PIPE)
        for line in proc.stdout:
            self.log.append(line.rstrip("\n"))
        proc.wait()
        with self.lock:
            self.finished_at = time.time()
            self.returncode = proc.returncode
            if self.stopped:
                self.state = "stopped"
            else:
                self.state = "completed" if proc.returncode == 0 else "failed"

    def stop(self) -> None:
        with self.lock:
            proc = self.proc
            if self.state != "running" or proc is None:
                return
            self.stopped = True
            self.log.append("[stopped from the dashboard]")
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    _kill_tree_windows(proc)
            except ProcessLookupError:
                pass

    def snapshot(self, tail: int | None = None) -> dict:
        log = list(self.log)
        return {
            "state": self.state,
            **self.info,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "now": time.time(),
            "log": log[-tail:] if tail else log,
            "log_lines": len(log),
            "progress": progress(log),
            "parallel": bool(self.info.get("parallel")),
            "units_done_on_disk": self._done_on_disk(),
        }

    def _done_on_disk(self) -> int | None:
        """Units this job finished, counted from DONE files written since it started. Worker
        processes of a parallel run do not log, so this is the count the page can show for them."""
        if self.info.get("target_kind") != "experiment" or self.started_at is None:
            return None
        root = os.path.join(self.cwd, self.info.get("target", ""))
        n = 0
        for d, _, files in os.walk(root):
            if "DONE" in files and os.path.getmtime(os.path.join(d, "DONE")) >= self.started_at:
                n += 1
        return n
