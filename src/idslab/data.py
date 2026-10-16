# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 hashtagRR
"""Read the dataset and cut the per-seed splits (DESIGN.md sections 1 and 2).

Dataset-level steps are stateless: read, drop columns, derive the binary label,
map and fill types. Nothing here learns from the data.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from idslab.config import Config, DatasetSpec, Protocol

RARE = "__rare__"


class DataError(ValueError):
    """The data does not match the config."""


@dataclass
class Table:
    """All rows of the dataset: the train file, then the test file if there is one."""

    X: pd.DataFrame
    y: np.ndarray
    types: np.ndarray | None
    row_id: np.ndarray
    from_test_file: np.ndarray
    files: list[str]
    notes: list[str] = field(default_factory=list)
    raw_types: np.ndarray | None = None  # types before type_map (held-out families at raw level, novelty)

    def __len__(self) -> int:
        return len(self.y)


@dataclass
class Splits:
    """Positions into a Table for one seed."""

    seed: int
    fit_base: np.ndarray
    cal: np.ndarray
    val: np.ndarray
    test: np.ndarray
    log: list[str]

    @property
    def fit(self) -> np.ndarray:
        return np.sort(np.concatenate([self.fit_base, self.cal]))

    def fingerprint(self, row_id: np.ndarray) -> str:
        h = hashlib.sha256()
        for name in ("fit_base", "cal", "val", "test"):
            h.update(name.encode())
            h.update("\n".join(np.sort(row_id[getattr(self, name)]).tolist()).encode())
        return h.hexdigest()


def read_file(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise DataError(f"data file not found: {path}")
    low = path.lower()
    if low.endswith((".parquet", ".pq")):
        return pd.read_parquet(path)
    if low.endswith((".csv", ".csv.gz", ".csv.zip", ".csv.bz2", ".csv.xz")):
        return pd.read_csv(path, low_memory=False)
    raise DataError(f"unsupported file type (use .csv or .parquet): {path}")


def load_table(cfg: Config) -> Table:
    spec = cfg.dataset
    files = [cfg.path(spec.train)]
    frames = [read_file(files[0])]
    if spec.test is not None:
        files.append(cfg.path(spec.test))
        frames.append(read_file(files[1]))
        if list(frames[1].columns) != list(frames[0].columns):
            only_train = sorted(set(frames[0].columns) - set(frames[1].columns))
            only_test = sorted(set(frames[1].columns) - set(frames[0].columns))
            if only_train or only_test:
                raise DataError(
                    f"train and test columns differ: train only {only_train}, test only {only_test}"
                )
            frames[1] = frames[1].reindex(columns=frames[0].columns)
    from_test = np.concatenate(
        [np.zeros(len(frames[0]), bool), np.ones(len(frames[1]) if len(frames) > 1 else 0, bool)]
    )
    prefixes = ["train", "test"]
    ids = []
    for k, f in enumerate(frames):
        if spec.id_column is not None:
            if spec.id_column not in f.columns:
                raise DataError(f"id_column '{spec.id_column}' not in {files[k]}")
            ids.append(f[spec.id_column].astype(str).to_numpy())
        else:
            ids.append(np.asarray([f"{prefixes[k]}:{i}" for i in range(len(f))], dtype=object))
    df = pd.concat(frames, ignore_index=True)
    row_id = np.concatenate(ids)
    if len(set(row_id.tolist())) != len(row_id):
        raise DataError("row ids are not unique; set dataset.id_column to a unique column or remove it")
    notes: list[str] = []
    types, raw_types = _types(df, spec, notes)
    y = _label(df, spec, types)
    if spec.label is not None and types is not None:
        clash = int(
            ((types == spec.normal_type) & (y == 1)).sum() + ((types != spec.normal_type) & (y == 0)).sum()
        )
        if clash:
            notes.append(
                f"{clash} rows have a label that disagrees with their type (label is used for the verdict)"
            )
    drop = set(spec.drop) | {c for c in (spec.label, spec.type, spec.id_column) if c is not None}
    missing = sorted(set(spec.drop) - set(df.columns))
    if missing:
        raise DataError(f"dataset.drop names columns that do not exist: {missing}")
    X = df.drop(columns=[c for c in df.columns if c in drop])
    if X.shape[1] == 0:
        raise DataError("no feature columns left after dropping label, type, id and drop columns")
    return Table(
        X=X,
        y=y,
        types=types,
        row_id=row_id,
        from_test_file=from_test,
        files=files,
        notes=notes,
        raw_types=raw_types,
    )


def _types(
    df: pd.DataFrame, spec: DatasetSpec, notes: list[str]
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """(mapped types, raw types), or (None, None) without a type column."""
    if spec.type is None:
        return None, None
    if spec.type not in df.columns:
        raise DataError(f"type column '{spec.type}' not in the data")
    col = df[spec.type]
    n_missing = int(col.isna().sum())
    if n_missing:
        if spec.type_fill is None:
            raise DataError(f"{n_missing} rows have no type; set dataset.type_fill to name them")
        notes.append(f"{n_missing} missing types filled with '{spec.type_fill}'")
        col = col.fillna(spec.type_fill)
    raw = col.astype(str).str.strip()
    mapped = raw.map(lambda v: spec.type_map.get(v, v))
    unused = sorted(set(spec.type_map) - set(raw.unique()))
    if unused:
        notes.append(f"type_map keys not present in the data: {unused}")
    return mapped.to_numpy(dtype=object), raw.to_numpy(dtype=object)


def _label(df: pd.DataFrame, spec: DatasetSpec, types: np.ndarray | None) -> np.ndarray:
    if types is not None and not (types == spec.normal_type).any():
        raise DataError(f"normal_type '{spec.normal_type}' does not occur in column '{spec.type}'")
    if spec.label is None:
        if types is None:
            raise DataError("dataset needs a label or a type column")
        return (types != spec.normal_type).astype(np.int8)
    if spec.label not in df.columns:
        raise DataError(f"label column '{spec.label}' not in the data")
    col = df[spec.label]
    if col.isna().any():
        raise DataError(f"label column '{spec.label}' has missing values")
    if col.dtype == bool:
        return col.to_numpy().astype(np.int8)
    values = set(pd.unique(col).tolist())
    if not values <= {0, 1}:
        raise DataError(
            f"label column '{spec.label}' must hold 0/1 (1 = attack); found {sorted(map(str, values))}"
        )
    return col.to_numpy().astype(np.int8)


def _strata(table: Table, idx: np.ndarray) -> np.ndarray:
    if table.types is not None:
        return table.types[idx].astype(str)
    return table.y[idx].astype(str)


def stratified_cut(
    idx: np.ndarray, labels: np.ndarray, fraction: float, *, seed: int, log: list[str], what: str
) -> tuple[np.ndarray, np.ndarray]:
    """Split ``idx`` into (part, rest) with ``part`` holding ``fraction`` of the rows.

    Strata with fewer than two rows are merged into one rare stratum for this cut
    only; if that stratum is still too small its rows join the largest stratum
    (a random split for them). Every change is written to ``log``.
    """
    lab = labels.astype(object).copy()
    values, counts = np.unique(lab.astype(str), return_counts=True)
    small = values[counts < 2]
    if len(small):
        lab[np.isin(lab.astype(str), small)] = RARE
        log.append(f"{what}: strata {small.tolist()} have fewer than 2 rows; merged for splitting only")
        if int((lab == RARE).sum()) < 2:
            big = values[np.argmax(counts)]
            lab[lab == RARE] = big
            log.append(f"{what}: merged stratum still too small; its rows are split at random")
    try:
        rest, part = train_test_split(idx, test_size=fraction, stratify=lab.astype(str), random_state=seed)
    except ValueError as e:
        log.append(f"{what}: stratified split impossible ({e}); split at random")
        rest, part = train_test_split(idx, test_size=fraction, random_state=seed)
    return np.sort(np.asarray(part)), np.sort(np.asarray(rest))


def make_splits(
    table: Table,
    protocol: Protocol,
    test_fraction: float | None,
    seed: int,
    exclude: np.ndarray | None = None,
) -> Splits:
    """Splits for one seed. ``exclude`` (a mask over the table) removes rows from the train partition
    after the TEST cut and the train subsample, so TEST and the kept rows do not depend on it."""
    log: list[str] = []
    everything = np.arange(len(table))
    if test_fraction is None:
        test = everything[table.from_test_file]
        pool = everything[~table.from_test_file]
    else:
        test, pool = stratified_cut(
            everything, _strata(table, everything), test_fraction, seed=seed, log=log, what="TEST cut"
        )
    sub = protocol.subsample
    if sub.get("train") and len(pool) > sub["train"]:
        frac = sub["train"] / len(pool)
        pool, _ = stratified_cut(
            pool, _strata(table, pool), frac, seed=seed + 101, log=log, what="train subsample"
        )
        log.append(f"train partition subsampled to {len(pool)} rows")
    if sub.get("test") and len(test) > sub["test"]:
        rng = np.random.default_rng(seed + 202)
        test = np.sort(rng.choice(test, size=sub["test"], replace=False))
        log.append(f"TEST subsampled at random (labels not used) to {len(test)} rows")
    if exclude is not None:
        removed = int(exclude[pool].sum())
        pool = pool[~exclude[pool]]
        log.append(f"{removed} rows removed from the train partition before splitting")
    val, fit = stratified_cut(
        pool, _strata(table, pool), protocol.val_fraction, seed=seed + 1, log=log, what="VAL cut"
    )
    cal, fit_base = stratified_cut(
        fit, _strata(table, fit), protocol.cal_fraction, seed=seed + 2, log=log, what="CAL cut"
    )
    for name, part in (("FIT-base", fit_base), ("CAL", cal), ("VAL", val), ("TEST", test)):
        if len(part) == 0:
            raise DataError(f"split {name} is empty for seed {seed}; the dataset is too small")
    return Splits(seed=seed, fit_base=fit_base, cal=cal, val=val, test=test, log=log)


def file_fingerprint(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
