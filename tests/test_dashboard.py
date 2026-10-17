# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
import copy
import http.client
import json
import os
import shutil
import threading
import time
from importlib import resources
from typing import Any

import pytest
import yaml

from idslab.config import from_dict, load
from idslab.dashboard.jobs import JobError, build_command, progress
from idslab.dashboard.server import STATIC, App, make_server
from idslab.dashboard.workspace import PathError, Workspace, WorkspaceMissingError
from idslab.report import write_report
from idslab.runner import run


@pytest.fixture
def ws_dir(tmp_path, data_dir, r2):
    """A workspace with data, one config and one finished run with its report."""
    root = tmp_path / "ws"
    (root / "data").mkdir(parents=True)
    for name in ("train.parquet", "test.parquet"):
        shutil.copyfile(data_dir / name, root / "data" / name)
    r2["dataset"]["train"] = "data/train.parquet"
    r2["dataset"]["test"] = "data/test.parquet"
    (root / "r2.yaml").write_text(yaml.safe_dump(r2))
    (root / "notes.yaml").write_text("just: a list\n")  # YAML, but not a config
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret.yaml").write_text(yaml.safe_dump(r2))
    cfg = load(str(root / "r2.yaml"))
    run(cfg, out_root=str(root / "runs"))
    write_report(str(root / "runs"), cfg)
    return root


@pytest.fixture
def server(ws_dir):
    app = App(str(ws_dir))
    srv = make_server("127.0.0.1", 0, app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, app
    app.job.stop()
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None) -> tuple[int, Any, http.client.HTTPResponse]:
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    hdrs = {"Host": f"127.0.0.1:{port}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    ctype = resp.getheader("Content-Type") or ""
    payload = json.loads(raw) if ctype.startswith("application/json") else raw
    return resp.status, payload, resp


# ---------- workspace ----------


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "../x",
        "a/../../x",
        ".hidden/secret.yaml",
        "~/x",
        "a/.git",
        "C:x",
        "a\\..\\..\\x",
        "f:stream",
    ],
)
def test_paths_outside_or_hidden_are_refused(ws_dir, bad):
    with pytest.raises(PathError):
        Workspace(str(ws_dir)).resolve(bad)


def test_symlink_out_of_the_workspace_is_refused(ws_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, ws_dir / "link")
    with pytest.raises(PathError, match="outside the workspace"):
        Workspace(str(ws_dir)).resolve("link")


def test_configs_and_runs_are_discovered(ws_dir):
    ws = Workspace(str(ws_dir))
    assert [c["path"] for c in ws.configs()] == ["r2.yaml"]  # not notes.yaml, not hidden, not run output
    [exp] = ws.experiments()
    assert exp["path"] == "runs/r2"
    assert exp["units_done"] == exp["units_total"] == 1
    assert exp["has_report"]
    assert exp["report"]["cascade"] is not None
    detail = ws.experiment("runs/r2")
    assert detail["configs"] == ["r2.yaml"]
    assert detail["out_root"] == "runs"
    [unit] = detail["units"]
    assert unit["path"] == "runs/r2/standard/seed_0"
    assert ws.unit(unit["path"])["summary"]["seed"] == 0


def test_save_config_rules(ws_dir):
    ws = Workspace(str(ws_dir))
    assert ws.save_config("sub/new.yaml", "name: x\n", overwrite=False) == "sub/new.yaml"
    with pytest.raises(PathError, match="already exists"):
        ws.save_config("sub/new.yaml", "name: y\n", overwrite=False)
    with pytest.raises(PathError, match=r"\.yaml or \.yml"):
        ws.save_config("evil.py", "print(1)\n", overwrite=True)
    with pytest.raises(PathError):
        ws.save_config("../out.yaml", "x: 1\n", overwrite=True)


# ---------- commands ----------


