"""Phase 3 — shape hydration (H3–H7): give every shape segment a length.

Joint code: reads the motion lane's `shape.json` and the absolute lane's
`anchors.json`, plus one candidate OSM path (`track.Path`), and returns a
monotone distance-along-time curve pinned at the segment boundaries.

Formulation. The shape is a time-ordered list of segments with boundaries
tau_0..tau_K. Unknowns are the path distances d_0..d_K at those boundaries,
monotone, d_0 = 0 (warm start = path start) and d_K ~ path length. Every term
below is a cost in *metres of penalty*, so the weights read as exchange rates:

  * turn match   — the path's bearing change over [d_k, d_k+1] must equal the
                   segment's gyro `turn_deg` (0 for straights). Turns are the
                   high-confidence pins of WORKLOG step 3.
  * stopped      — a `moving: false` segment should not advance (H3). It is a
                   penalty, not a constraint, so anchors/turns can overrule a
                   wrong stationary call.
  * speed prior  — moving segments are pulled toward the leg's mean moving
                   speed (path length / total moving time) and, weakly, toward
                   the IMU curve speed where the motion lane has evidence (H2).
  * anchors      — a cell anchor inside a segment must sit within its radius of
                   the path point at that moment (multi-hypothesis: best
                   candidate wins).

Solved exactly by dynamic programming on a distance grid: forward + backward
passes give the optimum (H4), and their sum gives a posterior spread per
boundary, reported as `sigma_m` (H5). No anchors is just fewer terms (H7).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import uniform_filter1d

from . import geo, paths, track

GRID_MIN_M = 25.0
GRID_CELLS_MAX = 1600          # cap the DP grid; grid step grows with path length
V_MAX_MPS = 55.0               # 198 km/h — hard feasibility bound
V_MIN_MOVING_MPS = 1.0
START_SLACK_M = 300.0          # platform vs OSM station node
END_SLACK_M = 150.0
HEADING_SMOOTH_M = 150.0
MAX_TURN_DEG_PER_25M = 15.0    # a real rail curve never bends faster (r > ~100 m); anything sharper
                               # is an OSM hairpin/reversal artifact (N1) and is dropped from the signature

W_TURN = 25.0                  # m of penalty per degree of turn mismatch beyond the deadband
TURN_DEADBAND_DEG = 3.0
TURN_DRIFT_DEG_PER_S = 0.01    # gyro bias residual, widens the deadband on long segments
W_STOP = 3.0                   # per metre moved during a "stopped" segment
STOP_TRUST_MIN_S = 15.0        # a "stopped" segment this long is believed outright
STOP_FRAC_WINDOW_S = 40.0      # shorter ones: believed only if the surrounding window is mostly stopped
STOP_FRAC_MIN = 0.5            # (ic536_02 flickers 2–5 s stops at 25 m/s -> frac ~0.35 -> moving;
                               #  ic2035_00 platform dwell flickers too but frac ~0.6 -> stopped)
W_SPEED = 0.25                 # per metre of deviation from mean moving speed
W_IMU = 0.10                   # per metre of deviation from the curve-derived speed prior
W_ANCHOR = 0.50                # per metre a cell anchor sits outside its radius
W_SLACK = 0.5                  # per metre of start/end slack beyond the free band
SIGMA_TEMP_M = 150.0           # softmin temperature for the confidence posterior


@dataclass
class Hydrated:
    t_ms: np.ndarray           # (K+1,) boundary times
    d_m: np.ndarray            # (K+1,) fitted distances
    sigma_m: np.ndarray        # (K+1,) posterior spread
    cost: float                # optimum total cost (metres of penalty)
    n_segments: int
    n_anchors: int
    notes: list[str] = field(default_factory=list)

    def distance_at(self, t) -> np.ndarray:
        return np.interp(np.asarray(t, dtype=float), self.t_ms.astype(float), self.d_m)

    def time_at_distance(self, d: float) -> float | None:
        """First boundary-interpolated instant the curve reaches d; None if never."""
        idx = np.searchsorted(self.d_m, d)
        if idx >= len(self.d_m):
            return None
        if idx == 0:
            return float(self.t_ms[0])
        d0, d1 = self.d_m[idx - 1], self.d_m[idx]
        t0, t1 = self.t_ms[idx - 1], self.t_ms[idx]
        f = 0.0 if d1 <= d0 else (d - d0) / (d1 - d0)
        return float(t0 + f * (t1 - t0))

    def to_json(self) -> dict:
        return {"knots": [{"t": int(t), "distance_m": round(float(d), 1), "sigma_m": round(float(s), 1)}
                          for t, d, s in zip(self.t_ms, self.d_m, self.sigma_m)],
                "cost": round(self.cost, 1), "n_segments": self.n_segments,
                "n_anchors": self.n_anchors, "notes": self.notes}


def load_shape(leg_id: str) -> dict | None:
    p = paths.WORK / leg_id / "shape.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- path signature

@dataclass
class PathGrid:
    d: np.ndarray              # (J,) grid distances
    heading_deg: np.ndarray    # (J,) unwrapped bearing, 0 at start, + = clockwise/right
    x: np.ndarray
    y: np.ndarray
    lat0: float

    @property
    def step(self) -> float:
        return float(self.d[1] - self.d[0]) if len(self.d) > 1 else 1.0


def path_grid(path: track.Path) -> PathGrid:
    L = path.length_m
    step = max(GRID_MIN_M, L / GRID_CELLS_MAX)
    d = np.arange(0.0, L + step, step)
    d[-1] = min(d[-1], L)
    ll = path.at_distance(d)
    lat0 = float(ll[:, 1].mean())
    x, y = geo.to_xy(ll[:, 0], ll[:, 1], lat0)
    b = np.degrees(np.arctan2(np.diff(x), np.diff(y)))          # bearing clockwise from north
    inc = np.diff(np.degrees(np.unwrap(np.radians(b))))
    inc[np.abs(inc) > MAX_TURN_DEG_PER_25M * step / 25.0] = 0.0   # kill hairpin artifacts
    h = np.concatenate([[0.0, 0.0], np.cumsum(inc)])
    h = uniform_filter1d(h, size=max(1, int(round(HEADING_SMOOTH_M / step))), mode="nearest")
    return PathGrid(d, h - h[0], x, y, lat0)


def _anchor_cost_on_grid(pg: PathGrid, anchor: dict) -> np.ndarray:
    """Per grid point: how far (m) the best candidate tower sits outside its radius."""
    best = np.full(len(pg.d), np.inf)
    for c in anchor["candidates"]:
        cx, cy = geo.to_xy(c["lon"], c["lat"], pg.lat0)
        out = np.maximum(0.0, np.hypot(pg.x - cx, pg.y - cy) - c["radius_m"])
        best = np.minimum(best, out)
    return best


def _believed_stops(tau: np.ndarray, moving: np.ndarray) -> np.ndarray:
    """H3: which `moving: false` segments get the stop penalty. Long stops always; short
    ones only when the stopped fraction of time in a window around them is high — that
    separates a flickering platform dwell from flicker while running."""
    dur = np.diff(tau) / 1000.0
    out = ~moving & (dur >= STOP_TRUST_MIN_S)
    half = STOP_FRAC_WINDOW_S * 500.0
    for k in np.flatnonzero(~moving & ~out):
        mid = (tau[k] + tau[k + 1]) / 2
        lo, hi = mid - half, mid + half
        overlap = np.maximum(0.0, np.minimum(tau[1:], hi) - np.maximum(tau[:-1], lo)) / 1000.0
        if overlap[~moving].sum() >= STOP_FRAC_MIN * max(overlap.sum(), 1e-9):
            out[k] = True
    return out


# --------------------------------------------------------------------------- the DP

def hydrate(shape: dict, anchor_doc: dict | None, path: track.Path) -> Hydrated:
    segs = [s for s in shape.get("segments", []) if s["t_end"] > s["t_start"]]
    if not segs:
        raise ValueError("shape has no segments")
    pg = path_grid(path)
    J = len(pg.d)
    L = path.length_m
    step = pg.step
    idx = np.arange(J)
    jj, jp = np.meshgrid(idx, idx, indexing="ij")      # jp = from (rows), jj = to (cols)
    jp, jj = jp.T, jj.T                                # rows: from j', cols: to j
    delta = (jj - jp) * step                           # (J, J) distance advanced, negative = infeasible
    dH = pg.heading_deg[jj] - pg.heading_deg[jp]

    tau = np.array([segs[0]["t_start"]] + [s["t_end"] for s in segs], dtype="int64")
    dur = np.diff(tau) / 1000.0
    moving = np.array([bool(s.get("moving")) for s in segs])
    stopped = _believed_stops(tau, moving)             # H3
    t_moving = float(dur[~stopped].sum())
    v_mean = L / t_moving if t_moving > 30 else L / max(dur.sum(), 1.0)

    anchors = [a for a in (anchor_doc or {}).get("anchors", []) if a["source"] != "warm_start"]
    anchor_grid = [(a["t"], _anchor_cost_on_grid(pg, a)) for a in anchors]

    def seg_cost(k: int) -> np.ndarray:
        s = segs[k]
        T = dur[k]
        c = np.zeros((J, J))
        feas = (delta >= 0) & (delta <= V_MAX_MPS * T + step)
        band = TURN_DEADBAND_DEG + TURN_DRIFT_DEG_PER_S * T
        c += W_TURN * np.maximum(0.0, np.abs(dH - float(s.get("turn_deg") or 0.0)) - band)
        if stopped[k]:
            c += W_STOP * delta
        else:
            c += W_SPEED * np.abs(delta - v_mean * T)
            v_imu = s.get("speed_prior_mps")
            if moving[k] and s.get("speed_source") == "curve" and v_imu:
                c += W_IMU * np.abs(delta - float(v_imu) * T)
        for t_a, ag in anchor_grid:
            if tau[k] <= t_a < tau[k + 1]:
                frac = (t_a - tau[k]) / (tau[k + 1] - tau[k])
                at = np.rint(jp + frac * (jj - jp)).astype(int)
                c += W_ANCHOR * ag[at]
        c[~feas] = np.inf
        return c

    K = len(segs)
    F = np.full((K + 1, J), np.inf)
    F[0] = W_SLACK * np.maximum(0.0, pg.d - START_SLACK_M)
    B = np.full((K + 1, J), np.inf)
    B[K] = W_SLACK * np.maximum(0.0, np.abs(pg.d - L) - END_SLACK_M)
    arg = np.zeros((K + 1, J), dtype=int)
    costs = [seg_cost(k) for k in range(K)]
    for k in range(K):
        tot = F[k][:, None] + costs[k]                 # rows j', cols j
        arg[k + 1] = np.argmin(tot, axis=0)
        F[k + 1] = tot[arg[k + 1], idx]
    for k in range(K - 1, -1, -1):
        tot = costs[k] + B[k + 1][None, :]
        B[k] = tot.min(axis=1)

    # optimum by traceback from the best final knot
    j = int(np.argmin(F[K] + B[K]))
    best = float(F[K][j] + B[K][j])
    knots = np.zeros(K + 1, dtype=int)
    knots[K] = j
    for k in range(K, 0, -1):
        j = arg[k][j]
        knots[k - 1] = j
    # posterior spread per knot from the two-sided cost
    sigma = np.zeros(K + 1)
    for k in range(K + 1):
        tot = F[k] + B[k]
        w = np.exp(-(tot - tot.min()) / SIGMA_TEMP_M)
        w /= w.sum()
        mu = (w * pg.d).sum()
        sigma[k] = np.sqrt((w * (pg.d - mu) ** 2).sum())

    notes = []
    if not shape.get("orientation_ok", True):
        notes.append("orientation_ok=false: turn pins are suspect")
    if not anchors:
        notes.append("no anchors (H7 path): turns + stops + speed prior only")
    return Hydrated(tau, pg.d[knots], sigma, best if np.isfinite(best) else float("inf"),
                    K, len(anchors), notes)
