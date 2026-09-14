---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  section { font-size: 26px; }
  h1 { font-size: 42px; }
  h2 { font-size: 30px; }
  code { font-size: 0.85em; }
---

# Locating a train with no GPS
### From accelerometer/gyro/cell/wifi noise to lat/lon on the rail network

Belgian Railway Hackathon — team worklog recap

---

## The problem

- `sensors.db` per leg: `accel` + `gyro` (~493 Hz), `cell` (24/50 legs have **zero** rows), `wifi` (thin, 13–1,074 rows/leg)
- No GPS anywhere in the data — that *is* the task
- Public reference data allowed: GTFS timetable, OSM rail network + stations, OpenCelliD cell CSV
- `meta.json` / `ground_truth.csv` are off-limits (label leakage) — warm track exception: given `stationFrom`, `coordFrom`, `tFromEpochMillis`
- Output: lat/lon trajectory, `routeGuess`, station arrival/departure calls

---

## Approach: three lanes, two contract files

```
IMU (accel, gyro)  ──▶  motion lane  ──▶  shape.json   ──┐
                                                          ├──▶  hydration ──▶ fusion
cell, wifi, GTFS    ──▶  absolute lane ──▶  anchors.json ──┘
```

- **Motion**: unscaled route *shape* — turn/straight segments, no position
- **Absolute**: sparse, multi-hypothesis position *anchors* + GTFS route candidates
- **Hydration**: the join — stretches the shape onto a candidate OSM path using the anchors + schedule as pins
- Two engineers worked the lanes in parallel against frozen JSON contracts, so neither blocked on the other

---

## Motion lane: what actually worked

- **Turns are cheap, speed is expensive.** Yaw rate from a slow (τ=60s) gravity EMA needs no heading — turn sequence: median heading error 10°, p90 37°
- **Accelerometer integration for speed fails outright**: drifted to −15 m/s while the train did +32 m/s (real accel ≈0.15 m/s², swamped by grade/handling noise)
- **Vibration energy vs. speed: r = 0.18** — unusable, the recorder is hand-held
- **What works: curve geometry.** `v = a_lat / ω` while turning, correcting for cant (superelevation) → median 39% relative speed error, *only while turning*
- Net: trust the **topology**, not the scale — hydration has to supply scale

---

## Absolute lane: anchors from a noisy world

- **GTFS route discovery**: candidate trips from the start station ±10 min, cost = timing + skipped-call penalty → **41/50 top-1, 47/50 top-2**
- **Cell join**: exact `(radio, cell)` hits only 35% of ids; eNodeB centroid fallback → 56%. Capped at 1 km overshoot after one bad centroid derailed a whole fit
- **OSM rail network**: 203 disconnected components → stitched to 48; known gap is hairpin reversals at junctions (turn-angle penalty not yet built)
- **Station join**: GTFS UIC code = OSM `uic_ref` — exact match for 672/835 stations, fixing the French/Dutch name mismatch (`Anvers-Central` vs `Antwerpen-Centraal`)

---

## Hydration: where the idea actually lives

- Exact **dynamic programming** over path-distance at each segment boundary — not a particle filter; a 50-segment/25 km leg solves in ~1 s
- Every cost term is "metres of penalty" — turns, cell anchors, GTFS schedule trapezoid, stationary flicker — all exchange rates on one scale, capped so no single noisy prior can dominate
- **Trajectory checked back against the timetable**: the curve's own crossing of "left the platform" / "reached the destination" must agree with GTFS times for the *proposed* trip — this alone separated same-timing route ambiguities
- Streamed too (`joint/stream.py`): shape segments and anchors feed one leg-time clock, re-fit every few seconds — the live view proves the pipeline is causal, not just batch-fittable

---

## Results (practice set, warm track)

| Metric | Score |
|---|---|
| Route (`routeGuess`) correct | **48 / 50** |
| Station calls correct | 42 / 50 |
| Position error (mean of medians) | **310 m** |
| 500m-out timing call | median −1.5 s, p90 43 s (±90 s tolerance) |

- Biggest single wins: turn-pinned hydration (1875 m → 157 m on a curvy leg), schedule prior capped at 1 km (347 m → 232 m median), timetable back-check (47 → 48/50 routes)

---

## Cold track & wrap-up

- **Cold = warm behind an inferred start**: candidate stations scored by which one's full route ranking fits best — separates adjacent stations (e.g. Antwerpen-Centraal vs Berchem) that cell towers alone can't
- Fixes on **12/50** legs (11 correct station) — the rest have no usable cell signal in the first minute, and a wrong guess would cost more than skipping
- **Lesson**: no single sensor carries the task — cell is sparse, IMU has no scale, GTFS alone can't disambiguate same-timing hops. The win was fusing weak, capped priors under one DP objective and checking the *output* against the schedule that produced it
