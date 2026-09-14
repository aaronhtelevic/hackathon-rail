"""Absolute-only warm-start solver — the lane's end-to-end baseline.

Inputs allowed on the warm track: start station name + coordinates + start
time (that is what the organizers hand over), plus the sensor time span.
Nothing else from meta.json. Steps:
  1. GTFS: trips leaving the start station around t0 -> next call = destination, routeGuess.
  2. OSM: route from start station to destination -> polyline + length L.
  3. Motion profile: trapezoid (accelerate / cruise / brake) spanning the sensor window, area L.
  3b. Hydration (absolute/hydrate.py): when the motion lane's work/<leg>/shape.json exists,
      the shape's segment boundaries are fitted onto the path (turns, stops, anchors) and
      that distance-along-time curve replaces the trapezoid. Written to work/<leg>/hydrated.json.
  4. Emit lat/lon every EMIT_S, one 500 m-out station call, routeGuess from row 0.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import anchors, gtfs, hydrate, paths, submission, track
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


WARM_KEYS = ("stationFrom", "coordFrom", "tFromEpochMillis")
# The scoring release is built by the same dataset builder as practice, so the warm fields
# almost certainly keep these names (Steven unreachable — assumption recorded in TASKS.md §10).
# Cheap insurance against a rename or a partial hand-over: aliases, and each missing piece
# derived from the others (coords from the station registry, station from the nearest to
# the coords, t0 from the first sensor sample, ISO time strings).
_ALIASES = {
    "stationFrom": ("stationFrom", "startStation", "station_from", "fromStation", "originStation", "stationName"),
    "coordFrom": ("coordFrom", "startCoord", "coord_from", "fromCoord", "startCoordinates", "coord"),
    "tFromEpochMillis": ("tFromEpochMillis", "startEpochMillis", "t_from_epoch_millis", "tFrom", "epochMillisFrom", "startTimeMs"),
}
_ISO_KEYS = ("tFromISO", "startISO", "tFrom", "startTime")
_LABEL_KEYS = ("lineName", "stationTo", "coordTo", "routeLengthM", "tToEpochMillis", "direction")


def _pick(m: dict, key: str):
    return next((m[k] for k in _ALIASES[key] if k in m and m[k] not in (None, "")), None)


def _lonlat(c) -> tuple[float, float] | None:
    if isinstance(c, dict):
        lon = c.get("lon", c.get("longitude")); lat = c.get("lat", c.get("latitude"))
        return (float(lon), float(lat)) if lon is not None and lat is not None else None
    if isinstance(c, (list, tuple)) and len(c) == 2:
        a, b = float(c[0]), float(c[1])
        return (b, a) if a > 49.0 and b < 7.0 else (a, b)   # [lat, lon] given? Belgium: lat ~50-51, lon ~2.5-6.5
    return None


def warm_from_meta(leg_id: str) -> WarmStart | None:
    """Only the warm-track fields (station name, coordinates, start time) are read from meta.json.
    None when the file is missing or nothing usable is in it (cold-only leg)."""
    f = paths.leg_dir(leg_id) / "meta.json"
    if not f.exists():
        return None
    m = json.loads(f.read_text(encoding="utf-8"))
    name = _pick(m, "stationFrom")
    ll = _lonlat(_pick(m, "coordFrom"))
    t0 = _pick(m, "tFromEpochMillis")
    if t0 is None:
        iso = next((m[k] for k in _ISO_KEYS if isinstance(m.get(k), str)), None)
        if iso:
            import datetime as dt
            t0 = int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    if isinstance(t0, str) and t0.isdigit():
        t0 = int(t0)
    if name is None and ll is None:
        return None
    reg = load_stations()
    if ll is None:
        st = reg.by_name(str(name))
        if st is None:
            return None
        ll = (st.lon, st.lat)
    if name is None:
        name = reg.nearest(*ll)[0][0].name
    if t0 is None:
        t0 = sensor_span_ms(leg_id)[0]
    return WarmStart(str(name), ll[0], ll[1], int(t0))


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


def motion_window(t0_ms: int, dep_ms: int | None, t_hi_ms: int) -> tuple[int, int]:
    """[start rolling, stop rolling] in epoch ms from the schedule prior (trapezoid fallback only;
    the shape's own moving flags are consumed by hydrate.py — their first/last `moving` segment
    is flicker-prone and made the trapezoid worse: 324 -> 530 m median when tried)."""
    t_move = max(t0_ms, (dep_ms or t0_ms) + int(DWELL_AFTER_SCHED_S * 1000))
    t_stop = t_hi_ms - int(TAIL_AFTER_ARRIVAL_S * 1000)
    if t_stop - t_move < 30_000:  # degenerate window: fall back to the raw span
        t_move, t_stop = t0_ms, t_hi_ms
    return t_move, t_stop


ANCHOR_COST_PER_M = 0.2    # seconds of cost per metre (median) an anchor sits outside its radius from the path
ANCHOR_RERANK_TOP = 6      # only re-rank the timing shortlist; a path per candidate costs a dijkstra
SHAPE_COST_PER_M = 2.0     # seconds of cost per metre of hydration penalty *per shape segment*: the IMU
                           # turn sequence fitted to each candidate path separates opposite directions
                           # that timing cannot (fixes ic2809_03, ic4112_00; l1679_02 needs > 2.6 while
                           # ic2809_04 breaks above 2.58 — a flat per-metre rate broke both).
NO_PATH_COST_S = 1e6       # a destination we cannot route to on OSM is useless to us


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


def _prior_for(ws: WarmStart, hop: gtfs.Hop, t_hi_ms: int, L: float):
    """Schedule-timed trapezoid as a distance(t) callable — hydration's weak timing prior."""
    t_move, t_stop = motion_window(ws.t0_ms, hop.dep_ms, t_hi_ms)
    Tm = (t_stop - t_move) / 1000.0
    return lambda t: trapezoid_distance((np.asarray(t) - t_move) / 1000.0, Tm, L)


