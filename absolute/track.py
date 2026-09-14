"""I2/N1 — routable OSM rail graph + station-to-station paths with linear referencing.

Every polyline vertex is a graph node; every consecutive vertex pair an edge.
Ways only touch where they share an exact node, and the extract has ~203
components, so loose endpoints are stitched to any node within STITCH_M.
Non-passenger track (yards, sidings, industrial) stays routable but penalised.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from . import paths
from .geo import M_PER_DEG_LAT, m_per_deg_lon

STITCH_M = 8.0
STATION_SNAP_M = 600.0
LAT0 = 50.85  # Belgium centre, for the working plane
PENALTY = {"yard": 4.0, "siding": 3.0, "spur": 3.0, "crossover": 1.2}
USAGE_PENALTY = {"industrial": 5.0, "military": 5.0, "tourism": 3.0, "branch": 1.1}


@dataclass
class Path:
    lonlat: np.ndarray      # (n, 2)
    cum_m: np.ndarray       # (n,) cumulative distance, 0 at start

    @property
    def length_m(self) -> float:
        return float(self.cum_m[-1])

    def at_distance(self, d) -> np.ndarray:
        """Linear-reference d (m, array-like) -> (lon, lat), clamped to the path."""
        d = np.clip(np.asarray(d, dtype=float), 0.0, self.length_m)
        lon = np.interp(d, self.cum_m, self.lonlat[:, 0])
        lat = np.interp(d, self.cum_m, self.lonlat[:, 1])
        return np.column_stack([lon, lat])

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        """Nearest-segment projection -> (distance_along_m, lateral_offset_m). Same rule as the scorer."""
        mlon = m_per_deg_lon(LAT0)
        xy = np.column_stack([self.lonlat[:, 0] * mlon, self.lonlat[:, 1] * M_PER_DEG_LAT])
        p = np.array([lon * mlon, lat * M_PER_DEG_LAT])
        a, b = xy[:-1], xy[1:]
        ab = b - a
        L2 = (ab ** 2).sum(1)
        t = np.clip(((p - a) * ab).sum(1) / np.where(L2 == 0, 1, L2), 0, 1)
        c = a + t[:, None] * ab
        perp = np.hypot(*(p - c).T)
        i = int(np.argmin(perp))
        return float(self.cum_m[i] + t[i] * np.sqrt(L2[i])), float(perp[i])


class RailGraph:
    def __init__(self, nodes_lonlat: np.ndarray, edges: np.ndarray, length_m: np.ndarray, cost: np.ndarray):
        self.nodes = nodes_lonlat
        self.edges = edges
        self.length_m = length_m
        n = len(nodes_lonlat)
        w = np.concatenate([cost, cost])
        i = np.concatenate([edges[:, 0], edges[:, 1]])
        j = np.concatenate([edges[:, 1], edges[:, 0]])
        self.adj = coo_matrix((w, (i, j)), shape=(n, n)).tocsr()
        self._xy = self._to_xy(nodes_lonlat)
        self.tree = cKDTree(self._xy)

    @staticmethod
    def _to_xy(lonlat):
        return np.column_stack([lonlat[:, 0] * m_per_deg_lon(LAT0), lonlat[:, 1] * M_PER_DEG_LAT])

    def nearest_nodes(self, lon: float, lat: float, radius_m: float = STATION_SNAP_M, k: int = 12) -> list[int]:
        d, idx = self.tree.query(self._to_xy(np.array([[lon, lat]]))[0], k=k, distance_upper_bound=radius_m)
        return [int(i) for i, di in zip(idx, d) if np.isfinite(di)]

    def route(self, lon_a, lat_a, lon_b, lat_b) -> Path | None:
        """Cheapest path between the two locations, trying a few snap nodes each side."""
        src = self.nearest_nodes(lon_a, lat_a)
        dst = self.nearest_nodes(lon_b, lat_b)
        if not src or not dst:
            return None
        dist, pred, _ = dijkstra(self.adj, indices=src, return_predecessors=True, min_only=True)
        best = min(dst, key=lambda j: dist[j])
        if not np.isfinite(dist[best]):
            return None
        seq = [best]
        while pred[seq[-1]] >= 0:
            seq.append(int(pred[seq[-1]]))
        seq.reverse()
        lonlat = self.nodes[seq]
        xy = self._to_xy(lonlat)
        step = np.hypot(*np.diff(xy, axis=0).T)
        return Path(lonlat, np.concatenate([[0.0], np.cumsum(step)]))


def build() -> RailGraph:
    feats = json.loads(paths.OSM_NETWORK.read_text(encoding="utf-8"))["features"]
    coords, edges, factor = [], [], []
    key_to_id: dict[tuple, int] = {}

    def node(c):
        k = (round(c[0], 7), round(c[1], 7))
        if k not in key_to_id:
            key_to_id[k] = len(coords)
            coords.append(k)
        return key_to_id[k]

    for f in feats:
        p = f["properties"]
        pen = PENALTY.get(p.get("service"), 1.0) * USAGE_PENALTY.get(p.get("usage"), 1.0)
        ids = [node(c) for c in f["geometry"]["coordinates"]]
        for a, b in zip(ids[:-1], ids[1:]):
            if a != b:
                edges.append((a, b))
                factor.append(pen)

    nodes = np.array(coords, dtype=float)
    edges = np.array(edges, dtype=np.int64)
    xy = RailGraph._to_xy(nodes)
    length = np.hypot(*(xy[edges[:, 1]] - xy[edges[:, 0]]).T)

    # stitch: degree-1 nodes that have a foreign node within STITCH_M get a zero-ish edge
    deg = np.bincount(edges.ravel(), minlength=len(nodes))
    ends = np.flatnonzero(deg == 1)
    tree = cKDTree(xy)
    extra = set()
    for e in ends:
        for j in tree.query_ball_point(xy[e], STITCH_M):
            if j != e:
                extra.add((min(e, j), max(e, j)))
    if extra:
        ex = np.array(sorted(extra), dtype=np.int64)
        edges = np.vstack([edges, ex])
        exlen = np.hypot(*(xy[ex[:, 1]] - xy[ex[:, 0]]).T)
        length = np.concatenate([length, exlen])
        factor = np.concatenate([np.array(factor), np.full(len(ex), 1.5)])
    cost = length * np.asarray(factor) + 0.01
    return RailGraph(nodes, edges, length, cost)


def load(force: bool = False) -> RailGraph:
    paths.CACHE.mkdir(parents=True, exist_ok=True)
    cache = paths.CACHE / "rail_graph.pkl"
    if cache.exists() and not force:
        return pickle.loads(cache.read_bytes())
    g = build()
    cache.write_bytes(pickle.dumps(g))
    return g
