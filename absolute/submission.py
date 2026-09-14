"""I4 — write position.csv / station_calls.csv in the scorer's exact layout."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import paths


def submission_dir(team: str, track: str, leg_id: str, root: Path | None = None) -> Path:
    d = (root or paths.WORK / "submissions") / team / track / leg_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_position(d: Path, t_ms, lon, lat, route_guess: str | None = None, route_guess_from_ms: int | None = None) -> Path:
    df = pd.DataFrame({"epochMillis": np.asarray(t_ms, dtype="int64"),
                       "latitude": np.round(np.asarray(lat, float), 7),
                       "longitude": np.round(np.asarray(lon, float), 7)})
    if route_guess is not None:
        start = route_guess_from_ms if route_guess_from_ms is not None else int(df.epochMillis.min())
        df["routeGuess"] = np.where(df.epochMillis >= start, route_guess, None)
    p = d / "position.csv"
    df.to_csv(p, index=False)
    return p


def append_position(d: Path, t_ms, lon, lat, route_guess: str | None) -> Path:
    """Append newly-emitted rows to position.csv as they're produced, so the file
    grows during the run instead of appearing once at the end. `route_guess` is
    whatever the live guess is *at write time* — causal, no backfill."""
    df = pd.DataFrame({"epochMillis": np.asarray(t_ms, dtype="int64"),
                       "latitude": np.round(np.asarray(lat, float), 7),
                       "longitude": np.round(np.asarray(lon, float), 7),
                       "routeGuess": route_guess})
    p = d / "position.csv"
    header = not p.exists()
    df.to_csv(p, mode="a", header=header, index=False)
    return p


def write_station_calls(d: Path, calls: list[tuple[int, str]]) -> Path:
    p = d / "station_calls.csv"
    pd.DataFrame(calls, columns=["epochMillis", "stationNameGuess"]).to_csv(p, index=False)
    return p
