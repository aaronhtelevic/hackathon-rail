"""Absolute-only warm-start solver — the lane's end-to-end baseline.

Inputs allowed on the warm track: start station name + coordinates + start
time (that is what the organizers hand over), plus the sensor time span.
Nothing else from meta.json. Steps:
  1. GTFS: trips leaving the start station around t0 -> next call = destination, routeGuess.
  2. OSM: route from start station to destination -> polyline + length L.
  3. Motion profile: trapezoid (accelerate / cruise / brake) spanning the sensor window, area L.
  4. Emit lat/lon every EMIT_S, one 500 m-out station call, routeGuess from row 0.
The motion lane's shape.json replaces step 3 once hydration lands.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import anchors, gtfs, paths, submission, track
from .stations import Station, load as load_stations

EMIT_S = 5
ACCEL_MPS2 = 0.45      # typical EMU service acceleration / braking
APPROACH_M = 500.0     # scorer/metrics.STATION_APPROACH_DISTANCE_M
# Measured on good practice legs: train starts rolling ~85 s after scheduled departure
# (platform dwell is inside the leg) and the recording runs ~30 s past arrival.
DWELL_AFTER_SCHED_S = 85.0
TAIL_AFTER_ARRIVAL_S = 30.0


@dataclass
class WarmStart:
    station_name: str
    lon: float
    lat: float
    t0_ms: int


def warm_from_meta(leg_id: str) -> WarmStart:
    """Only the fields the warm track gives you. Dev convenience; scoring legs will hand these over."""
    m = json.loads((paths.leg_dir(leg_id) / "meta.json").read_text(encoding="utf-8"))
    return WarmStart(m["stationFrom"], m["coordFrom"][0], m["coordFrom"][1], int(m["tFromEpochMillis"]))


def sensor_span_ms(leg_id: str) -> tuple[int, int]:
    con = sqlite3.connect(paths.leg_dir(leg_id) / "sensors.db")
    lo, hi = con.execute("select min(epochMillis), max(epochMillis) from accel_samples").fetchone()
    return int(lo), int(hi)


def trapezoid_distance(t_s: np.ndarray, T: float, L: float, a: float = ACCEL_MPS2) -> np.ndarray:
    """Distance covered at times t_s in [0, T] for accel a, cruise, brake a, total L over T."""
    disc = a * a * T * T - 4 * a * L
    if disc < 0:  # can't reach L in T even with a triangle at accel a -> scale a up
        a = 4 * L / (T * T) + 1e-9
        disc = 0.0
    v = (a * T - np.sqrt(disc)) / 2          # cruise speed
    ta = v / a                                # accel (= brake) time
    t = np.clip(t_s, 0, T)
    d = np.where(t < ta, 0.5 * a * t ** 2,
         np.where(t < T - ta, 0.5 * a * ta ** 2 + v * (t - ta),
                  L - 0.5 * a * (T - t) ** 2))
    return d


def motion_window(leg_id: str, t0_ms: int, dep_ms: int | None, t_hi_ms: int) -> tuple[int, int]:
    """[start rolling, stop rolling] in epoch ms. Prefers the motion lane's shape.json
    (first `moving: true` segment) when it exists; otherwise the schedule-based prior."""
    t_move = max(t0_ms, (dep_ms or t0_ms) + int(DWELL_AFTER_SCHED_S * 1000))
    shape = paths.WORK / leg_id / "shape.json"
    if shape.exists():
        try:
            segs = json.loads(shape.read_text(encoding="utf-8")).get("segments", [])
            moving = [s for s in segs if s.get("moving")]
            if moving:
                t_move = int(moving[0]["t_start"])
                t_stop = int(moving[-1]["t_end"])
                return t_move, min(t_stop, t_hi_ms)
        except (ValueError, KeyError):
            pass
    t_stop = t_hi_ms - int(TAIL_AFTER_ARRIVAL_S * 1000)
    if t_stop - t_move < 30_000:  # degenerate window: fall back to the raw span
        t_move, t_stop = t0_ms, t_hi_ms
    return t_move, t_stop


ANCHOR_COST_PER_M = 0.2    # seconds of cost per metre (median) an anchor sits outside its radius from the path
ANCHOR_RERANK_TOP = 6      # only re-rank the timing shortlist; a path per candidate costs a dijkstra


def anchor_misfit_m(path: track.Path, anchor_doc: dict, t_from_ms: int) -> float:
    """Median over anchors (after t_from) of how far the best candidate tower sits
    outside its radius from the path. 0 = every anchor is consistent with this route."""
    miss = []
    for a in anchor_doc["anchors"]:
        if a["source"] != "cell" or a["t"] < t_from_ms:
            continue
        best = min(max(0.0, path.project(c["lon"], c["lat"])[1] - c["radius_m"]) for c in a["candidates"])
        miss.append(best)
    return float(np.median(miss)) if miss else 0.0  # median: one bad eNB centroid must not dominate


def choose_hop(ws: WarmStart, observed_duration_s: float, leg_id: str | None = None,
               g: track.RailGraph | None = None) -> tuple[gtfs.Hop | None, list, dict[int, track.Path]]:
    """Timing shortlist from GTFS, then re-rank the top few by cell-anchor consistency
    with each candidate's OSM path — the timing model cannot tell opposite directions apart,
    towers can. Returns (best, ranked, paths-by-trip_pk)."""
    reg = load_stations()
    st = reg.by_name(ws.station_name) or reg.nearest(ws.lon, ws.lat)[0][0]
    ranked = gtfs.rank_hops(gtfs.hops_from(st, ws.t0_ms), ws.t0_ms, observed_duration_s)
    paths: dict[int, track.Path] = {}
    if not ranked or leg_id is None or g is None:
        return (ranked[0][0] if ranked else None), ranked, paths
    doc = anchors.build(leg_id)
    if not any(a["source"] == "cell" for a in doc["anchors"]):
        return ranked[0][0], ranked, paths
    # anchors from the second half of the leg point at the destination, not the shared start
    t_half = ws.t0_ms + int(observed_duration_s * 500)
    rescored = []
    for h, cost in ranked[:ANCHOR_RERANK_TOP]:
        key = (h.to.uic or h.to.name)
        pth = paths.get(key) or g.route(ws.lon, ws.lat, h.to.lon, h.to.lat)
        if pth is None:
            rescored.append((h, cost + 600.0))
            continue
        paths[key] = pth
        rescored.append((h, cost + ANCHOR_COST_PER_M * anchor_misfit_m(pth, doc, t_half)))
    rescored.sort(key=lambda x: x[1])
    ranked = rescored + ranked[ANCHOR_RERANK_TOP:]
    return ranked[0][0], ranked, paths


def solve_warm(leg_id: str, team: str, ws: WarmStart | None = None, out_root: Path | None = None) -> dict:
    ws = ws or warm_from_meta(leg_id)
    t_lo, t_hi = sensor_span_ms(leg_id)
    T = (t_hi - ws.t0_ms) / 1000.0
    g = track.load()
    hop, ranked, cand_paths = choose_hop(ws, T, leg_id, g)
    info = {"leg": leg_id, "hop": None, "path_len_m": None, "n_candidates": len(ranked)}

    if hop is None:
        dest: Station | None = None
        path = None
    else:
        dest = hop.to
        path = cand_paths.get(dest.uic or dest.name) or g.route(ws.lon, ws.lat, dest.lon, dest.lat)
        info["hop"] = f"{hop.route_guess} {hop.frm.name}->{dest.name} dep {hop.dep_ms} sched {hop.scheduled_duration_s:.0f}s"

    d = submission.submission_dir(team, "warm", leg_id, out_root)
    t_ms = np.arange(ws.t0_ms, t_hi + 1, EMIT_S * 1000, dtype="int64")

    if path is None:
        # no route: stand still at the start — still a valid, scoreable file
        lon = np.full(len(t_ms), ws.lon); lat = np.full(len(t_ms), ws.lat)
        submission.write_position(d, t_ms, lon, lat, hop.route_guess if hop else None)
        submission.write_station_calls(d, [])
        return info

    L = path.length_m
    info["path_len_m"] = round(L, 1)
    t_move, t_stop = motion_window(leg_id, ws.t0_ms, hop.dep_ms, t_hi)
    Tm = (t_stop - t_move) / 1000.0
    info["window"] = f"+{(t_move - ws.t0_ms) / 1000:.0f}s .. +{(t_stop - ws.t0_ms) / 1000:.0f}s"
    dist = trapezoid_distance((t_ms - t_move) / 1000.0, Tm, L)
    lonlat = path.at_distance(dist)
    submission.write_position(d, t_ms, lonlat[:, 0], lonlat[:, 1], hop.route_guess)

    # 500 m-out call: the instant the profile crosses L - 500
    fine_t = np.arange(0, Tm, 0.5)
    fine_d = trapezoid_distance(fine_t, Tm, L)
    i = int(np.searchsorted(fine_d, max(0.0, L - APPROACH_M)))
    call_ms = int(t_move + fine_t[min(i, len(fine_t) - 1)] * 1000)
    submission.write_station_calls(d, [(call_ms, dest.name)])

    anchors.write(leg_id, anchors.warm_start_anchor(ws.t0_ms, ws.lon, ws.lat), paths.WORK / leg_id)
    return info