def test_build_command_matches_the_cli(ws_dir):
    ws = Workspace(str(ws_dir))
    body = {"command": "run", "config": "r2.yaml", "seeds": "0, 3", "jobs": "2", "force": True, "debug": True}
    argv, info = build_command(ws, body)
    assert argv[:5] == ["--debug", "run", "r2.yaml", "--out", "runs"]
    assert argv[5:] == ["--seeds", "0", "3", "--jobs", "2", "--force"]
    assert info["target"] == "runs/r2"
    argv, _ = build_command(ws, {"command": "export-splits", "config": "r2.yaml"})
    assert argv == ["export-splits", "r2.yaml", "--out", "splits"]
    argv, info = build_command(ws, {"command": "example", "directory": "-x", "overwrite": True})
    assert argv == ["example", "./-x", "--overwrite"]  # never read as an option


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"command": "rm"}, "command must be"),
        ({"command": "run", "config": "missing.yaml"}, "no such file"),
        ({"command": "run", "config": "r2.yaml", "jobs": 0}, "jobs must be"),
        ({"command": "run", "config": "r2.yaml", "jobs": True}, "jobs must be"),
        ({"command": "run", "config": "r2.yaml", "seeds": [True]}, "not an integer"),
        ({"command": "run", "config": "r2.yaml", "out": "/srv/out"}, "relative to the workspace"),
    ],
)
def test_build_command_refuses_what_the_cli_would(ws_dir, body, message):
    with pytest.raises(JobError, match=message):
        build_command(Workspace(str(ws_dir)), body)


# ---------- server ----------


def test_api_reads(server):
    srv, _ = server
    status, meta, _ = request(srv, "GET", "/api/meta")
    assert status == 200
    assert meta["commands"] == ["run", "report", "export-splits", "example", "compare", "sweep"]
    status, runs, _ = request(srv, "GET", "/api/runs")
    assert [r["path"] for r in runs] == ["runs/r2"]
    status, cfg, _ = request(srv, "GET", "/api/config?path=r2.yaml")
    assert "stages:" in cfg["yaml"]
    status, files, _ = request(srv, "GET", "/api/files?path=runs/r2")
    assert {"report.html", "report.json", "standard"} <= {e["name"] for e in files["entries"]}
    status, _, _ = request(srv, "GET", "/api/nothing")
    assert status == 404


def test_validate_is_the_cli_loader(server, r2):
    srv, _ = server
    status, ok, _ = request(srv, "POST", "/api/validate", {"yaml": yaml.safe_dump(r2), "path": "r2.yaml"})
    assert status == 200
    assert ok["ok"]
    assert ok["config"]["order"] == list(r2["stages"])
    assert all(d["exists"] for d in ok["config"]["data"])
    assert ok["text"].startswith("r2.yaml: valid")
    r2["stages"]["screen"]["routes"]["anomalous"] = "nowhere"
    _, bad, _ = request(srv, "POST", "/api/validate", {"yaml": yaml.safe_dump(r2)})
    assert not bad["ok"]
    assert "nowhere" in bad["error"]
    _, syntax, _ = request(srv, "POST", "/api/validate", {"yaml": "a: [1"})
    assert not syntax["ok"]


def test_requests_from_other_sites_are_refused(server):
    srv, _ = server
    status, _, _ = request(srv, "GET", "/api/meta", headers={"Host": "evil.example:80"})
    assert status == 403
    evil = {"Origin": "http://evil.example"}
    status, _, _ = request(srv, "POST", "/api/validate", {"yaml": "x"}, headers=evil)
    assert status == 403
    status, _, _ = request(
        srv, "POST", "/api/validate", {"yaml": "x"}, headers={"Content-Type": "text/plain"}
    )
    assert status == 415


def test_files_are_served_safely(server):
    srv, _ = server
    status, body, resp = request(srv, "GET", "/files/runs/r2/report.html")
    assert status == 200
    assert b"<html" in body
    csp = resp.getheader("Content-Security-Policy") or ""
    assert csp.startswith("default-src 'none'")  # no scripts, nothing external
    status, _, resp = request(srv, "GET", "/files/runs/r2/report.md")
    assert (resp.getheader("Content-Type") or "").startswith("text/plain")
    status, _, resp = request(srv, "GET", "/files/runs/r2/standard/seed_0/per_flow.parquet")
    assert "attachment" in (resp.getheader("Content-Disposition") or "")
    bad = ("/files/../r2.yaml", "/files/%2e%2e/x", "/files/.hidden/secret.yaml", "/static/../server.py")
    for path in bad:
        status, _, _ = request(srv, "GET", path)
        assert status == 404, path


