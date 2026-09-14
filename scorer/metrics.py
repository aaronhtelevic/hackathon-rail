"""
Pure scoring functions for one leg submission against its ground truth.
No file I/O here - see scorer.py for loading/CLI/reporting.

Self-contained (no dependency on the organizer's internal toolkit) - this is
the exact same logic the organizer's copy uses, just packaged so it only
ever needs datasets/practice/, which you already have.
"""
import re

import numpy as np
import pandas as pd

MAX_INTERP_GAP_S = 60.0  # must match how ground truth was built - gaps longer than this (tunnels) are left undefined, not interpolated
STATION_CALL_TOLERANCE_S = 90.0  # how far from the true reference moment a call can land and still count
FIRST_FIX_ACCURACY_THRESHOLD_M = 1000.0  # a fix only "counts" once it's within this of the truth
STATION_APPROACH_DISTANCE_M = 500.0  # the "predict when you're X out" reference point (see PARTICIPANT_BRIEF.md)


def normalize_name(s: str) -> str:
    s = s.lower().strip()
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def interpolate_ground_truth(gt_df: pd.DataFrame, timestamps_ms) -> list[float | None]:
    """For each timestamp, interpolate distanceAlongTrackM between the two
    bracketing ground-truth rows - unless that gap exceeds MAX_INTERP_GAP_S,
    in which case ground truth is genuinely undefined there (tunnel), and we
    return None rather than silently extrapolating across it."""
    gt = gt_df.sort_values("epochMillis").reset_index(drop=True)
    t = gt["epochMillis"].to_numpy()
    d = gt["distanceAlongTrackM"].to_numpy()
    out = []
    for ts in timestamps_ms:
        if ts < t[0] or ts > t[-1]:
            out.append(None)
            continue
        if ts == t[0]:
            out.append(float(d[0]))
            continue
        if ts == t[-1]:
            out.append(float(d[-1]))
            continue
        i = int(np.searchsorted(t, ts, side="right") - 1)
        i = max(0, min(i, len(t) - 2))
        gap_s = (t[i + 1] - t[i]) / 1000.0
        if gap_s > MAX_INTERP_GAP_S:
            out.append(None)
            continue
        frac = (ts - t[i]) / (t[i + 1] - t[i])
        out.append(float(d[i] + frac * (d[i + 1] - d[i])))
    return out


def position_accuracy(submission_df: pd.DataFrame, gt_df: pd.DataFrame) -> dict:
    """Steady-state tracking error: median/mean |predicted - true| distance
    along track, over every submitted row where ground truth is defined."""
    sub = submission_df.dropna(subset=["distanceAlongTrackM"]).sort_values("epochMillis")
    truth = interpolate_ground_truth(gt_df, sub["epochMillis"].to_numpy())
    errors = [abs(p - t) for p, t in zip(sub["distanceAlongTrackM"], truth) if t is not None]
    n_scoreable = sum(1 for t in truth if t is not None)
    return {
        "nSubmittedRows": int(len(sub)),
        "nScoreableRows": n_scoreable,
        "coveragePct": round(100 * n_scoreable / len(sub), 1) if len(sub) else 0.0,
        "medianErrorM": round(float(np.median(errors)), 1) if errors else None,
        "meanErrorM": round(float(np.mean(errors)), 1) if errors else None,
        "maxErrorM": round(float(np.max(errors)), 1) if errors else None,
    }


def first_fix(submission_df: pd.DataFrame, gt_df: pd.DataFrame, leg_t_from_ms: int) -> dict:
    """Cold-start only: time until the submission first lands within
    FIRST_FIX_ACCURACY_THRESHOLD_M of the truth - a wildly-wrong early row
    doesn't count as a "fix" yet, per the participant brief. Moot for
    warm-start (position given at t=0)."""
    sub = submission_df.dropna(subset=["distanceAlongTrackM"]).sort_values("epochMillis")
    if sub.empty:
        return {"timeToFirstFixS": None, "firstFixErrorM": None}
    truth = interpolate_ground_truth(gt_df, sub["epochMillis"].to_numpy())
    for (_, row), t in zip(sub.iterrows(), truth):
        if t is None:
            continue
        err = abs(row["distanceAlongTrackM"] - t)
        if err <= FIRST_FIX_ACCURACY_THRESHOLD_M:
            return {
                "timeToFirstFixS": round((row["epochMillis"] - leg_t_from_ms) / 1000.0, 1),
                "firstFixErrorM": round(err, 1),
            }
    return {"timeToFirstFixS": None, "firstFixErrorM": None}  # never got within threshold


