# Contributing

Bug reports and pull requests are welcome at https://github.com/hashtagRR/IDSLab.

## Setup

```bash
git clone https://github.com/hashtagRR/IDSLab
cd IDSLab
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,xgboost,pyod,imbalance,torch]"
```

## Checks

```bash
pytest                      # the whole suite, a few minutes
ruff check src tests examples
ruff format --check src tests examples
pyright
```

## Rules for changes

- The protocol rules in [DESIGN.md](DESIGN.md) hold for every change: TEST labels never influence
  fitting or selection, and the controls are always reported. The test suite includes a check that
  shuffles TEST labels and requires the same frozen configuration; keep it passing.
- A change that can alter what a run computes raises `RESULTS_VERSION` in
  `src/idslab/results_version.py`, so finished run units are not reused by mistake, and says so in
  [CHANGELOG.md](CHANGELOG.md). Other changes leave it alone, so users keep their finished runs.
- Every change worth noting gets a line under "Unreleased" in the changelog; a release turns that
  section into its notes (see [Releasing](#releasing)).
- New source and test files start with the license header:

  ```python
  # SPDX-License-Identifier: AGPL-3.0-or-later
  # Copyright (C) 2026 hashtagRR
  ```

By contributing you agree that your contribution is licensed under the AGPL-3.0-or-later.

## Releasing

Releases run from GitHub Actions; nothing is uploaded from a personal machine.

1. Make sure the changelog's **Unreleased** section describes the release, and that it says so when
   `RESULTS_VERSION` was raised (users' experiments then run once more).
2. On GitHub, open **Actions**, choose **release**, press **Run workflow** on `main`, and give the
   version (for example `0.3.1`) and, optionally, words for the title.
3. The workflow tests `main`, sets the version in `pyproject.toml`, `src/idslab/__init__.py` and
   `CITATION.cff`, dates the changelog section (leaving an empty **Unreleased** heading for the next
   changes), builds and checks the packages, and prepares the commit "Release X.Y.Z" and the tag
   `vX.Y.Z` without pushing them.
4. The PyPI upload waits for approval in the `pypi` environment. Approving it publishes to PyPI with
   Trusted Publishing (no token); only then are the commit and tag pushed and the GitHub release
   created, with the changelog section as its notes. Zenodo archives that release and gives it a DOI.

`python scripts/release.py X.Y.Z --notes notes.md` shows locally what step 3 changes (`git diff`; undo
with `git checkout .`). Declining the approval, or a failed upload, leaves `main` and the tags
untouched, so the same version can simply be run again. If `main` moved while the release waited for approval, the
workflow merges the release commit into it; when the two changed the same lines it asks, in the run's
warnings, for the tag to be merged by hand.

One-time setup: on PyPI, project `idslab`, **Publishing**, add a GitHub publisher for owner
`hashtagRR`, repository `IDSLab`, workflow `release.yml`, environment `pypi`. On GitHub, **Settings**,
**Environments**, create `pypi` and add yourself as a required reviewer.