def test_static_files_ship_and_carry_the_licence_header(server):
    srv, _ = server
    status, _, resp = request(srv, "GET", "/")
    assert status == 200
    assert "frame-ancestors 'none'" in (resp.getheader("Content-Security-Policy") or "")
    for name in STATIC:
        text = (resources.files("idslab.dashboard") / "static" / name).read_text(encoding="utf-8")
        assert "SPDX-License-Identifier: AGPL-3.0-or-later" in text, name
        status, _, _ = request(srv, "GET", f"/static/{name}")
        assert status == 200, name


def test_save_through_the_api(server, ws_dir):
    srv, _ = server
    status, body, _ = request(srv, "POST", "/api/config/save", {"path": "copy.yaml", "yaml": "name: x\n"})
    assert status == 200
    assert not body["ok"]  # saved, and reported invalid
    assert (ws_dir / "copy.yaml").read_text() == "name: x\n"
    status, body, _ = request(srv, "POST", "/api/config/save", {"path": "copy.yaml", "yaml": "y"})
    assert status == 400
    assert "already exists" in body["error"]


def test_a_job_runs_the_cli_and_only_one_at_a_time(server, ws_dir):
    srv, app = server
    os.remove(ws_dir / "runs" / "r2" / "report.md")
    status, snap, _ = request(srv, "POST", "/api/job", {"command": "report", "config": "r2.yaml"})
    assert status == 200
    assert snap["state"] == "running"
    assert snap["log"][0] == "$ idslab report r2.yaml --out runs"
    status, busy, _ = request(srv, "POST", "/api/job", {"command": "report", "config": "r2.yaml"})
    if app.job.state == "running":  # the first job may already be done on a fast machine
        assert status == 409
        assert "already running" in busy["error"]
    deadline = time.time() + 120
    while app.job.state == "running" and time.time() < deadline:
        time.sleep(0.2)
    _, snap, _ = request(srv, "GET", "/api/job")
    assert snap["state"] == "completed", snap["log"]
    assert (ws_dir / "runs" / "r2" / "report.md").is_file()


# ---------- builder ----------


def test_builder_endpoints(server, ws_dir, r2):
    srv, _ = server
    _, cat, _ = request(srv, "GET", "/api/catalog")
    classes = {e["class"] for e in cat["classifier"]}
    assert "sklearn.ensemble.RandomForestClassifier" in classes
    rf = next(e for e in cat["classifier"] if e["class"].endswith("RandomForestClassifier"))
    assert any(p["name"] == "n_estimators" and p["type"] == "int" for p in rf["params"])
    assert any(e["class"] == "idslab.prep.Encode" for e in cat["step"])

    _, files, _ = request(srv, "GET", "/api/datafiles")
    assert "data/train.parquet" in files
    assert not any(f.startswith("runs/") for f in files)
    _, data, _ = request(srv, "GET", "/api/dataset?path=data/train.parquet")
    assert data["suggest"] == {
        "label": "label",
        "type": "attack_cat",
        "normal_type": "Normal",
        "categorical": ["proto", "service"],
    }

    _, parsed, _ = request(srv, "POST", "/api/parse", {"yaml": (ws_dir / "r2.yaml").read_text()})
    assert parsed["ok"]
    assert parsed["config"]["stages"].keys() == r2["stages"].keys()
    _, bad, _ = request(srv, "POST", "/api/parse", {"yaml": "a: [1"})
    assert not bad["ok"]

    _, dumped, _ = request(srv, "POST", "/api/dump", {"config": parsed["config"], "path": "r2.yaml"})
    assert dumped["ok"]
    assert "off: " in dumped["yaml"]  # a plain word under YAML 1.2 rules, never quoted
    assert "'off'" not in dumped["yaml"]
    broken = dict(parsed["config"], entry="nowhere")
    _, invalid, _ = request(srv, "POST", "/api/dump", {"config": broken})
    assert not invalid["ok"]
    assert "nowhere" in invalid["error"]

    status, _, _ = request(srv, "POST", "/api/layout", {"path": "r2.yaml", "positions": {"screen": [10, 20]}})
    assert status == 200
    _, layout, _ = request(srv, "GET", "/api/layout?path=r2.yaml")
    assert layout == {"positions": {"screen": [10.0, 20.0]}}
    status, _, _ = request(srv, "POST", "/api/layout", {"path": "notes.txt", "positions": {}})
    assert status == 400


