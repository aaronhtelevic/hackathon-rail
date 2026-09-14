"""Cold track (F1–F3): infer the warm-start triple from the sensors alone, then reuse the
warm pipeline. Start time = first sensor sample. Start station = the GTFS/OSM station most
consistent with the cell towers seen in the first COLD_WINDOW_S seconds *and* with having a
departure around t0 in GTFS. Legs with no cell samples get None — no fix is better than a
wild one (the first-fix metric only counts a row within 1 km of the truth).
"""
from __future__ import annotations

import os

import numpy as np

from . import anchors, geo, gtfs, hydrate, track
from .solve import (NO_PATH_COST_S, SHAPE_COST_PER_M, WarmStart, _prior_for, choose_hop,
                    schedule_check, schedule_cost, sensor_span_ms)
from .stations import load as load_stations

COLD_WINDOW_S = 60.0        # cell anchors considered for the start fix
STATION_SLACK_M = 500.0     # a station this far beyond a tower's radius is still a candidate
MAX_STATIONS = 5            # start-station hypotheses carried into the GTFS ranking
STATION_COST_PER_M = 0.05   # seconds of cost per metre a station sits outside the anchors' radii
MAX_RADIUS_M = 6000.0       # eNodeB centroids with a wider radius say nothing about *which* station
CENTROID_PULL = 0.02        # tie-break inside a radius: nearer the tower is a little better

# No-cell fallback (24 of the 50 practice legs have an empty cell_samples table): the start
# station cannot be named at all, so every timing-plausible hop in the feed is a candidate and
# the shape + timetable rank them. Timing alone leaves ~400 origins with the truth at median
# rank 30 (worst 162), so the cutoff has to be generous — the true hop's own timing cost reaches
# 554 s on l1679_02, whose schedule prefers the wrong direction by 253 s.
NO_CELL_CUTOFF_S = 600.0    # timing cost past which the shape cannot rescue a candidate
NO_CELL_MAX_CAND = 500      # hard budget: ~55 ms of hydration each


def station_candidates(leg_id: str, t0_ms: int) -> list[tuple[object, float]]:
    """(station, misfit_m) for stations consistent with the early cell anchors, best first.
    misfit = weight-averaged distance outside each anchor's best candidate radius."""
    doc = anchors.build(leg_id)
    early = [a for a in doc["anchors"] if a["source"] == "cell" and a["t"] <= t0_ms + COLD_WINDOW_S * 1000]
    tight = [a for a in early if min(c["radius_m"] for c in a["candidates"]) <= MAX_RADIUS_M]
    early = tight or early
    if not early:
        return []
    reg = load_stations()
    lon, lat = reg._lon, reg._lat
    misfit = np.zeros(len(reg.stations))
    for a in early:
        best = np.full(len(reg.stations), np.inf)
        for c in a["candidates"]:
            dist = geo.dist_m(c["lon"], c["lat"], lon, lat)
            out = np.maximum(0.0, dist - c["radius_m"]) + CENTROID_PULL * dist
            best = np.minimum(best, out / max(c["weight"], 1e-3) ** 0.5)  # favour heavier candidates
        misfit += best / len(early)
    order = np.argsort(misfit)
    out = [(reg.stations[i], float(misfit[i])) for i in order[:MAX_STATIONS]]
    return [(s, m) for s, m in out if m <= STATION_SLACK_M or s is out[0][0]]