def find_time_at_distance(gt_df: pd.DataFrame, target_distance_m: float) -> float | None:
    """Inverse of interpolate_ground_truth: the timestamp at which ground
    truth crosses a given arc-length distance. None if that distance is
    never reached within the recorded ground truth (e.g. it falls inside a
    GPS-denied gap, or beyond what was ever recorded)."""
    gt = gt_df.sort_values("epochMillis").reset_index(drop=True)
    d = gt["distanceAlongTrackM"].to_numpy()
    t = gt["epochMillis"].to_numpy()
    if len(d) == 0:
        return None
    if target_distance_m <= d[0]:
        return float(t[0])
    if target_distance_m >= d[-1]:
        return None
    i = int(np.searchsorted(d, target_distance_m, side="right") - 1)
    i = max(0, min(i, len(d) - 2))
    seg = d[i + 1] - d[i]
    frac = 0.0 if seg <= 0 else (target_distance_m - d[i]) / seg
    return float(t[i] + frac * (t[i + 1] - t[i]))


def route_discovery(submission_df: pd.DataFrame, true_route: str, leg_t_from_ms: int) -> dict:
    """Final guess correctness + "lock-in" time: the earliest row after
    which the guess never changes again, so late flip-flopping doesn't get
    credit for an earlier lucky guess."""
    if "routeGuess" not in submission_df.columns:
        return {"finalGuess": None, "correct": False, "lockInTimeS": None}
    guesses = submission_df.dropna(subset=["routeGuess"]).sort_values("epochMillis")
    if guesses.empty:
        return {"finalGuess": None, "correct": False, "lockInTimeS": None}

    final_guess = guesses.iloc[-1]["routeGuess"]
    correct = normalize_name(str(final_guess)) == normalize_name(str(true_route))

    lock_in_time_s = None
    if correct:
        values = guesses["routeGuess"].to_numpy()
        times = guesses["epochMillis"].to_numpy()
        # walk backwards from the end while the guess still matches the final one
        i = len(values) - 1
        while i > 0 and normalize_name(str(values[i - 1])) == normalize_name(str(final_guess)):
            i -= 1
        lock_in_time_s = round((times[i] - leg_t_from_ms) / 1000.0, 1)

    return {"finalGuess": str(final_guess), "correct": bool(correct), "lockInTimeS": lock_in_time_s}


def station_detection(station_calls_df: pd.DataFrame | None, true_station: str,
                       gt_df: pd.DataFrame, route_length_m: float | None) -> dict:
    """Did they call the correct arrival station within tolerance of the
    "500m out" reference moment (STATION_APPROACH_DISTANCE_M before the
    leg's end - see PARTICIPANT_BRIEF.md: "predict when you're 500m out")?

    Only the participant's FIRST call (chronologically) is scored - per the
    brief's "only predict it once": submitting many guesses and hoping one
    lands in the tolerance window earns no extra credit, so there's no
    incentive to spam calls. `nCallsInLeg` is still reported so you can see
    if you did anyway.
    """
    if station_calls_df is None or station_calls_df.empty:
        return {"detected": False, "matchedGuess": None, "timingErrorS": None,
                "nCallsInLeg": 0, "referenceTimeMs": None}

    calls = station_calls_df.sort_values("epochMillis").reset_index(drop=True)
    first_call = calls.iloc[0]
    n_calls = int(len(calls))

    true_norm = normalize_name(true_station)
    guess_norm = normalize_name(str(first_call["stationNameGuess"]))
    name_ok = true_norm in guess_norm or guess_norm in true_norm

    reference_time_ms = None
    if route_length_m is not None and gt_df is not None and not gt_df.empty:
        target_distance = max(0.0, route_length_m - STATION_APPROACH_DISTANCE_M)
        reference_time_ms = find_time_at_distance(gt_df, target_distance)

    if reference_time_ms is None or not name_ok:
        return {
            "detected": False, "matchedGuess": str(first_call["stationNameGuess"]),
            "timingErrorS": None, "nCallsInLeg": n_calls, "referenceTimeMs": reference_time_ms,
        }

    timing_error_s = abs(first_call["epochMillis"] - reference_time_ms) / 1000.0
    detected = timing_error_s <= STATION_CALL_TOLERANCE_S
    return {
        "detected": bool(detected), "matchedGuess": str(first_call["stationNameGuess"]),
        "timingErrorS": round(timing_error_s, 1), "nCallsInLeg": n_calls,
        "referenceTimeMs": reference_time_ms,
    }