# ---------- compare and sweeps ----------


def test_compare_and_sweep_jobs_and_endpoints(server, ws_dir, r2):
    srv, _ = server
    (ws_dir / "sweeps").mkdir()
    spec = {"name": "two", "base": "../r2.yaml", "variants": {"a": {}, "b": {"protocol.objective": "mcc"}}}
    (ws_dir / "sweeps" / "two.yaml").write_text(yaml.safe_dump(spec))
    _, sweeps, _ = request(srv, "GET", "/api/sweeps")
    assert [s["path"] for s in sweeps] == ["sweeps/two.yaml"]
    text = (ws_dir / "sweeps" / "two.yaml").read_text()
    _, check, _ = request(srv, "POST", "/api/sweep/check", {"yaml": text, "path": "sweeps/two.yaml"})
    assert check["ok"]
    assert [v["name"] for v in check["variants"]] == ["a", "b"]
    assert "stages.screen.decision" in check["keys"]
    _, bad, _ = request(
        srv,
        "POST",
        "/api/sweep/check",
        {"yaml": text.replace("../r2.yaml", "nope.yaml"), "path": "sweeps/two.yaml"},
    )
    assert not bad["ok"]

    ws = Workspace(str(ws_dir))
    argv, info = build_command(
        ws, {"command": "sweep", "sweep": "sweeps/two.yaml", "seeds": "0", "bootstrap": 50}
    )
    assert argv[:4] == ["sweep", "sweeps/two.yaml", "--out", "runs"]
    assert info["target"] == "runs/two/comparison"
    with pytest.raises(JobError, match="at least two"):
        build_command(ws, {"command": "compare", "experiments": ["runs/r2"]})

    # a second experiment on the same data and splits, then the in-process comparison
    cfg_raw = copy.deepcopy(r2)
    cfg_raw["name"] = "r2-b"
    cfg_raw["dataset"]["train"] = "data/train.parquet"
    cfg_raw["dataset"]["test"] = "data/test.parquet"
    cfg = from_dict(cfg_raw, base_dir=str(ws_dir))
    run(cfg, out_root=str(ws_dir / "runs"))
    write_report(str(ws_dir / "runs"), cfg)
    status, c, _ = request(
        srv, "POST", "/api/compare", {"experiments": ["runs/r2", "runs/r2-b"], "bootstrap": 20}
    )
    assert status == 200
    assert [h["path"] for h in c["experiments"]] == ["runs/r2", "runs/r2-b"]
    [pair] = c["paired"]
    assert pair["per_unit"][0]["diff"] == pytest.approx(0.0)  # the same design on the same flows


# ---------- experiments: configs grouped with their outputs ----------


def test_runs_log_progress_and_store_the_routes(ws_dir, caplog):
    summary = json.loads((ws_dir / "runs/r2/standard/seed_0/summary.json").read_text())
    outline = summary["cascade"]
    assert outline["entry"] == "screen"
    assert {s["name"]: s["routes"] for s in outline["stages"]}["screen"]["anomalous"] == "clear"
    cfg = load(str(ws_dir / "r2.yaml"))
    with caplog.at_level("INFO", logger="idslab"):
        run(cfg, out_root=str(ws_dir / "runs"))
    lines = [r.getMessage() for r in caplog.records]
    assert "1 unit to run: seed 0" in lines
    assert "seed 0: already done, reused (fingerprints match)" in lines


