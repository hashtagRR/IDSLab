# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Small synthetic flow dataset for examples and tests.

Five types with different difficulty: DoS is easy to separate, Scan is medium,
Exploit overlaps normal traffic, and Worms is rare and far from everything else
(the case where an anomaly stage can help). Two categorical columns and six
numeric ones. No real traffic is involved.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

TYPES = ("Normal", "DoS", "Scan", "Exploit", "Worms")
SHARES = (0.62, 0.14, 0.12, 0.10, 0.02)
N_NUM = 6


def make_frame(n: int, seed: int, shares: tuple[float, ...] = SHARES) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    types = rng.choice(TYPES, size=n, p=np.asarray(shares) / np.sum(shares))
    centre = {
        "Normal": np.zeros(N_NUM),
        "DoS": np.array([3.0, 2.5, 0, 0, 0, 0]),
        "Scan": np.array([0, 1.5, 1.5, 0, 0, 0]),
        "Exploit": np.array([0.6, 0, 0, 0.7, 0, 0]),
        "Worms": np.array([0, 0, 0, 0, 5.0, -5.0]),
    }
    num = np.vstack([rng.normal(centre[t], 1.0) for t in types])
    proto = np.where(
        types == "Scan",
        rng.choice(["tcp", "udp", "icmp"], n, p=[0.3, 0.2, 0.5]),
        rng.choice(["tcp", "udp", "icmp"], n, p=[0.7, 0.25, 0.05]),
    )
    service = rng.choice(["http", "dns", "ssh", "-"], n, p=[0.4, 0.3, 0.1, 0.2])
    frame = pd.DataFrame(num, columns=[f"x{i}" for i in range(N_NUM)])
    frame.insert(0, "service", service)
    frame.insert(0, "proto", proto)
    frame["attack_cat"] = types
    frame["label"] = (types != "Normal").astype(int)
    return frame


def write_example(directory: str, n_train: int = 6000, n_test: int = 3000, seed: int = 0) -> tuple[str, str]:
    """Write train.parquet and test.parquet into ``directory``; return their paths."""
    os.makedirs(directory, exist_ok=True)
    train = make_frame(n_train, seed)
    test = make_frame(n_test, seed + 1)
    paths = (os.path.join(directory, "train.parquet"), os.path.join(directory, "test.parquet"))
    train.to_parquet(paths[0], index=False)
    test.to_parquet(paths[1], index=False)
    return paths
