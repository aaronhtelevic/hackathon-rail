"""H1 — write anchors.json (the absolute lane's half of the data contract).

Schema is frozen in WORKLOG.md -> Data contracts. Empty `anchors` is valid.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import paths
from .cells import leg_tower_hits

ANCHOR_BIN_S = 10  # collapse 1 Hz cell samples into one anchor per bin


def cell_anchors(leg_id: str) -> list[dict]:
    hits = leg_tower_hits(leg_id)
    if hits.empty:
        return []
    hits["bin"] = (hits.epochMillis // (ANCHOR_BIN_S * 1000)).astype("int64")
    anchors = []
    for _, g in hits.groupby("bin"):
        cands = (g.groupby(["lon", "lat"], as_index=False)
                  .agg(radius_m=("radius_m", "min"), weight=("weight", "sum")))
        cands["weight"] /= cands["weight"].sum()
        anchors.append({
            "t": int(g.epochMillis.median()),
            "source": "cell",
            "candidates": [{"lat": float(c.lat), "lon": float(c.lon),
                            "radius_m": float(c.radius_m), "weight": round(float(c.weight), 4)}
                           for c in cands.itertuples()],
        })
    return anchors


def warm_start_anchor(t_ms: int, lon: float, lat: float, radius_m: float = 100.0) -> dict:
    return {"t": int(t_ms), "source": "warm_start",
            "candidates": [{"lat": float(lat), "lon": float(lon), "radius_m": radius_m, "weight": 1.0}]}


def build(leg_id: str, warm: dict | None = None) -> dict:
    anchors = cell_anchors(leg_id)
    if warm is not None:
        anchors.insert(0, warm)
    anchors.sort(key=lambda a: a["t"])
    return {"leg_id": leg_id, "anchors": anchors}


def write(leg_id: str, warm: dict | None = None, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (paths.WORK / leg_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "anchors.json"
    p.write_text(json.dumps(build(leg_id, warm), indent=1), encoding="utf-8")
    return p
