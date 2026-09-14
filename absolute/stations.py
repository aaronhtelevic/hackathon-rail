"""I6 — one station registry joining OSM (geometry, names) and GTFS (stop_pk, UIC).

Join key is the UIC code: GTFS stop_id 'gs:nmbssncb:8896008' <-> OSM uic_ref '8896008'.
Name matching is the fallback, using the scorer's own normalisation rule.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from . import paths
from .geo import dist_m
from .names import normalize, names_match


@dataclass
class Station:
    uic: str | None
    name: str            # preferred display name (Dutch where available)
    lon: float
    lat: float
    gtfs_stop_pks: list[int] = field(default_factory=list)
    aliases: set[str] = field(default_factory=set)  # normalised

    def matches(self, guess: str) -> bool:
        return any(names_match(guess, a) for a in self.aliases)


class StationRegistry:
    def __init__(self, stations: list[Station]):
        self.stations = stations
        self._by_uic = {s.uic: s for s in stations if s.uic}
        self._by_pk = {pk: s for s in stations for pk in s.gtfs_stop_pks}
        self._lon = np.array([s.lon for s in stations])
        self._lat = np.array([s.lat for s in stations])

    def by_uic(self, uic: str) -> Station | None:
        return self._by_uic.get(str(uic))

    def by_stop_pk(self, pk: int) -> Station | None:
        return self._by_pk.get(int(pk))

    def by_name(self, name: str) -> Station | None:
        n = normalize(name)
        exact = [s for s in self.stations if n in s.aliases]
        if exact:
            return exact[0]
        fuzzy = [s for s in self.stations if s.matches(n)]
        if not fuzzy:  # word order / spelling variants: 'aspere gavere' vs 'gavere asper'
            fuzzy = [s for s in self.stations if any(_tokens_match(n, a) for a in s.aliases)]
        # prefer the shortest alias (least extra qualifiers) among fuzzy hits
        return min(fuzzy, key=lambda s: min(len(a) for a in s.aliases)) if fuzzy else None

    def nearest(self, lon: float, lat: float, k: int = 1) -> list[tuple[Station, float]]:
        d = dist_m(lon, lat, self._lon, self._lat)
        idx = np.argsort(d)[:k]
        return [(self.stations[i], float(d[i])) for i in idx]


def _tokens_match(guess: str, alias: str, min_len: int = 4) -> bool:
    """Every guess token is a prefix of (or prefixed by) some alias token, order-free."""
    at = alias.split()
    return all(any(len(min(g, t, key=len)) >= min_len and (t.startswith(g) or g.startswith(t)) for t in at)
               for g in guess.split())


def _uic_from_stop_id(stop_id: str) -> str | None:
    tail = stop_id.rsplit(":", 1)[-1].split("_")[0]
    return tail if tail.isdigit() else None


@lru_cache(maxsize=1)
def load() -> StationRegistry:
    by_uic: dict[str, Station] = {}
    unkeyed: list[Station] = []

    # GTFS first: it carries stop_pk (needed for stop_times joins) and every platform variant.
    con = sqlite3.connect(paths.GTFS_DB)
    for pk, stop_id, name, name_nl, name_en, lat, lon in con.execute(
        "select stop_pk, stop_id, stop_name, stop_name_nl, stop_name_en, stop_lat, stop_lon from stops"
    ):
        uic = _uic_from_stop_id(stop_id)
        aliases = {normalize(x) for x in (name, name_nl, name_en) if x}
        st = by_uic.get(uic) if uic else None
        if st is None:
            st = Station(uic, name_nl or name, float(lon), float(lat))
            if uic:
                by_uic[uic] = st
            else:
                unkeyed.append(st)
        st.gtfs_stop_pks.append(pk)
        st.aliases |= aliases

    # OSM: add geometry-only stations and extra aliases (name:nl / name:fr / name).
    for f in json.loads(paths.OSM_STATIONS.read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        aliases = {normalize(p[k]) for k in ("name", "name:nl", "name:fr", "name:en") if p.get(k)}
        uic = str(p["uic_ref"]) if p.get("uic_ref") else None
        st = by_uic.get(uic) if uic else None
        if st is None:
            # bilingual OSM names look like "Bruxelles-Midi - Brussel-Zuid": split them too
            for a in list(aliases):
                aliases |= {x.strip() for x in a.split(" - ") if x.strip()}
            st = Station(uic, p.get("name:nl") or p.get("name") or "?", float(lon), float(lat))
            if uic:
                by_uic[uic] = st
            else:
                unkeyed.append(st)
        st.aliases |= aliases

    return StationRegistry(list(by_uic.values()) + unkeyed)
