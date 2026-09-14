"""Joint streaming solve: two input streams in, one output stream out.

    IMU samples  ->  shape segments  \
                                      >--  prefix hydration  -->  hydrated points
    cell samples ->  anchors         /

The batch path (`scripts/run_joint.py` -> `absolute.solve.solve_warm`) runs the
lanes back to back: the whole shape is written, then all the anchors, then one
global DP fit. That is fine for scoring but there is no intermediate state to
look at, and it is not how the thing would run on a train.

Here both lanes advance on one leg-time clock instead. IMU samples feed the
`ShapeTracker`; cell anchors are released the moment their timestamp passes;
and every `refit_every_s` of leg time the hydration DP is re-run *on the prefix*
-- the segments and anchors seen so far, with the end-of-path pin dropped since
mid-leg the train has not arrived yet. The fitted knots are turned into lat/lon
and appended to the hydrated stream, so the map draws as the leg plays.

Causality: an anchor is only ever released once leg time reaches it, and the
shape prefix only ever contains segments the tracker has already closed. The
cell table is read up front (it is one small query) but nothing is *used*
before its own timestamp. The route shortlist comes from GTFS at t0 and is
re-ranked live by how well each candidate path hydrates the prefix.

Scoring is unchanged: when the stream ends, the completed shape.json and
anchors.json are written and `solve.solve_warm()` produces the submission from
them exactly as the batch path does -- full-resolution grid, end pinned. The
streaming fits drive the live view and the live route guess, never the CSVs.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from absolute import anchors as anchors_mod
from absolute import gtfs, hydrate, paths, solve, track
from absolute.stations import load as load_stations
from motion.shape_stream import ShapeTracker, imu_stream

REFIT_EVERY_S = 30.0        # leg-time between prefix hydration refits
LIVE_GRID_CELLS = 350       # coarse DP grid for the live refits (the final fit runs full)
RERANK_TOP = 4              # candidate routes carried as live hypotheses
EMIT_S = solve.EMIT_S       # spacing of the emitted hydrated points
MAX_SLEEP_S = 4.0           # never stall the viewer on one long quiet stretch


@dataclass
class StreamConfig:
    refit_every_s: float = REFIT_EVERY_S
    live_grid_cells: int = LIVE_GRID_CELLS
    rerank_top: int = RERANK_TOP
    demo_speed: float = 0.0      # 0 = run flat-out, still streaming; >0 = pace to X-times realtime


@dataclass
class _Candidate:
    hop: gtfs.Hop
    path: track.Path
    timing_cost: float
    live_cost: float = float("inf")


def _candidates(ws: solve.WarmStart, observed_duration_s: float, g: track.RailGraph,
                top: int) -> tuple[list[_Candidate], int]:
    """GTFS shortlist at t0 + an OSM path each. This is all the route knowledge
    available before a single IMU sample has been consumed."""
    reg = load_stations()
    st = reg.by_name(ws.station_name) or reg.nearest(ws.lon, ws.lat)[0][0]
    ranked = gtfs.rank_hops(gtfs.hops_from(st, ws.t0_ms), ws.t0_ms, observed_duration_s)
    out = []
    for h, cost in ranked[:top]:
        pth = g.route(ws.lon, ws.lat, h.to.lon, h.to.lat)
        if pth is not None:
            out.append(_Candidate(h, pth, cost))
    return out, len(ranked)


def _shape_prefix(tk: ShapeTracker, leg_id: str) -> dict:
    """The shape contract as far as it is known, plus the still-open segment as
    a provisional one so the fit reaches *now* rather than the last closed
    boundary (a long straight can stay open for minutes)."""
    segs = list(tk.segments)
    preview = tk.preview_segment()
    if preview is not None:
        segs.append(preview)
    return {"leg_id": leg_id, "t_start": tk.t_start_ms, "t_end": tk.t_ms,
            "orientation_ok": tk.orientation_ok, "segments": segs}


def _points(hy: hydrate.Hydrated, path: track.Path, t0_ms: int, t_end_ms: int) -> list[dict]:
    """Hydrated knots -> the emitted point stream (what the map draws)."""
    if t_end_ms <= t0_ms:
        return []
    t_ms = np.arange(t0_ms, t_end_ms + 1, EMIT_S * 1000, dtype="int64")
    dist = np.clip(hy.distance_at(t_ms), 0.0, path.length_m)
    lonlat = path.at_distance(dist)
    return [{"t": int(t), "lat": float(ll[1]), "lon": float(ll[0]), "distance_m": float(d)}
            for t, ll, d in zip(t_ms, lonlat, dist)]


def solve_leg(leg_id: str, team: str, ws: solve.WarmStart | None = None,
              out_root: Path | None = None, gui=None, cfg: StreamConfig | None = None) -> dict:
    """Stream one leg, then hand the completed contracts to the batch solver
    for the actual submission. Returns solve_warm()'s info dict."""
    cfg = cfg or StreamConfig()
    ws = ws or solve.warm_from_meta(leg_id)
    t_lo, t_hi = solve.sensor_span_ms(leg_id)
    T = (t_hi - ws.t0_ms) / 1000.0
    _ev(gui, "W1", f"stream start {ws.station_name} @ {ws.t0_ms}, sensor span {T:.0f}s", pct=0.02)

    g = track.load()
    cands, n_ranked = _candidates(ws, T, g, cfg.rerank_top)
    if not cands:
        _ev(gui, "R3", "no routable GTFS candidate — falling straight through to the batch solver",
            level="warn")
    else:
        _ev(gui, "R3", f"{n_ranked} GTFS candidates, {len(cands)} routable; opening guess "
            f"{cands[0].hop.route_guess}->{cands[0].hop.to.name}", pct=0.08)

    # Anchor stream: resolved up front (one small query), released by timestamp.
    warm = anchors_mod.warm_start_anchor(ws.t0_ms, ws.lon, ws.lat)
    pending = sorted(anchors_mod.cell_anchors(leg_id), key=lambda a: a["t"])
    live: list[dict] = [warm]
    ai = 0

    tk = ShapeTracker(leg_id)
    db = str(paths.leg_dir(leg_id) / "sensors.db")
    next_refit_ms = None
    demo_t0 = time.time()
    n_refits = 0
    best = cands[0] if cands else None

    for row in imu_stream(db):
        t = row[0]
        tk.step(*row)
        if next_refit_ms is None:
            next_refit_ms = t + int(cfg.refit_every_s * 1000)
        while ai < len(pending) and pending[ai]["t"] <= t:
            live.append(pending[ai])
            ai += 1
        if t < next_refit_ms:
            continue
        next_refit_ms = t + int(cfg.refit_every_s * 1000)
        if cfg.demo_speed > 0 and tk.t_start_ms is not None:
            wait = (t - tk.t_start_ms) / 1000.0 / cfg.demo_speed - (time.time() - demo_t0)
            if wait > 0:
                time.sleep(min(wait, MAX_SLEEP_S))
        best = _refit(leg_id, ws, tk, live, cands, best, gui, cfg, t, t_hi)
        n_refits += 1

    shape = tk.finish()
    _write_contracts(leg_id, shape, warm, gui)
    _ev(gui, "M3", f"stream done: {len(shape['segments'])} segments, {len(live) - 1} cell anchors, "
        f"{n_refits} live refits", pct=0.75)
    return solve.solve_warm(leg_id, team, ws, out_root, gui=gui)


