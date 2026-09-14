"""P1/P2 — cell observations -> candidate tower positions.

Exact (radio, cell) match first; for LTE fall back to the eNodeB (cellId >> 8):
sibling cells of the same eNodeB sit on the same mast, so their centroid is a
fair position with a wider radius. 35% -> 56% of distinct ids resolve this way.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from . import paths

RADIO_MAP = {"LTE": "LTE", "WCDMA": "UMTS", "GSM": "GSM"}  # NR has no reference data
ENB_FALLBACK_MIN_RADIUS_M = 1500.0


@dataclass
class TowerHit:
    lon: float
    lat: float
    radius_m: float
    weight: float
    how: str  # "exact" | "enb"


@lru_cache(maxsize=1)
def _ref() -> pd.DataFrame:
    df = pd.read_csv(paths.CELLS_CSV, usecols=["radio", "net", "cell", "lon", "lat", "range", "samples"])
    df["enb"] = df["cell"] // 256
    return df


def load_cell_samples(leg_id: str) -> pd.DataFrame:
    con = sqlite3.connect(paths.leg_dir(leg_id) / "sensors.db")
    df = pd.read_sql("select epochMillis, cellId, networkType, rssiDbm, isRegistered from cell_samples", con)
    df["cell"] = pd.to_numeric(df["cellId"], errors="coerce")
    return df.dropna(subset=["cell"]).astype({"cell": "int64"})


def resolve(cell: int, network_type: str) -> list[TowerHit]:
    radio = RADIO_MAP.get(network_type)
    if radio is None:
        return []
    ref = _ref()
    sub = ref[(ref.radio == radio) & (ref.cell == cell)]
    if len(sub):
        w = sub["samples"].clip(lower=1).to_numpy(dtype=float)
        w /= w.sum()
        return [TowerHit(r.lon, r.lat, max(float(r.range), 300.0), float(wi), "exact")
                for r, wi in zip(sub.itertuples(), w)]
    if radio == "LTE":
        sib = ref[(ref.radio == "LTE") & (ref.enb == cell // 256)]
        if len(sib):
            w = sib["samples"].clip(lower=1).to_numpy(dtype=float)
            lon = float(np.average(sib.lon, weights=w))
            lat = float(np.average(sib.lat, weights=w))
            spread = float(np.hypot((sib.lon - lon) * 70000, (sib.lat - lat) * 110540).max())
            return [TowerHit(lon, lat, max(ENB_FALLBACK_MIN_RADIUS_M, spread + float(sib["range"].median())), 1.0, "enb")]
    return []


def leg_tower_hits(leg_id: str) -> pd.DataFrame:
    """One row per (epochMillis, candidate tower) for every resolvable cell sample."""
    samples = load_cell_samples(leg_id)
    if samples.empty:
        return pd.DataFrame(columns=["epochMillis", "cell", "rssiDbm", "lon", "lat", "radius_m", "weight", "how"])
    cache: dict[tuple[int, str], list[TowerHit]] = {}
    rows = []
    for r in samples.itertuples():
        key = (r.cell, r.networkType)
        if key not in cache:
            cache[key] = resolve(*key)
        for h in cache[key]:
            rows.append((r.epochMillis, r.cell, r.rssiDbm, h.lon, h.lat, h.radius_m, h.weight, h.how))
    return pd.DataFrame(rows, columns=["epochMillis", "cell", "rssiDbm", "lon", "lat", "radius_m", "weight", "how"])
