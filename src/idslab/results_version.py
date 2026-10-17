# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""The results version: which version of the evaluation a finished run unit belongs to.

A unit's fingerprints decide whether it is reused and whether a report counts it. They carry this
number rather than the package version, so a release that changes only the dashboard, the docs or
anything else that cannot alter what a run computes keeps every finished unit valid.

Raise RESULTS_VERSION in the same change as anything that can alter a unit's output: splits,
fitting, selection, controls, metrics, the written files a report reads. A release that raises it
says so in the changelog, because every user's experiments then run again.
"""

from __future__ import annotations

from typing import Any

RESULTS_VERSION = "1"

# Units written before results versions existed carry the package version instead. These package
# versions compute the same results as RESULTS_VERSION "1": run on the same config and data, they
# give identical per-flow predictions, frozen configurations and controls.
_SAME_AS_ONE = frozenset({"0.2.0", "0.3.0"})


def results_version(fingerprints: Any) -> str | None:
    """The results version of a unit's stored fingerprints, or None when it cannot be told."""
    if not isinstance(fingerprints, dict):
        return None
    if "results_version" in fingerprints:
        return str(fingerprints["results_version"])
    if fingerprints.get("package_version") in _SAME_AS_ONE:
        return "1"
    return None


def normalized(fingerprints: Any) -> dict[str, Any]:
    """Stored fingerprints in the current form, so older units compare equal when they match.
    Anything that is not a mapping (a damaged DONE file) matches nothing, so the unit runs again."""
    if not isinstance(fingerprints, dict):
        return {"results_version": None}
    out = {k: v for k, v in fingerprints.items() if k != "package_version"}
    out["results_version"] = results_version(fingerprints)
    return out