def test_progress_reads_only_what_the_log_says():
    log = [
        "$ idslab run r2.yaml --out runs --jobs 1",
        "10:00:00 3 units to run: seed 0, seed 1, seed 2",
        "10:00:01 seed 0: already done, reused (fingerprints match)",
        "10:00:02 seed 1: started",
        "10:00:03 seed 1: fitting stage screen (2 candidates)",
    ]
    p = progress(log)
    assert (p["units_total"], p["units_done"], p["units_reused"]) == (3, 1, 1)
    assert (p["unit"], p["phase"]) == ("seed 1", "fitting stage screen (2 candidates)")
    p = progress([*log, "10:00:09 seed 1: finished in 7.0s", "10:00:10 seed 2: evaluating on TEST"])
    assert (p["units_done"], p["phase"]) == (2, "evaluating on TEST")
    p = progress(["10:00:00 sweep s: variant 2 of 3, lof (x=1)", "10:00:00 1 unit to run: seed 0"])
    assert (p["variant"], p["variant_index"], p["variants_total"], p["units_total"]) == ("lof", 2, 3, 1)
    assert progress(["$ idslab report r2.yaml"])["units_total"] is None  # no count is made up


def test_experiments_group_outputs_without_guessing(ws_dir, r2):
    ws = Workspace(str(ws_dir))
    [e] = ws.experiment_index()
    assert (e["key"], e["state"]) == ("c:r2.yaml", "completed")
    assert [o["path"] for o in e["outputs"]] == ["runs/r2"]
    assert not e["changed_since_run"]

    # an identical copy with the same name: both match the run's fingerprint, so neither owns it
    (ws_dir / "copy.yaml").write_text(yaml.safe_dump(r2))
    index = {x["key"]: x for x in ws.experiment_index()}
    assert index["c:r2.yaml"]["outputs"] == index["c:copy.yaml"]["outputs"] == []
    alone = index["o:runs/r2"]
    assert alone["kind"] == "output"
    assert "share the name r2" in alone["reason"]
    assert alone["candidates"] == ["copy.yaml", "r2.yaml"]

    # the copy changes: only r2.yaml matches the fingerprint the run recorded
    changed = copy.deepcopy(r2)
    changed["stages"]["screen"]["decision"] = {"budget": 0.3, "off": "normal"}
    (ws_dir / "copy.yaml").write_text(yaml.safe_dump(changed))
    index = {x["key"]: x for x in ws.experiment_index()}
    assert [o["path"] for o in index["c:r2.yaml"]["outputs"]] == ["runs/r2"]
    assert "o:runs/r2" not in index
    assert index["c:copy.yaml"]["state"] == "draft"

    # no config of that name at all
    os.remove(ws_dir / "copy.yaml")
    shutil.copytree(ws_dir / "runs/r2", ws_dir / "runs/old")
    index = {x["key"]: x for x in ws.experiment_index()}
    assert index["o:runs/old"]["reason"] == "no config in the workspace is named old"


def test_experiment_states(ws_dir, r2):
    ws = Workspace(str(ws_dir))
    missing = copy.deepcopy(r2)
    missing["name"] = "elsewhere"
    missing["dataset"]["train"] = "nowhere/train.parquet"
    (ws_dir / "missing.yaml").write_text(yaml.safe_dump(missing))
    (ws_dir / "broken.yaml").write_text("name: broken\nentry: a\nstages: {a: {kind: nope}}\n")
    edited = copy.deepcopy(r2)
    edited["protocol"]["objective"] = "mcc"
    index = {x["key"]: x for x in ws.experiment_index()}
    assert index["c:missing.yaml"]["state"] == "needs_data"
    assert index["c:broken.yaml"]["state"] == "invalid"
    assert "dataset" in index["c:broken.yaml"]["record"]["error"]
    (ws_dir / "r2.yaml").write_text(yaml.safe_dump(edited))
    e = next(x for x in ws.experiment_index() if x["key"] == "c:r2.yaml")
    assert e["state"] == "completed"  # finished results stay visible
    assert e["changed_since_run"]


