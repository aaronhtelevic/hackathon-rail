"""R1/R3/R6 — which train is this? GTFS hop lookup and routeGuess formatting.

A leg is one hop (stationFrom -> next call). Given a start station and a start
time, list every scheduled trip departing that station near that time, with
its next call and scheduled arrival. That is the whole route-discovery
problem for warm start; cold start feeds a *set* of candidate stations in.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from . import paths
from .stations import Station, load as load_stations

TZ = ZoneInfo(paths.TZ)


@dataclass
class Hop:
    trip_pk: int
    route_short_name: str   # "IC", "L", "S51"
    train_number: str       # trip_short_name, e.g. "830"
    headsign: str
    frm: Station
    to: Station
    dep_ms: int             # scheduled departure from `frm`, epoch ms
    arr_ms: int             # scheduled arrival at `to`, epoch ms
    n_skipped: int = 0      # scheduled calls between frm and to that the leg ran through

    @property
    def route_guess(self) -> str:
        """Matches meta.lineName after the scorer's normalisation: 'ic830', 'l1679', 's51 761'."""
        r = self.route_short_name.lower()
        sep = " " if any(ch.isdigit() for ch in r) else ""
        return f"{r}{sep}{self.train_number}"

    @property
    def scheduled_duration_s(self) -> float:
        return (self.arr_ms - self.dep_ms) / 1000.0


def _service_day(t: dt.datetime) -> dt.datetime:
    """GTFS times can exceed 24:00; anything before 03:00 local belongs to the previous service day."""
    local = t.astimezone(TZ)
    if local.hour < 3:
        local -= dt.timedelta(days=1)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _gtfs_time_to_ms(day0: dt.datetime, hhmmss: str) -> int:
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return int((day0 + dt.timedelta(hours=h, minutes=m, seconds=s)).timestamp() * 1000)


def _connect():
    return sqlite3.connect(paths.GTFS_DB)


def active_service_ids(day0: dt.datetime) -> set[str]:
    date = day0.strftime("%Y%m%d")
    con = _connect()
    added = {r[0] for r in con.execute("select service_id from calendar_dates where date=? and exception_type='1'", (date,))}
    removed = {r[0] for r in con.execute("select service_id from calendar_dates where date=? and exception_type='2'", (date,))}
    return added - removed


MAX_DOWNSTREAM = 4  # how many calls past the next one to offer as destination candidates


def hops_from(station: Station, t_ms: int, before_s: float = 600, after_s: float = 600) -> list[Hop]:
    """Trips departing `station` in [t - before, t + after], one Hop per downstream call
    (next call first). Recording start sits within about +-2 min of the scheduled
    departure either way, so the window is symmetric."""
    reg = load_stations()
    t = dt.datetime.fromtimestamp(t_ms / 1000, TZ)
    day0 = _service_day(t)
    services = active_service_ids(day0)
    if not services or not station.gtfs_stop_pks:
        return []
    con = _connect()
    pks = ",".join(str(p) for p in station.gtfs_stop_pks)
    rows = con.execute(f"""
        select t.trip_pk, t.service_id, t.route_short_name, t.trip_short_name, t.trip_headsign,
               st1.departure_time, st2.stop_pk, st2.arrival_time
        from trips t
        join stop_times st1 on st1.trip_pk = t.trip_pk and st1.stop_pk in ({pks})
        join stop_times st2 on st2.trip_pk = t.trip_pk
             and st2.stop_sequence between st1.stop_sequence + 1 and st1.stop_sequence + {1 + MAX_DOWNSTREAM}
        order by t.trip_pk, st2.stop_sequence
    """).fetchall()
    out: list[Hop] = []
    seen = set()
    k_by_trip: dict[int, int] = {}
    for trip_pk, service_id, rsn, num, headsign, dep, to_pk, arr in rows:
        if service_id not in services:
            continue
        dep_ms = _gtfs_time_to_ms(day0, dep)
        if not (t_ms - before_s * 1000 <= dep_ms <= t_ms + after_s * 1000):
            continue
        to = reg.by_stop_pk(to_pk)
        k = k_by_trip.get(trip_pk, 0)
        k_by_trip[trip_pk] = k + 1
        key = (rsn, num, to.uic if to else to_pk, dep_ms)
        if to is None or key in seen:
            continue
        seen.add(key)
        out.append(Hop(trip_pk, rsn, num, headsign or "", station, to, dep_ms, _gtfs_time_to_ms(day0, arr), k))
    out.sort(key=lambda h: (abs(h.dep_ms - t_ms), h.n_skipped))
    return out