def hop_candidates_no_cell(leg_id: str, g: track.RailGraph) -> list[tuple[float, gtfs.Hop]]:
    """(cost, hop) over every timing-plausible hop in the feed, best first, for a leg with no
    cell samples at all.

    Same three-stage cost as `solve.choose_hop` minus the anchor term, which has nothing to
    bite on here: GTFS timing, then the hydration fit of the IMU shape onto the candidate's
    OSM path, then `schedule_check` on the hydrated curve. The absolute signals a phone
    without cellular still has are the clock and the turn sequence — length is not one of
    them (`dead_reckoned_length_m` is off by 20x on ic536_02), and with no magnetometer
    there is no absolute bearing either, so the geodesic bound below is deliberately loose.
    Candidates are grouped by origin so each origin costs one dijkstra (`track.paths_from`)."""
    t_lo, t_hi = sensor_span_ms(leg_id)
    T = (t_hi - t_lo) / 1000.0
    reach_m = hydrate.V_MAX_MPS * T          # a hop the leg had no time to cover is out
    ranked = [(h, c) for h, c in gtfs.rank_hops(gtfs.hops_anywhere(t_lo), t_lo, T)
              if c <= NO_CELL_CUTOFF_S
              and geo.dist_m(h.frm.lon, h.frm.lat, h.to.lon, h.to.lat) <= reach_m]
    ranked = ranked[:NO_CELL_MAX_CAND]
    if not ranked:
        return []
    shape = None if os.environ.get("NO_HYDRATE") else hydrate.load_shape(leg_id)
    if shape is None:
        return [(c, h) for h, c in ranked]   # timing only — a wild fix, but ordered
    doc = anchors.build(leg_id)
    by_origin: dict[str, list[tuple[gtfs.Hop, float]]] = {}
    for h, c in ranked:
        by_origin.setdefault(h.frm.uic or h.frm.name, []).append((h, c))
    scored: list[tuple[float, gtfs.Hop]] = []
    for group in by_origin.values():
        frm = group[0][0].frm
        ws = WarmStart(frm.name, frm.lon, frm.lat, int(t_lo))
        for (h, c), pth in zip(group, g.paths_from(frm.lon, frm.lat,
                                                   [(h.to.lon, h.to.lat) for h, _ in group])):
            if pth is None:
                scored.append((c + NO_PATH_COST_S, h))
                continue
            try:
                hy = hydrate.hydrate(shape, doc, pth, _prior_for(ws, h, t_hi, pth.length_m))
            except ValueError:
                scored.append((c + NO_PATH_COST_S, h))
                continue
            cost = c + SHAPE_COST_PER_M * hy.cost / max(hy.n_segments, 1)
            scored.append((cost + schedule_cost(schedule_check(hy, h, pth.length_m)), h))
    scored.sort(key=lambda x: x[0])
    return scored


def cold_start(leg_id: str, g: track.RailGraph | None = None) -> tuple[WarmStart | None, list]:
    """Best (station, t0) hypothesis as a WarmStart, plus the ranked alternatives for the log.
    Each station hypothesis is scored by the full warm route ranking (GTFS timing + anchor
    misfit + shape fit, `solve.choose_hop`) so adjacent stations — Antwerpen-Centraal vs
    Berchem, Brussel-Noord vs Centraal — are separated by the ride, not by coarse towers."""
    t_lo, t_hi = sensor_span_ms(leg_id)
    T = (t_hi - t_lo) / 1000.0
    g = g or track.load()
    ranked = []
    for st, misfit in station_candidates(leg_id, t_lo):
        ws = WarmStart(st.name, st.lon, st.lat, int(t_lo))
        hop, hops, _ = choose_hop(ws, T, leg_id, g)
        if hop is None:
            continue
        ranked.append((hops[0][1] + STATION_COST_PER_M * misfit, st, misfit, hop))
    if not ranked:
        # No tower ever named a start station. Rank the whole feed by shape + timetable
        # instead of skipping the leg — `misfit` is reported as 0 since none was measured.
        scored = hop_candidates_no_cell(leg_id, g)
        if not scored:
            return None, []
        hop = scored[0][1]
        return (WarmStart(hop.frm.name, hop.frm.lon, hop.frm.lat, int(t_lo)),
                [(c, h.frm, 0.0, h) for c, h in scored[:MAX_STATIONS]])
    ranked.sort(key=lambda x: x[0])
    st = ranked[0][1]
    return WarmStart(st.name, st.lon, st.lat, int(t_lo)), ranked