def test_report_behind_finished_units(ws_dir):
    ws = Workspace(str(ws_dir))
    [o] = ws.experiments()
    assert not o["report_behind"]
    assert o["report_files"] == {"md": True, "json": True, "html": True}
    done = ws_dir / "runs/r2/standard/seed_0/DONE"
    later = os.path.getmtime(ws_dir / "runs/r2/report.json") + 60
    os.utime(done, (later, later))
    [o] = ws.experiments()
    assert o["report_behind"]


def test_experiment_endpoints(server, ws_dir):
    srv, _ = server
    status, index, _ = request(srv, "GET", "/api/experiments")
    assert status == 200
    assert [e["key"] for e in index] == ["c:r2.yaml"]
    status, e, _ = request(srv, "GET", "/api/experiment?key=c%3Ar2.yaml")
    assert status == 200
    assert e["output"]["path"] == "runs/r2"
    assert e["output"]["report_full"]["objective"]
    assert e["validation"]["ok"]
    status, e, _ = request(srv, "GET", "/api/experiment?output=runs%2Fr2")
    assert e["key"] == "c:r2.yaml"  # an old link to the run finds its experiment
    assert request(srv, "GET", "/api/experiment?key=c%3Anope.yaml")[0] == 404
    status, ds, _ = request(srv, "GET", "/api/datasets")
    train = next(d for d in ds if d["path"] == "data/train.parquet")
    assert train["used_by"] == ["r2.yaml"]
    assert train["size"] > 0
    status, hist, _ = request(srv, "GET", "/api/history")
    assert {h["kind"] for h in hist} == {"unit", "report"}
    unit = next(h for h in hist if h["kind"] == "unit")
    assert (unit["experiment"], unit["label"]) == ("runs/r2", "seed 0")


def test_running_job_marks_its_experiment(server, ws_dir):
    srv, app = server
    body = {"command": "run", "config": "r2.yaml", "out": "runs", "force": True}
    status, snap, _ = request(srv, "POST", "/api/job", body)
    assert status == 200
    assert snap["argv"][:2] == ["run", "r2.yaml"]
    assert "progress" in snap
    [e] = request(srv, "GET", "/api/experiments")[1]
    assert e["state"] in ("running", "completed")  # completed if the job already ended
    for _ in range(600):
        if app.job.snapshot()["state"] != "running":
            break
        time.sleep(0.1)
    snap = app.job.snapshot()
    assert snap["state"] == "completed", snap["log"][-5:]
    assert snap["progress"]["units_total"] == 1
    assert snap["progress"]["units_done"] == 1
    assert snap["units_done_on_disk"] == 1


def test_workspace_folder_problems_are_told_apart(tmp_path):
    with pytest.raises(WorkspaceMissingError, match="does not exist") as e:
        Workspace(str(tmp_path / "demo"))
    assert "--workspace" not in str(e.value)  # command-line advice belongs to the CLI
    (tmp_path / "file").write_text("x")
    with pytest.raises(PathError, match="is a file, not a folder"):
        Workspace(str(tmp_path / "file"))
    if os.name == "posix" and os.geteuid() != 0:
        locked = tmp_path / "locked"
        (locked / "ws").mkdir(parents=True)
        locked.chmod(0)
        try:
            with pytest.raises(PathError, match="cannot be read") as e:
                Workspace(str(locked / "ws"))
            assert not isinstance(e.value, WorkspaceMissingError)
        finally:
            locked.chmod(0o700)


