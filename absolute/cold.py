"""Cold track (F1–F3): infer the warm-start triple from the sensors alone, then reuse the
warm pipeline. Start time = first sensor sample. Start station = the GTFS/OSM station most
consistent with the cell towers seen in the first COLD_WINDOW_S seconds *and* with having a
departure around t0 in GTFS. Legs with no cell samples get None — no fix is better than a
wild one (the first-fix metric only counts a row within 1 km of the truth).
"""
from __future__ import annotations

import numpy as np

from . import anchors, geo, gtfs, track
from .solve import WarmStart, choose_hop, sensor_span_ms
from .stations import load as load_stations

COLD_WINDOW_S = 60.0        # cell anchors considered for the start fix
STATION_SLACK_M = 500.0     # a station this far beyond a tower's radius is still a candidate
MAX_STATIONS = 5            # start-station hypotheses carried into the GTFS ranking
STATION_COST_PER_M = 0.05   # seconds of cost per metre a station sits outside the anchors' radii
MAX_RADIUS_M = 6000.0       # eNodeB centroids with a wider radius say nothing about *which* station
CENTROID_PULL = 0.02        # tie-break inside a radius: nearer the tower is a little better


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
        return None, []
    ranked.sort(key=lambda x: x[0])
    st = ranked[0][1]
    return WarmStart(st.name, st.lon, st.lat, int(t_lo)), ranked
