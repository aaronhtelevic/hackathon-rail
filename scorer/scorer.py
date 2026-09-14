"""
Score one of YOUR OWN submissions for one practice leg against its answer key.

Usage:
    python scorer.py --leg-id <leg_id> --track cold|warm --submission-dir <path-to-your-output>

`submission-dir` must contain `position.csv` (epochMillis + either
distanceAlongTrackM or latitude/longitude + optional routeGuess) and,
optionally, `station_calls.csv` (epochMillis, stationNameGuess).

This only ever scores against `datasets/practice/<leg_id>/` - the one place
you actually have a real ground_truth.csv. It's the same logic the
organizers' copy uses for blind/scoring legs; those answer keys aren't in
this package (obviously) so this can't ever look at them, by construction,
not just by convention.

NOTE - a heads-up from the organizers: running this against practice legs
you already have answers for is a great way to sanity-check your pipeline,
but it's easy to over-tune to these specific 50 legs in a way that doesn't
generalize to the blind/scoring legs you'll actually be judged on. Treat a
great practice-set score as informative, not as a guarantee.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics

SCORER_DIR = Path(__file__).resolve().parent
DATASETS_DIR = SCORER_DIR.parent / "datasets"
POLYLINES_DIR = SCORER_DIR / "polylines"

M_PER_DEG_LAT = 110540.0


def _m_per_deg_lon(lat):
    return 111320.0 * np.cos(np.radians(lat))


def _polyline_to_local_xy(polyline, lat_center):
    mlon = _m_per_deg_lon(lat_center)
    return np.array([[lon * mlon, lat * M_PER_DEG_LAT] for lon, lat in polyline])


def _cumulative_lengths(xy):
    seg = np.diff(xy, axis=0)
    seg_len = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    return cum, seg_len


def _project_point(px, py, xy, cum, seg_len):
    """Nearest-segment projection of one point onto the polyline. Returns
    (distance_along_track_m, lateral_offset_m)."""
    best_dist_along, best_perp = None, float("inf")
    for i in range(len(xy) - 1):
        ax, ay = xy[i]
        bx, by = xy[i + 1]
        dx, dy = bx - ax, by - ay
        length = seg_len[i]
        if length == 0:
            continue
        t = ((px - ax) * dx + (py - ay) * dy) / (length * length)
        t = max(0.0, min(1.0, t))
        cx, cy = ax + t * dx, ay + t * dy
        perp = np.hypot(px - cx, py - cy)
        if perp < best_perp:
            best_perp = perp
            best_dist_along = cum[i] + t * length
    return best_dist_along


def find_leg_dir(leg_id: str) -> Path:
    candidate = DATASETS_DIR / "practice" / leg_id
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"No practice leg found at {candidate}. This scorer only works against "
        f"datasets/practice/<leg_id> - it can't score blind or scoring legs "
        f"(you don't have their answer keys, and neither does this package).")


def load_polyline(leg_id: str):
    path = POLYLINES_DIR / f"{leg_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["polyline"]


def project_lonlat_to_distance(df: pd.DataFrame, polyline) -> pd.Series:
    lat_center = float(np.mean([lat for _, lat in polyline]))
    xy_line = _polyline_to_local_xy(polyline, lat_center)
    cum, seg_len = _cumulative_lengths(xy_line)
    mlon = _m_per_deg_lon(lat_center)
    dists = []
    for _, row in df.iterrows():
        px, py = row["longitude"] * mlon, row["latitude"] * M_PER_DEG_LAT
        dists.append(_project_point(px, py, xy_line, cum, seg_len))
    return pd.Series(dists, index=df.index)


def load_submission(submission_dir: Path, leg_id: str) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    pos_path = submission_dir / "position.csv"
    if not pos_path.exists():
        raise FileNotFoundError(f"{pos_path} not found - every submission needs position.csv")
    pos = pd.read_csv(pos_path)
    if "epochMillis" not in pos.columns:
        raise ValueError("position.csv must have an epochMillis column")

    if "distanceAlongTrackM" not in pos.columns:
        if {"latitude", "longitude"}.issubset(pos.columns):
            polyline = load_polyline(leg_id)
            if polyline is None:
                raise FileNotFoundError(
                    f"No polyline available for {leg_id!r} to project lat/lon - "
                    f"submit distanceAlongTrackM directly for this leg instead.")
            pos["distanceAlongTrackM"] = project_lonlat_to_distance(pos, polyline)
        else:
            raise ValueError("position.csv needs either distanceAlongTrackM or latitude+longitude")

    calls_path = submission_dir / "station_calls.csv"
    calls = pd.read_csv(calls_path) if calls_path.exists() else None
    if calls is not None and not {"epochMillis", "stationNameGuess"}.issubset(calls.columns):
        raise ValueError("station_calls.csv must have epochMillis and stationNameGuess columns")

    return pos, calls


def score_leg(leg_id: str, track: str, submission_dir: Path) -> dict:
    if track not in ("cold", "warm"):
        raise ValueError("--track must be 'cold' or 'warm'")

    leg_dir = find_leg_dir(leg_id)
    meta = json.loads((leg_dir / "meta.json").read_text(encoding="utf-8"))
    if not meta.get("hasGroundTruth"):
        raise ValueError(f"{leg_id!r} has no ground truth to score against")
    gt_df = pd.read_csv(leg_dir / "ground_truth.csv")
    position_df, station_calls_df = load_submission(submission_dir, leg_id)

    report = {
        "legId": leg_id, "track": track, "groundTruthQuality": meta.get("groundTruthQuality"),
        "hasCellData": meta.get("hasCellData"),
        "tFromISO": meta.get("tFromISO"), "tToISO": meta.get("tToISO"),
        "positionAccuracy": metrics.position_accuracy(position_df, gt_df),
        "routeDiscovery": metrics.route_discovery(position_df, meta["lineName"], meta["tFromEpochMillis"]),
        "stationDetection": metrics.station_detection(
            station_calls_df, meta["stationTo"], gt_df, meta.get("routeLengthM")),
    }
    if track == "cold":
        report["firstFix"] = metrics.first_fix(position_df, gt_df, meta["tFromEpochMillis"])
    else:
        report["firstFix"] = {"note": "not applicable in warm-start - position given at t=0"}

    if meta.get("groundTruthQuality") in ("degraded", "bad"):
        report["_warning"] = (
            f"this leg's ground truth is labeled {meta['groundTruthQuality']!r} - "
            f"positionAccuracy here is less trustworthy than on a 'good' leg")

    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leg-id", required=True)
    ap.add_argument("--track", required=True, choices=["cold", "warm"])
    ap.add_argument("--submission-dir", required=True, type=Path)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    report = score_leg(args.leg_id, args.track, args.submission_dir)
    text = json.dumps(report, indent=2)
    print(text)
    if args.json_out:
        args.json_out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