def choose_hop(ws: WarmStart, observed_duration_s: float, leg_id: str | None = None,
               g: track.RailGraph | None = None) -> tuple[gtfs.Hop | None, list, dict[int, track.Path]]:
    """Timing shortlist from GTFS, then re-rank the top few by (a) cell-anchor consistency and
    (b) how well the IMU shape hydrates onto each candidate's OSM path — the timing model cannot
    tell opposite directions apart; towers and the turn sequence can. Returns (best, ranked, paths)."""
    reg = load_stations()
    st = reg.by_name(ws.station_name) or reg.nearest(ws.lon, ws.lat)[0][0]
    ranked = gtfs.rank_hops(gtfs.hops_from(st, ws.t0_ms), ws.t0_ms, observed_duration_s)
    paths: dict[int, track.Path] = {}
    if not ranked or leg_id is None or g is None:
        return (ranked[0][0] if ranked else None), ranked, paths
    doc = anchors.build(leg_id)
    has_cell = any(a["source"] == "cell" for a in doc["anchors"])
    shape = None if os.environ.get("NO_HYDRATE") else hydrate.load_shape(leg_id)
    if not has_cell and shape is None:
        return ranked[0][0], ranked, paths
    # anchors from the second half of the leg point at the destination, not the shared start
    t_half = ws.t0_ms + int(observed_duration_s * 500)
    t_hi = ws.t0_ms + int(observed_duration_s * 1000)
    rescored = []
    for h, cost in ranked[:ANCHOR_RERANK_TOP]:
        key = (h.to.uic or h.to.name)
        pth = paths.get(key) or g.route(ws.lon, ws.lat, h.to.lon, h.to.lat)
        if pth is None:
            rescored.append((h, cost + NO_PATH_COST_S))
            continue
        paths[key] = pth
        if has_cell:
            cost += ANCHOR_COST_PER_M * anchor_misfit_m(pth, doc, t_half)
        if shape is not None:
            try:
                hy = hydrate.hydrate(shape, doc, pth, _prior_for(ws, h, t_hi, pth.length_m))
                cost += SHAPE_COST_PER_M * hy.cost / max(hy.n_segments, 1)
            except ValueError:
                pass
        rescored.append((h, cost))
    rescored.sort(key=lambda x: x[1])
    ranked = rescored + ranked[ANCHOR_RERANK_TOP:]
    return ranked[0][0], ranked, paths


def _ev(gui, stage, msg, **kw):
    if gui is not None:
        gui.event(stage, msg, **kw)


