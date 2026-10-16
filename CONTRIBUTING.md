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
- A change that can alter results bumps the version in `pyproject.toml` and `src/idslab/__init__.py`,
  so finished run units are not reused by mistake, and gets a line in [CHANGELOG.md](CHANGELOG.md).
- New source and test files start with the license header:

  ```python
  # SPDX-License-Identifier: AGPL-3.0-or-later
  # Copyright (C) 2026 hashtagRR
  ```

By contributing you agree that your contribution is licensed under the AGPL-3.0-or-later.