def _refit(leg_id, ws, tk, live, cands, best, gui, cfg, t_now, t_hi):
    """One tick of the joined stream: fit the prefix on every live candidate,
    emit the hydrated points of the winner."""
    shape = _shape_prefix(tk, leg_id)
    anchor_doc = {"leg_id": leg_id, "anchors": live}
    if gui is not None:
        gui.shape(shape)
        gui.anchors(anchor_doc)
    if not shape["segments"] or not cands:
        return best

    fitted: list[tuple[_Candidate, hydrate.Hydrated]] = []
    for c in cands:
        try:
            # solve._prior_for is reused rather than rebuilt here on purpose: the
            # schedule trapezoid must stay one formula across batch and stream.
            hy = hydrate.hydrate(shape, anchor_doc, c.path,
                                 prior_distance_at=solve._prior_for(ws, c.hop, t_hi, c.path.length_m),
                                 pin_end=False, grid_cells_max=cfg.live_grid_cells)
        except ValueError:
            continue
        # timing cost is seconds, hydration cost is metres of penalty: same exchange
        # rate the batch re-rank uses (solve.SHAPE_COST_PER_M), per segment.
        c.live_cost = c.timing_cost + solve.SHAPE_COST_PER_M * hy.cost / max(hy.n_segments, 1)
        fitted.append((c, hy))
    if not fitted:
        return best

    fitted.sort(key=lambda ch: ch[0].live_cost)
    winner, hy = fitted[0]
    pts = _points(hy, winner.path, ws.t0_ms, int(hy.t_ms[-1]))
    if gui is not None:
        gui.hydrated(pts, source="stream", partial=True, route_guess=winner.hop.route_guess,
                     destination=winner.hop.to.name, path_len_m=winner.path.length_m,
                     knots=hy.to_json()["knots"])
    changed = best is not None and winner.hop.route_guess != best.hop.route_guess
    _ev(gui, "H4", f"+{(t_now - ws.t0_ms) / 1000:.0f}s: {len(shape['segments'])} segs, "
        f"{len(live) - 1} anchors, {len(pts)} points -> {winner.hop.route_guess} "
        f"({winner.hop.to.name}), fit {hy.cost:.0f}" + (" [guess changed]" if changed else ""),
        level="warn" if changed else "info",
        pct=min(0.7, 0.1 + 0.6 * (t_now - ws.t0_ms) / max(t_hi - ws.t0_ms, 1)))
    return winner


def _write_contracts(leg_id: str, shape: dict, warm: dict, gui) -> None:
    """Both lane contracts, complete, where the batch solver expects them."""
    out = paths.WORK / leg_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "shape.json").write_text(json.dumps(shape, indent=2), encoding="utf-8")
    anchors_mod.write(leg_id, warm, out)
    if gui is not None:
        gui.shape(shape)


def _ev(gui, stage, msg, **kw):
    if gui is not None:
        gui.event(stage, msg, **kw)