def solve_warm(leg_id: str, team: str, ws: WarmStart | None = None, out_root: Path | None = None,
               gui=None, sub_track: str = "warm") -> dict:
    """Batch solve: needs work/<leg>/shape.json to already exist to hydrate.
    `sub_track` only picks the submission folder: the cold track calls this with a `ws`
    inferred by absolute/cold.py instead of the given one.
    `gui` is an optional web/python/rail_gui.LegWriter: when given, anchors.json,
    hydrated.json, the CSVs and progress events are mirrored into its run directory.

    The live, synchronized version of this is joint/stream.py — same result,
    but shape and anchors arrive as one leg-time stream and the hydrated
    points come out as a third."""
    ws = ws or warm_from_meta(leg_id)
    if ws is None:
        raise ValueError(f"{leg_id}: no warm-start fields in meta.json")
    t_lo, t_hi = sensor_span_ms(leg_id)
    T = (t_hi - ws.t0_ms) / 1000.0
    _ev(gui, "W1", f"warm start {ws.station_name} @ {ws.t0_ms}, sensor span {T:.0f}s", pct=0.05)
    g = track.load()
    hop, ranked, cand_paths = choose_hop(ws, T, leg_id, g)
    info = {"leg": leg_id, "hop": None, "path_len_m": None, "n_candidates": len(ranked)}
    _ev(gui, "R3", f"{len(ranked)} GTFS candidates; top: "
        + ", ".join(f"{h.route_guess}->{h.to.name} ({c:.0f})" for h, c in ranked[:3]), pct=0.3)

    if hop is None:
        dest: Station | None = None
        path = None
    else:
        dest = hop.to
        path = cand_paths.get(dest.uic or dest.name) or g.route(ws.lon, ws.lat, dest.lon, dest.lat)
        info["hop"] = f"{hop.route_guess} {hop.frm.name}->{dest.name} dep {hop.dep_ms} sched {hop.scheduled_duration_s:.0f}s"

    d = submission.submission_dir(team, sub_track, leg_id, out_root)
    t_ms = np.arange(ws.t0_ms, t_hi + 1, EMIT_S * 1000, dtype="int64")

    anchor_doc = anchors.build(leg_id, anchors.warm_start_anchor(ws.t0_ms, ws.lon, ws.lat))
    anchors.write(leg_id, anchors.warm_start_anchor(ws.t0_ms, ws.lon, ws.lat), paths.WORK / leg_id)
    n_cell = sum(a["source"] == "cell" for a in anchor_doc["anchors"])
    _ev(gui, "H1", f"{n_cell} cell anchors" if n_cell else "no cell anchors — H7 path",
        level="info" if n_cell else "warn", pct=0.5)
    if gui is not None:
        gui.anchors(anchor_doc)

    if path is None:
        # no route: stand still at the start — still a valid, scoreable file
        _ev(gui, "N3", "no GTFS hop or no OSM path — standing still at start", level="warn")
        lon = np.full(len(t_ms), ws.lon); lat = np.full(len(t_ms), ws.lat)
        submission.write_position(d, t_ms, lon, lat, hop.route_guess if hop else None)
        submission.write_station_calls(d, [])
        _mirror(gui, d)
        return info

    L = path.length_m
    info["path_len_m"] = round(L, 1)
    shape = None if os.environ.get("NO_HYDRATE") else hydrate.load_shape(leg_id)  # dev A/B switch
    hyd = None
    extra: dict = {}
    t_move, t_stop = motion_window(ws.t0_ms, hop.dep_ms, t_hi)
    Tm = (t_stop - t_move) / 1000.0
    if shape is not None:
        try:
            hyd = hydrate.hydrate(shape, anchor_doc, path, prior_distance_at=_prior_for(ws, hop, t_hi, L))
        except ValueError as e:
            _ev(gui, "H4", f"hydration failed ({e}) — trapezoid fallback", level="warn")
    if hyd is not None:
        dist = hyd.distance_at(t_ms)
        call_t = hyd.time_at_distance(max(0.0, L - APPROACH_M))
        call_ms = int(call_t) if call_t is not None else int(t_hi)
        info["window"] = f"hydrated {hyd.n_segments} segs, {hyd.n_anchors} anchors, cost {hyd.cost:.0f}"
        (paths.WORK / leg_id).mkdir(parents=True, exist_ok=True)
        (paths.WORK / leg_id / "hydrated.json").write_text(
            json.dumps({"leg_id": leg_id, "path_len_m": L, **hyd.to_json()}, indent=1), encoding="utf-8")
        extra = {"knots": hyd.to_json()["knots"], "source": "hydration"}
        _ev(gui, "H4", info["window"] + (f" — {'; '.join(hyd.notes)}" if hyd.notes else ""),
            level="warn" if hyd.notes else "info", pct=0.7)
    else:
        info["window"] = f"+{(t_move - ws.t0_ms) / 1000:.0f}s .. +{(t_stop - ws.t0_ms) / 1000:.0f}s"
        dist = trapezoid_distance((t_ms - t_move) / 1000.0, Tm, L)
        # 500 m-out call: the instant the profile crosses L - 500
        fine_t = np.arange(0, Tm, 0.5)
        fine_d = trapezoid_distance(fine_t, Tm, L)
        i = int(np.searchsorted(fine_d, max(0.0, L - APPROACH_M)))
        call_ms = int(t_move + fine_t[min(i, len(fine_t) - 1)] * 1000)
        extra = {"source": "trapezoid"}
    lonlat = path.at_distance(dist)
    submission.write_position(d, t_ms, lonlat[:, 0], lonlat[:, 1], hop.route_guess)
    submission.write_station_calls(d, [(call_ms, dest.name)])
    _ev(gui, "N3", f"path {L:.0f} m, {info['window']}", pct=0.8)
    _ev(gui, "T4", f"500 m-out call '{dest.name}' at +{(call_ms - ws.t0_ms) / 1000:.0f}s", pct=0.9)
    if gui is not None:
        gui.hydrated([{"t": int(t), "lat": float(ll[1]), "lon": float(ll[0]), "distance_m": float(dd)}
                      for t, ll, dd in zip(t_ms, lonlat, dist)],
                     route_guess=hop.route_guess, destination=dest.name, path_len_m=L, **extra)
        _mirror(gui, d)
    return info


def _mirror(gui, sub_dir: Path) -> None:
    if gui is None:
        return
    for name in ("position.csv", "station_calls.csv"):
        f = sub_dir / name
        if f.exists():
            gui.write_text(name, f.read_text(encoding="utf-8"))
