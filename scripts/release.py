# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Prepare a release: set the version everywhere it is written, and turn the changelog's
Unreleased section into the release's dated section and its notes.

    python scripts/release.py 0.3.1 --notes release-notes.md

Run by .github/workflows/release.yml; safe to run locally to see what a release would change
(``git diff`` shows it, ``git checkout .`` undoes it). Nothing is committed, tagged or uploaded here.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
CONCEPT_DOI = "10.5281/zenodo.23435536"  # Zenodo: every version of IDSLab


class ReleaseError(ValueError):
    """Something a release must not go ahead with."""


def _parts(version: str) -> tuple[int, int, int]:
    m = VERSION.match(version)
    if not m:
        raise ReleaseError(f"version {version!r}: give three numbers, for example 0.3.1")
    return int(m[1]), int(m[2]), int(m[3])


def _replaced(name: str, text: str, pattern: str, repl: str) -> str:
    new, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if n != 1:
        raise ReleaseError(f"{name}: the line to update was not found")
    return new


def current_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not m:
        raise ReleaseError("pyproject.toml: no version line")
    return m[1]


def unreleased(changelog: str) -> tuple[int, int, str]:
    """Start and end of the Unreleased section (heading included) and its text."""
    m = re.search(r"^## Unreleased[ \t]*\n", changelog, re.MULTILINE)
    if not m:
        raise ReleaseError("CHANGELOG.md: no '## Unreleased' section; write the release's changes there")
    nxt = re.search(r"^## ", changelog[m.end() :], re.MULTILINE)
    end = m.end() + nxt.start() if nxt else len(changelog)
    body = changelog[m.end() : end].strip()
    if not body:
        raise ReleaseError("CHANGELOG.md: the Unreleased section is empty; a release needs notes")
    return m.start(), end, body


def prepare(root: Path, version: str, today: datetime.date) -> str:
    """Update the files for ``version``; returns the release notes (Markdown). Every change is
    worked out first and the files are written only if all of them succeed."""
    old = current_version(root)
    new_parts = _parts(version)
    if not VERSION.match(old):
        raise ReleaseError(
            f"pyproject.toml has version {old}, which is not three numbers; fix it by hand first"
        )
    if new_parts <= _parts(old):
        raise ReleaseError(f"version {version} is not newer than {old}")
    date = today.isoformat()
    paths = {
        "log": root / "CHANGELOG.md",
        "pyproject": root / "pyproject.toml",
        "init": root / "src" / "idslab" / "__init__.py",
        "cff": root / "CITATION.cff",
    }
    texts = {k: p.read_text(encoding="utf-8") for k, p in paths.items()}
    start, end, body = unreleased(texts["log"])
    log = texts["log"]
    # the dated section takes the notes; an empty Unreleased heading waits for the next changes
    texts["log"] = f"{log[:start]}## Unreleased\n\n## {version} ({date})\n\n{body}\n\n{log[end:]}"
    texts["pyproject"] = _replaced(
        "pyproject.toml", texts["pyproject"], r'^version = "[^"]+"', f'version = "{version}"'
    )
    texts["init"] = _replaced(
        "__init__.py", texts["init"], r'^__version__ = "[^"]+"', f'__version__ = "{version}"'
    )
    texts["cff"] = _replaced("CITATION.cff", texts["cff"], r"^version: .+$", f"version: {version}")
    texts["cff"] = _replaced("CITATION.cff", texts["cff"], r"^date-released: .+$", f'date-released: "{date}"')
    for k, p in paths.items():
        p.write_text(texts[k], encoding="utf-8")
    return (
        f"{body}\n\n## Install\n\n    pip install idslab=={version}\n\n"
        f"Every change is in [CHANGELOG.md](https://github.com/hashtagRR/IDSLab/blob/main/CHANGELOG.md). "
        f"Each release is archived on Zenodo with its own DOI; "
        f"[{CONCEPT_DOI}](https://doi.org/{CONCEPT_DOI}) lists them all.\n"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prepare an IDSLab release: version, changelog, notes.")
    p.add_argument("version")
    p.add_argument("--notes", help="write the release notes to this file")
    p.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help=argparse.SUPPRESS)
    p.add_argument("--date", help=argparse.SUPPRESS)  # for tests
    args = p.parse_args(argv)
    now = datetime.datetime.now(datetime.timezone.utc).date()
    today = datetime.date.fromisoformat(args.date) if args.date else now
    try:
        notes = prepare(Path(args.root), args.version, today)
    except ReleaseError as e:
        print(f"release: {e}", file=sys.stderr)
        return 2
    if args.notes:
        Path(args.notes).write_text(notes, encoding="utf-8")
    print(f"prepared {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
