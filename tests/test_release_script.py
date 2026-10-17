# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""scripts/release.py: the files a release changes, on a copy of the real ones."""

import datetime
import runpy
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
release = runpy.run_path(str(ROOT / "scripts" / "release.py"))


@pytest.fixture
def repo(tmp_path):
    for rel in ("pyproject.toml", "CITATION.cff", "CHANGELOG.md", "src/idslab/__init__.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    # give the copy an Unreleased entry whatever state the real changelog is in (right after a
    # release its Unreleased heading is empty)
    log = tmp_path / "CHANGELOG.md"
    text = log.read_text(encoding="utf-8")
    if "## Unreleased" in text:
        text = text.replace("## Unreleased\n", "## Unreleased\n\n- a change\n", 1)
    else:
        text = text.replace("\n## ", "\n## Unreleased\n\n- a change\n\n## ", 1)
    log.write_text(text, encoding="utf-8")
    return tmp_path


def _next(root):
    major, minor, patch = (int(x) for x in release["current_version"](root).split("."))
    return f"{major}.{minor}.{patch + 1}"


def test_prepare_sets_every_version_and_dates_the_changelog(repo):
    version = _next(repo)
    unreleased = release["unreleased"]((repo / "CHANGELOG.md").read_text(encoding="utf-8"))[2]
    notes = release["prepare"](repo, version, datetime.date(2030, 1, 2))
    assert release["current_version"](repo) == version
    assert f'__version__ = "{version}"' in (repo / "src/idslab/__init__.py").read_text(encoding="utf-8")
    cff = (repo / "CITATION.cff").read_text(encoding="utf-8")
    assert f"version: {version}" in cff
    assert 'date-released: "2030-01-02"' in cff
    log = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## Unreleased\n\n## {version} (2030-01-02)" in log  # ready for the next changes
    with pytest.raises(release["ReleaseError"], match="empty"):  # and empty until they are written
        release["prepare"](repo, _next(repo), datetime.date(2030, 1, 3))
    assert notes.startswith(unreleased)
    assert f"pip install idslab=={version}" in notes


def test_refuses_what_must_not_be_released(repo):
    err = release["ReleaseError"]
    current = release["current_version"](repo)
    with pytest.raises(err, match="not newer"):
        release["prepare"](repo, current, datetime.date.today())
    with pytest.raises(err, match="three numbers"):
        release["prepare"](repo, "1.0", datetime.date.today())
    log = repo / "CHANGELOG.md"
    text = log.read_text(encoding="utf-8")
    start, end, _ = release["unreleased"](text)
    log.write_text(text[:start] + "## Unreleased\n\n" + text[end:], encoding="utf-8")
    with pytest.raises(err, match="empty"):
        release["prepare"](repo, _next(repo), datetime.date.today())
    assert release["current_version"](repo) == current  # nothing changed on refusal


def test_a_failed_update_leaves_every_file_as_it_was(repo):
    cff = repo / "CITATION.cff"
    cff.write_text(cff.read_text(encoding="utf-8").replace("date-released:", "released:"), encoding="utf-8")
    before = {p: p.read_text(encoding="utf-8") for p in repo.rglob("*") if p.is_file()}
    with pytest.raises(release["ReleaseError"], match=r"CITATION\.cff"):
        release["prepare"](repo, _next(repo), datetime.date.today())
    assert {p: p.read_text(encoding="utf-8") for p in before} == before


def test_an_unusual_current_version_is_named_as_such(repo):
    py = repo / "pyproject.toml"
    current = release["current_version"](repo)
    py.write_text(
        py.read_text(encoding="utf-8").replace(f'version = "{current}"', 'version = "9.9.9rc1"'),
        encoding="utf-8",
    )
    with pytest.raises(release["ReleaseError"], match=r"pyproject\.toml has version 9\.9\.9rc1"):
        release["prepare"](repo, "10.0.0", datetime.date.today())


def test_command_line(repo, tmp_path):
    notes = tmp_path / "notes.md"
    main = release["main"]
    assert main([_next(repo), "--root", str(repo), "--notes", str(notes), "--date", "2030-01-02"]) == 0
    assert notes.read_text(encoding="utf-8").strip()
    assert main(["0.0.1", "--root", str(repo)]) == 2


def test_the_next_release_after_one(repo):
    first = _next(repo)
    release["prepare"](repo, first, datetime.date(2030, 1, 2))
    log = repo / "CHANGELOG.md"  # right after a release: an empty Unreleased heading
    log.write_text(log.read_text(encoding="utf-8").replace("## Unreleased\n", "## Unreleased\n\n- next\n", 1))
    notes = release["prepare"](repo, _next(repo), datetime.date(2030, 2, 3))
    assert notes.startswith("- next")
