"""I3/I5 — score submissions with the organizers' scorer, in-process, over many legs."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from . import paths

sys.path.insert(0, str(paths.SCORER))
import scorer  # noqa: E402  (organizers' code)


def score(leg_id: str, track: str, sub_dir: Path) -> dict:
    return scorer.score_leg(leg_id, track, sub_dir)


def flatten(rep: dict) -> dict:
    pa, rd, sd, ff = rep["positionAccuracy"], rep["routeDiscovery"], rep["stationDetection"], rep.get("firstFix", {})
    return {
        "leg": rep["legId"], "gtq": rep.get("groundTruthQuality"),
        "medErrM": pa["medianErrorM"], "meanErrM": pa["meanErrorM"], "maxErrM": pa["maxErrorM"], "cov%": pa["coveragePct"],
        "route": rd["finalGuess"], "routeOK": rd["correct"], "lockInS": rd["lockInTimeS"],
        "stationOK": sd["detected"], "stTimingS": sd["timingErrorS"], "stGuess": sd["matchedGuess"],
        "ttffS": ff.get("timeToFirstFixS"), "ffErrM": ff.get("firstFixErrorM"),
    }


def table(team: str, track: str, legs: list[str], root: Path | None = None) -> pd.DataFrame:
    rows = []
    for leg in legs:
        d = (root or paths.WORK / "submissions") / team / track / leg
        if not (d / "position.csv").exists():
            rows.append({"leg": leg, "error": "no submission"})
            continue
        try:
            rows.append(flatten(score(leg, track, d)))
        except Exception as e:  # keep going; one bad leg shouldn't hide the rest
            rows.append({"leg": leg, "error": f"{type(e).__name__}: {e}"})
    return pd.DataFrame(rows)


def summary(df: pd.DataFrame) -> str:
    good = df[df.get("gtq", pd.Series(dtype=object)) == "good"] if "gtq" in df else df
    lines = [f"legs scored: {df.get('medErrM', pd.Series(dtype=float)).notna().sum()}/{len(df)}  (good-GT legs: {len(good)})"]
    if "medErrM" in good:
        lines.append(f"position  median-of-medians {good.medErrM.median():.0f} m   mean-of-medians {good.medErrM.mean():.0f} m   worst median {good.medErrM.max():.0f} m")
    if "routeOK" in df:
        ok = df.routeOK.fillna(False).astype(bool)
        lines.append(f"route     correct {ok.sum()}/{len(df)}   median lock-in {df.loc[ok, 'lockInS'].median():.0f} s")
    if "stationOK" in df:
        s = df.stationOK.fillna(False).astype(bool)
        lines.append(f"station   detected {s.sum()}/{len(df)}   median |timing| {df.stTimingS.median():.0f} s")
    if "ttffS" in df and df.ttffS.notna().any():
        lines.append(f"firstfix  median TTFF {df.ttffS.median():.0f} s   median err {df.ffErrM.median():.0f} m")
    return "\n".join(lines)
