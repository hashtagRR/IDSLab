# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Plugins: packages that add models, pipeline steps and objectives through an entry point.

A package declares, in its ``pyproject.toml``::

    [project.entry-points."idslab.plugins"]
    mytool = "mytool.idslab:plugin"

where ``plugin()`` returns a mapping with any of these keys:

* ``classifier``, ``detector``, ``step``: lists of ``{class, label, note}`` shown in the builder's
  catalog (the classes are used in configs like any other class path);
* ``objectives``: ``{name: formula}``, where ``formula(tp, fp, tn, fn)`` returns the objective from
  confusion counts (NumPy arrays that broadcast; higher is better).

A plugin that fails to load is logged and skipped; it never stops a run.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Any

GROUP = "idslab.plugins"
SECTIONS = ("classifier", "detector", "step")
logger = logging.getLogger(__name__)


def _entry_points() -> list[Any]:
    return list(entry_points(group=GROUP))


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    """Every installed plugin, merged: catalog sections and objectives (objectives are registered)."""
    from idslab.metrics import register_objective  # noqa: PLC0415  (metrics imports this module lazily)

    merged: dict[str, Any] = {k: [] for k in SECTIONS} | {"objectives": {}, "plugins": []}
    for ep in _entry_points():
        try:
            spec = ep.load()()
        except Exception:  # a broken plugin is reported and skipped
            logger.warning("idslab plugin %s could not be loaded", ep.name, exc_info=True)
            continue
        if not isinstance(spec, dict):
            logger.warning("idslab plugin %s did not return a mapping; skipped", ep.name)
            continue
        merged["plugins"].append(ep.name)
        for section in SECTIONS:
            merged[section] += [
                i for i in spec.get(section, []) if isinstance(i, dict) and isinstance(i.get("class"), str)
            ]
        for name, formula in (spec.get("objectives") or {}).items():
            if callable(formula):
                register_objective(str(name), formula)
                merged["objectives"][str(name)] = formula
    return merged


def objectives() -> dict[str, Any]:
    return load()["objectives"]