def test_cli_dashboard_explains_a_bad_workspace(tmp_path, capsys, monkeypatch):
    from idslab.cli import main  # noqa: PLC0415

    monkeypatch.chdir(tmp_path)
    assert main(["dashboard", "--workspace", "demo"]) == 2
    err = capsys.readouterr().err
    assert "workspace folder demo does not exist" in err
    assert "check the path for a typo" in err
    assert "idslab example demo" in err
    assert "--debug" not in err
    assert main(["dashboard", "--workspace", "my data"]) == 2
    err = capsys.readouterr().err
    assert "check the path for a typo" in err
    assert "idslab example <this folder>" in err  # not pasted unquoted
    with pytest.raises(WorkspaceMissingError):
        main(["--debug", "dashboard", "--workspace", "demo"])  # --debug keeps the traceback

    (tmp_path / "file").write_text("x")
    assert main(["dashboard", "--workspace", "file"]) == 2
    err = capsys.readouterr().err
    assert "is a file, not a folder" in err
    assert "--debug" not in err
    assert main(["dashboard", "--workspace", "a/b/c"]) == 2  # missing more than one level up
    err = capsys.readouterr().err
    assert "neither does the folder above it" in err
    assert "idslab example" not in err
    if os.name == "posix":
        os.symlink(tmp_path / "gone", tmp_path / "link")
        assert main(["dashboard", "--workspace", "link"]) == 2
        err = capsys.readouterr().err
        assert "is a link to" in err
        assert "idslab example" not in err  # the example cannot be written through a broken link
        assert main(["dashboard", "--workspace", "link/"]) == 2  # a trailing slash too
        assert "idslab example" not in capsys.readouterr().err
        assert main(["dashboard", "--workspace", "link/demo"]) == 2
        assert "idslab example" not in capsys.readouterr().err
        os.symlink(tmp_path / "loop", tmp_path / "loop")
        assert main(["dashboard", "--workspace", "loop/demo"]) == 2
        assert "points back to itself" in capsys.readouterr().err
        if os.geteuid() != 0:
            ro = tmp_path / "ro"
            ro.mkdir()
            ro.chmod(0o500)
            try:
                assert main(["dashboard", "--workspace", "ro/demo"]) == 2
                err = capsys.readouterr().err
                assert "cannot be written to" in err
                assert "idslab example" not in err
            finally:
                ro.chmod(0o700)
        gone = tmp_path / "gone-cwd"
        gone.mkdir()
        monkeypatch.chdir(gone)
        gone.rmdir()
        assert main(["dashboard", "--workspace", "."]) == 2
        assert "the current folder no longer exists" in capsys.readouterr().err
        monkeypatch.chdir(tmp_path)
        # a link followed by .. resolves as the system resolves it (link first)
        (tmp_path / "x" / "y").mkdir(parents=True)
        (tmp_path / "x" / "ws").mkdir()
        os.symlink(tmp_path / "x" / "y", tmp_path / "toy")
        assert Workspace("toy/../ws").root == os.path.realpath(tmp_path / "x" / "ws")
    assert main(["dashboard", "--workspace", "file/demo"]) == 2
    err = capsys.readouterr().err
    assert "is a file, not a folder" in err
    assert "idslab example" not in err  # creating it there cannot work

    assert main(["example", "demo"]) == 0
    Workspace(str(tmp_path / "demo"))  # once the example has run, the folder is accepted


def test_results_of_another_version_are_marked(ws_dir):
    from idslab import __version__  # noqa: PLC0415

    ws = Workspace(str(ws_dir))
    [e] = ws.experiment_index()
    [o] = e["outputs"]
    assert (o["versions"], o["other_version"], o["reportable_units"]) == ([__version__], [], 1)
    assert "_done_keys" not in o
    assert "_done_keys" not in ws.experiments()[0]
    summary = ws_dir / "runs/r2/standard/seed_0/summary.json"
    summary.rename(summary.with_suffix(".bak"))  # DONE without a summary: the report would skip it
    assert ws.experiment_index()[0]["outputs"][0]["reportable_units"] == 0
    summary.with_suffix(".bak").rename(summary)
    done = ws_dir / "runs/r2/standard/seed_0/DONE"
    data = json.loads(done.read_text())
    data["fingerprints"]["package_version"] = "0.0.1"
    done.write_text(json.dumps(data))
    [e] = ws.experiment_index()
    [o] = e["outputs"]
    assert (o["other_version"], o["reportable_units"], e["state"]) == (["0.0.1"], 0, "outdated")
    with pytest.raises(FileNotFoundError, match="no finished seeds"):  # what Rebuild report would meet
        write_report(str(ws_dir / "runs"), load(str(ws_dir / "r2.yaml")))
    del data["fingerprints"]["package_version"]  # a DONE that does not say counts as another version
    done.write_text(json.dumps(data))
    [e] = ws.experiment_index()
    assert (e["outputs"][0]["other_version"], e["state"]) == (["unknown"], "outdated")