def _offset_hhmmss(day0: dt.datetime, t_ms: int) -> str:
    """Clock string for `t_ms` on `day0`'s service day, allowing the GTFS >24:00 convention."""
    s = max(0.0, (t_ms / 1000.0) - day0.timestamp())
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}"


def hops_anywhere(t_ms: int, before_s: float = 600, after_s: float = 600) -> list[Hop]:
    """Every scheduled hop in the feed departing *any* station inside the window.

    `hops_from` needs a start station; the cold track has one only when cell towers name it.
    With no towers the start is unknown, so the candidate set is the whole country and the
    shape + timetable have to do the discriminating (see `cold.hop_candidates_no_cell`).
    The departure_time bound is applied in SQL — the unbounded stop_times self-join is the
    difference between a second and minutes."""
    reg = load_stations()
    t = dt.datetime.fromtimestamp(t_ms / 1000, TZ)
    day0 = _service_day(t)
    services = active_service_ids(day0)
    if not services:
        return []
    con = _connect()
    rows = con.execute(f"""
        select t.trip_pk, t.service_id, t.route_short_name, t.trip_short_name, t.trip_headsign,
               st1.stop_pk, st1.departure_time, st2.stop_pk, st2.arrival_time,
               st2.stop_sequence - st1.stop_sequence - 1
        from stop_times st1
        join trips t on t.trip_pk = st1.trip_pk
        join stop_times st2 on st2.trip_pk = st1.trip_pk
             and st2.stop_sequence between st1.stop_sequence + 1 and st1.stop_sequence + {1 + MAX_DOWNSTREAM}
        where st1.departure_time between ? and ?
    """, (_offset_hhmmss(day0, t_ms - before_s * 1000),
          _offset_hhmmss(day0, t_ms + after_s * 1000))).fetchall()
    out: list[Hop] = []
    seen = set()
    for trip_pk, service_id, rsn, num, headsign, frm_pk, dep, to_pk, arr, n_skipped in rows:
        if service_id not in services:
            continue
        dep_ms = _gtfs_time_to_ms(day0, dep)
        if not (t_ms - before_s * 1000 <= dep_ms <= t_ms + after_s * 1000):
            continue
        frm, to = reg.by_stop_pk(frm_pk), reg.by_stop_pk(to_pk)
        if frm is None or to is None:
            continue
        key = (rsn, num, frm.uic or frm.name, to.uic or to.name, dep_ms)
        if key in seen:
            continue
        seen.add(key)
        out.append(Hop(trip_pk, rsn, num, headsign or "", frm, to, dep_ms,
                       _gtfs_time_to_ms(day0, arr), n_skipped))
    return out


# Measured on the 44 good practice legs (see TASKS.md §11): the recording covers
# platform dwell + run + ~30 s after arrival, so its span exceeds the scheduled
# hop time by about this much.
LEG_OVERHEAD_S = 110.0


def rank_hops(hops: list[Hop], t_start_ms: int, observed_duration_s: float | None = None) -> list[tuple[Hop, float]]:
    """Lower cost = better. Departure offset and duration mismatch both in seconds;
    duration carries more weight because it is the sharper discriminator, and
    skipping scheduled calls is rare so each skipped one costs extra."""
    scored = []
    for h in hops:
        off = abs(t_start_ms - h.dep_ms) / 1000.0
        cost = off + 120.0 * h.n_skipped
        if observed_duration_s is not None:
            cost += 1.5 * abs(observed_duration_s - (h.scheduled_duration_s + LEG_OVERHEAD_S))
        scored.append((h, cost))
    scored.sort(key=lambda x: x[1])
    return scored
