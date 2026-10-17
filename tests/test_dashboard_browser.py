# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""The dashboard in a real browser: every page loads without a script error, old addresses lead
to their new pages, and the main journeys work end to end.

Needs Playwright and its Chromium (``pip install playwright`` and ``playwright install chromium``);
the module is skipped without them. ``IDSLAB_CHROMIUM`` points at another Chromium binary.
"""

import os
import shutil
import threading

import pytest
import yaml

from idslab.config import load
from idslab.dashboard.server import App, make_server
from idslab.report import write_report
from idslab.runner import run

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            # IDSLAB_CHROMIUM: a Chromium to use instead of the one Playwright downloads
            b = p.chromium.launch(executable_path=os.environ.get("IDSLAB_CHROMIUM") or None)
        except Exception as e:  # noqa: BLE001  (no browser installed: skip, do not fail)
            pytest.skip(f"Chromium for Playwright is not installed: {e}")
        yield b
        b.close()


@pytest.fixture
def site(tmp_path, data_dir, r2):
    """A served workspace with data, the R2 config and one finished run."""
    root = tmp_path / "ws"
    (root / "data").mkdir(parents=True)
    for name in ("train.parquet", "test.parquet"):
        shutil.copyfile(data_dir / name, root / "data" / name)
    r2["dataset"]["train"] = "data/train.parquet"
    r2["dataset"]["test"] = "data/test.parquet"
    (root / "r2.yaml").write_text("# notes kept by hand\n" + yaml.safe_dump(r2, sort_keys=False))
    cfg = load(str(root / "r2.yaml"))
    run(cfg, out_root=str(root / "runs"))
    write_report(str(root / "runs"), cfg)
    app = App(str(root))
    srv = make_server("127.0.0.1", 0, app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", root
    app.job.stop()
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    yield pg
    ctx.close()


K = "c%3Ar2.yaml"
PAGES = [
    "#/",
    "#/new",
    "#/templates",
    "#/datasets",
    "#/datasets/data%2Ftrain.parquet",
    "#/jobs",
    "#/reports",
    "#/help",
    "#/files",
    "#/compare",
    "#/sweeps",
    *(f"#/e/{K}/setup/{s}" for s in ("data", "cascade", "evaluation", "review", "yaml")),
    *(f"#/e/{K}/{t}" for t in ("runs", "results", "report", "files")),
]
ALIASES = {
    "#/config/r2.yaml": f"#/e/{K}/setup/yaml",
    "#/builder/r2.yaml": f"#/e/{K}/setup/cascade",
    "#/run/runs%2Fr2/units": f"#/e/{K}/runs",
    "#/command/run/r2.yaml": f"#/e/{K}/setup/review",
    "#/command/report": "#/reports",
    "#/job": "#/jobs",
    "#/example": "#/datasets",
    "#/configs": "#/",
}


def test_every_page_loads_and_old_addresses_redirect(site, page):
    base, _ = site
    for h in PAGES:
        page.goto(f"{base}/{h}")
        page.wait_for_selector(".page h2", timeout=60000)
    for old, new in ALIASES.items():
        page.goto(f"{base}/{old}")
        page.wait_for_function("h => location.hash === h", arg=new, timeout=30000)
    assert page.errors == []


def test_narrow_screens_do_not_scroll_sideways(site, browser):
    base, _ = site
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
        pg = ctx.new_page()
        for h in PAGES:
            pg.goto(f"{base}/{h}")
            pg.wait_for_selector(".page h2", timeout=60000)
            pg.wait_for_timeout(300)
            assert pg.evaluate("document.documentElement.scrollWidth - innerWidth") <= 0, (scheme, h)
        ctx.close()


def test_template_to_run_to_report(site, page):
    base, root = site
    page.goto(f"{base}/#/templates")
    card = page.locator("article.template", has_text="r2_anomaly_first.yaml")
    card.locator("button", has_text="Use this configuration").click()
    page.locator("dialog[open] button", has_text="Save").click()
    page.wait_for_url("**/setup/data")
    text = (root / "my_r2_anomaly_first.yaml").read_text()
    assert "name: my-r2-anomaly-first" in text  # its own name, so its results are told apart
    assert text.startswith("# SPDX")  # the template's comments are kept
    page.locator("a.step", has_text="Review").click()
    page.locator("#rv-seeds").fill("0")
    page.locator("#rv-seeds").dispatch_event("input")
    page.locator("button.primary", has_text="Run").click()
    page.wait_for_url("**/#/jobs")
    page.wait_for_selector("text=View results", timeout=180000)
    assert "1 of 1 finished" in page.locator(".page").inner_text()
    page.locator("a", has_text="View results").click()
    page.wait_for_selector("p.lead", timeout=30000)
    page.locator("nav.tabs a", has_text="Report").click()
    page.wait_for_selector("iframe.report-frame")
    assert page.errors == []


def test_yaml_and_visual_editing_share_one_draft(site, page):
    base, root = site
    page.goto(f"{base}/#/e/{K}/setup/yaml")
    area = page.locator(".editor textarea")
    area.wait_for()
    area.fill(area.input_value().replace("objective: accuracy", "objective: mcc"))
    badge = "document.querySelector('.page-head .badge:last-of-type')?.textContent"
    page.wait_for_function(f"() => {badge}.includes('Unsaved')")
    page.locator("a.step", has_text="Evaluation").click()
    assert page.locator("#ev-objective").input_value() == "mcc"

    # leaving with unsaved edits asks first
    asked = []
    page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    page.locator(".nav a", has_text="Jobs").click()
    page.wait_for_timeout(300)
    assert asked
    assert "unsaved" in asked[0]
    assert "/setup/evaluation" in page.url

    # a visual edit on a file with comments asks before rewriting it; Keep leaves it as typed
    page.locator("a.step", has_text="Cascade").click()
    page.locator(".bl-chip.multiclass").click()
    page.locator("dialog[open] .diff").wait_for()
    page.locator("dialog[open] button", has_text="Keep the YAML").click()
    page.wait_for_timeout(300)
    assert page.locator(".bl-node").count() == 2
    page.locator(".bl-chip.multiclass").click()
    page.locator("dialog[open] button", has_text="Rewrite").click()
    page.wait_for_function("() => document.querySelectorAll('.bl-node').length === 3")

    # click a port, then a destination; then the same from the keyboard
    page.locator('[data-key="p:type:next"]').click()
    assert "Choose where type" in page.locator(".bl-hint").inner_text()
    page.locator(".bl-term.benign").click()
    page.wait_for_timeout(500)
    page.locator('[data-key="p:clear:attack"]').focus()
    page.keyboard.press("Enter")
    page.locator('[data-key="n:type"]').focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    assert page.locator(".bl-issue").count() == 0  # every output has a destination and every stage is reached

    # a loop is drawn and named, and the run is blocked until it is fixed
    page.locator('select[aria-label="type output destination"]').select_option("clear")
    page.wait_for_selector(".bl-edge.cycle")
    assert "The routes loop" in page.locator(".bl-issue").first.inner_text()
    page.locator('select[aria-label="type output destination"]').select_option("ATTACK")
    page.wait_for_function("() => !document.querySelector('.bl-edge.cycle')")
    page.locator(".page-head button", has_text="Save").last.click()
    page.wait_for_function(f"() => {badge} === 'Saved'")
    saved = yaml.safe_load((root / "r2.yaml").read_text())
    assert saved["protocol"]["objective"] == "mcc"
    assert saved["stages"]["clear"]["routes"]["attack"] == "type"
    assert saved["stages"]["type"]["next"] == "ATTACK"
    assert page.errors == []
