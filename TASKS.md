# Task Log — Train Localization Without GPS

Working task register for the team. Task IDs are stable — reference them in
later requests (e.g. "do H3", "I2 is done, what's next").

Companion docs — don't duplicate them here:
- [`WORKLOG.md`](WORKLOG.md) — living approach/state doc (what we believe and
  why, what's been tried and rejected). **Read before starting new work.**
- [`DATA_SCHEMA.md`](DATA_SCHEMA.md) — structural schema, no data.
- [`datasets/PARTICIPANT_BRIEF.md`](datasets/PARTICIPANT_BRIEF.md) — the ask.

Status key: `TODO` · `WIP` · `DONE` · `BLOCKED` · `DROPPED`

---

## 0. Where we are

**Both lanes are implemented and joined: hydration (Phase 3) is live.**

*Absolute* (`absolute/`, run with `.venv/bin/python scripts/run_warm.py`):
no IMU used — GTFS picks the train and destination, OSM gives the path, a
schedule-timed trapezoid gives position, and the 500 m-out call falls out of
the same profile. Scores on the 50 practice legs (44 good-GT):

| metric | baseline (`ic830_00` only) | absolute-only, all legs | **+ hydration (Phase 3)** |
|---|---|---|---|
| position, median-of-medians (good GT) | 974 m | 324 m (mean-of-medians 687 m) | **202 m** (mean 470 m) |
| route correct / lock-in | — | 44/50, 0 s | **47/50**, 0 s |
| station call detected / median \|timing\| | 0/1 | 40/50, 17 s | **42/50**, 19 s — the 7 misses with a correct route are **unscoreable** (500 m reference moment falls in a GT gap) |

*Hydration* (`absolute/hydrate.py`, joint, 2026-09-14 13h): the shape's
segment boundaries are fitted onto the candidate OSM path by exact DP —
turns pin, believed stops hold, cell anchors and the schedule trapezoid pull
weakly. The same fit cost, per segment, re-ranks the GTFS shortlist: the IMU
turn sequence tells opposite directions apart where timing cannot
(`ic2809_03`, `ic4112_00` fixed). Remaining route misses: 2 same-hop/same-minute
pairs (`ic2035`/`s33 2963`, `ic3033`/`ic2633`) and `l1679_02` (timing prefers
the wrong direction by 253 s; shape prefers the right one, but weighting it
enough to win there flips `ic2809_04`). Biggest remaining position errors are
legs where the stationary detector misses the platform dwell entirely
(`ic3013_03`: one 235 s "moving" straight covering the dwell) — the schedule
prior is what saves those.

Absolute-only picture (kept for reference): where its error sat: (1) 6 wrong GTFS picks — 3 are
same-destination/same-hop pairs nothing in this leg can separate
(`ic2035`/`s33 2963`, `ic536`/`s2 3785`, `ic3033`/`ic2633`), 3 are
opposite-direction pairs on **cell-less** legs (`ic2809_03`, `l1679_02`,
`ic4112_00`) that need the motion lane's initial heading; (2) the platform dwell
at the start of every leg — timing is a schedule prior, and the motion lane's
`shape.json` `moving` flags now exist to replace it (`solve.py::motion_window`
is already wired for them, but nothing feeds it yet); (3) four hairpin routes
in OSM (N1).

*Motion* (`motion/shape_stream.py`, causal, stdlib-only, ~200x realtime, plus
`motion/validate_shape.py`): Phase 2 + the H2 speed prior. Turn topology is
solid (net heading error median 10 deg); the length prior is weak (median 39 %)
— see `WORKLOG.md` → Motion lane. Task detail in the M/O/H2 rows.

**The two lanes now run jointly, not separately** (`scripts/run_joint.py`,
2026-09-14): per leg, motion writes `shape.json` first, then the absolute
solver runs and reads it (`motion_window` already preferred real `moving`
segments over the schedule-dwell prior when present — now it always gets
the chance to). A standalone motion-only or absolute-only run no longer
makes sense, because both files feed the one hydration step that snaps to
OSM; `motion/shape_stream.py` and `scripts/run_warm_gui.py` still run
stand-alone from a shell for lane-local debugging, but the web GUI's "New
run" panel only launches the joint script now. Phase 3 (hydration, §5) is
what turns the shape into scale-correct position and lets cell anchors and
the turn sequence re-rank the GTFS shortlist. **Hydration (H3–H7) is done**
— `solve_warm` reads `work/<leg>/shape.json` when present and falls back to
the schedule trapezoid otherwise (`NO_HYDRATE=1` env var forces the fallback
for A/B runs). Note `motion_window` no longer reads the shape's first/last
`moving` segment: that made the trapezoid *worse* (324 → 530 m) because the
first flicker fires long before the train rolls; hydration consumes the flags
properly instead.

**And they now run as one stream, not one after the other** (`joint/stream.py`,
`run_joint.py --stream`, **H8**): shape and anchors advance on a single
leg-time clock and the hydration DP re-fits the prefix as they go, so hydrated
points are emitted while the leg plays instead of all at the end. That is what
the GUI launches; the shell default stays batch for the 50-leg sweep. The
submission is produced the same way in both modes, so scores are identical.

**Decision taken (A):** we submit **`latitude,longitude`**, not
`distanceAlongTrackM`. The scorer projects onto its own polyline, so we never
have to reproduce the organizers' distance origin — and it makes the "will
scoring legs ship polylines?" question (X7) moot for *our* output: the
organizers need them to score at all.

**The finding that most shapes the plan: 24 of 50 practice legs have zero
cell samples** (§11). Cell-based positioning can't be the backbone.

### Pipeline (this is the whole idea)

```
IMU (accel+gyro, 493 Hz)
  └─ Phase 2  orientation → turn/straight SHAPE  (topology, no scale)
                    │
cell + wifi ────────┼─ Phase 3  HYDRATION: stretch/pin shape using
  (sparse, ±radius) │            position estimates ± radius
                    ▼            → scaled shape + distance-along-time
              Phase 4  snap to OSM network → align to GTFS
                    ▼
     position.csv        station_calls.csv      routeGuess
     (Phase 4)           (Phase 6)              (Phase 5)
```

The shape carries *every* leg (IMU is always present); cell/wifi only set the
scale where they exist. That ordering is the point of the redesign — see
`WORKLOG.md` steps 2–3.

### Two engineers, two lanes

Owner column: **M** = motion lane, **A** = absolute lane, **J** = joint (pair
on it). The lanes meet at two files only — `shape.json` and `anchors.json`,
specified in `WORKLOG.md` → Data contracts.

1. **Both, first 30 min** — freeze the contracts, commit stub producers (**I7**).
2. **Parallel** — M: `I1` → `O*` → `M*`. A: `I2` → `I3` → `P1 P2`. Each
   validates against a stub of the other's file.
3. **Converge** — real shape + real anchors meet in hydration. Pair on `H4`.
4. **Parallel again** — M takes `T*` stations, A takes `R*` routes + `X*`.

No shared file ownership: `I1` is M's alone, `I3` is A's alone.

---

## 1. Context (from `datasets/PARTICIPANT_BRIEF.md`)

Estimate a train's position over time from IMU + cell + WiFi only — **no GPS**.
Track-constrained: the real Belgian rail network (OSM) is provided, so the
unknown is mostly *distance along track*, not free-space position.

**Inputs** — see `DATA_SCHEMA.md` for exact columns.
- `datasets/practice/` — 50 legs: `sensors.db` (accel/gyro/cell/wifi) +
  `meta.json` + `ground_truth.csv` (answer key, dev only).
- `reference_data/osm/` — 25,024 rail LineStrings, 717 station/halt Points.
- `reference_data/gtfs_schedule/nmbs_schedule.sqlite` — 60,050 trips,
  754,730 stop_times, valid 2026-07-03..2026-12-12.
- `reference_data/celltower/flanders_cells.csv` — 39,006 OpenCelliD rows.
- `starter_kit/baseline_solve.py` — deliberately weak working example.
- `scorer/` — **the organizers' real scorer** (`scorer.py` + `metrics.py`),
  plus `scorer/polylines/<leg_id>.json` (true route geometry + `routeLengthM`
  for all 50 practice legs). Run:
  `python scorer/scorer.py --leg-id <leg_id> --track cold|warm --submission-dir <dir>`

**Outputs per leg**: `position.csv` (`epochMillis` + **either**
`distanceAlongTrackM` **or** `latitude,longitude`, optional `routeGuess`) and
`station_calls.csv` (`epochMillis, stationNameGuess`).
Layout: `<team_name>/<cold|warm>/<leg_id>/position.csv`

Each leg is **one hop** — a single `stationFrom` → `stationTo` pair (see
`meta.json`). There is no multi-station sequence inside a leg.

**Two independent leaderboards**: *warm start* (start station + coords + time
given, route unknown) and *cold start* (nothing given). Not averaged.
Entering both is optional.

### Scoring dimensions

Read from `scorer/metrics.py` — these are the organizers' exact rules, not
inferences.

| # | Dimension | Track | Notes |
|---|-----------|-------|-------|
| 1 | Trip/route discovery | both | `routeGuess` vs `meta.lineName` (e.g. `ic4112`). **Last non-null row** scores. Compared after lowercase + `-`/`_`→space + whitespace collapse, then **exact equality**. |
| 2 | Speed of discovery | both | `lockInTimeS` = earliest row after which the guess never changes. **Only computed if the final guess is correct**, so a wrong final guess scores nothing here. |
| 3 | Position accuracy | both | median/mean/max \|error\| along track, over rows where GT is defined. |
| 4 | Time to first fix | cold | First row within **1000 m**. Warm gets `"not applicable"`. |
| 5 | First-fix accuracy | cold | Error at that first qualifying row. |
| 6 | Station detection | both | **Not arrival detection.** Reference moment = when GT crosses `routeLengthM − 500 m`. Only the **first** call chronologically is scored, ±**90 s**. Name match is substring, either direction. |

Consequences worth internalizing:
- Rows where GT is undefined (tunnel gaps >60 s) are **skipped, not
  penalised** — emit densely, there's no cost to covering a gap.
- Metric #6 is a *one-shot 500 m-out prediction of the leg's destination*.
  Nothing scores actually detecting the arrival, and extra calls are ignored
  (`nCallsInLeg` is reported but unscored).
- Submitting `latitude,longitude` instead of `distanceAlongTrackM` makes the
  scorer project onto the leg's polyline itself (`scorer/scorer.py` →
  `project_lonlat_to_distance`). See **X7** — this may remove the need to
  pick an OSM route at all.

### Rules / constraints

- OSM + GTFS + celltower CSV: use freely.
- `meta.json`, `ground_truth.csv` and **`scorer/polylines/`** are off-limits
  as algorithm inputs — they're labels. The polylines are the true route
  geometry per practice leg; using them would be self-scoring. Dev-time
  measurement/calibration only, and never one leg's truth as input to
  another scored leg.
- Any language/toolchain; submission is plain CSV.
- The baseline cheats by reading `lineName`/`stationTo` from `meta.json`.
  Those fields won't exist on scoring legs.
- Don't calibrate accuracy against `degraded`/`bad` legs (§11).

### Timeline (hard)

- **16h00** — final scoring dataset handed over (`datasets/scoring_release/`).
- **17h00** — final submission, over Teams chat.
- Questions → Steven Lauwereins.

---

## 2. Phase 0 — Setup & recon

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| S1 | Python env with pandas/numpy (+ shapely/scipy) | J | DONE | `.venv` via `uv venv .venv --python 3.12` + `uv pip install numpy pandas scipy shapely matplotlib pyproj`. No root needed. Always run as `.venv/bin/python`. |
| S2 | Run `baseline_solve.py` on one practice leg end-to-end | A | DONE | Baseline scores 974 m median on `ic830_00`; scorer runs in-process from `absolute/harness.py`. |
| S3 | Locate/obtain the scorer | A | DONE | It's `scorer/` — organizers' real logic, not a reimplementation. Tolerances now known exactly (§1); I3 drops to a thin wrapper. |
| S8 | Read `scorer/metrics.py` and confirm every task's assumed metric matches | A | DONE | Metric #6 turned out to be a 500 m-out prediction, not arrival detection → rescoped T1–T6. |
| S4 | Dump `sensors.db` schema + sample rates | — | DONE | → `DATA_SCHEMA.md`. Accel **and** gyro ~493 Hz, identical row counts per leg. |
| S5 | Inventory all 50 legs: duration, length, GT quality, cell/wifi coverage | — | DONE | → §11. |
| S6 | Inspect GTFS sqlite schema | — | DONE | → `DATA_SCHEMA.md`. Train number in `trips.trip_short_name`; `calendar_dates` authoritative. |
| S7 | Inspect OSM network + station geojson structure | — | DONE | → `DATA_SCHEMA.md`. Routable graph is I2. |

## 3. Phase 1 — Infrastructure (blocks everything else)

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| I7 | **Freeze the two data contracts + commit stub producers** | J | DONE | anchors side is the real producer (`absolute/anchors.py`), written to `work/<leg>/anchors.json` by the warm solver. `shape.json` is read opportunistically by `absolute/solve.py::motion_window` — `motion/shape_stream.py` now writes it for real, so both contracts have real producers. |
| I1 | Leg loader: `sensors.db` + `meta.json` → dataframes on a common time grid | M | TODO | Pick the resample rate: 493 Hz × 1800 s ≈ 900k rows/leg/sensor. |
| I2 | Track model: OSM → linestrings with cumulative distance; project lat/lon ↔ (edge, distance-along) | A | DONE | `absolute/track.py`. 250k nodes / 257k edges; stitching at ≤8 m collapses 203 → **48** components, largest 249k nodes. 23/50 legs within 2 % of true length; the rest → N1. |
| I3 | Wrapper around `scorer/scorer.py` — import `score_leg()` directly, don't shell out per leg | A | DONE | `absolute/harness.py` — imports `scorer.score_leg`, flattens to one row per leg, prints a summary. |
| I4 | Submission writer: exact `<team>/<track>/<leg_id>/` layout + columns | A | DONE | `absolute/submission.py`. We emit **lat/lon**, not `distanceAlongTrackM` — see §0 / decision log. |
| I5 | Batch runner: all 50 legs → metrics table + per-leg diagnostics | A | DONE | `scripts/run_warm.py` (absolute-only) and `scripts/run_joint.py` (motion+absolute, now the default) solve + score all 50 legs in a couple minutes; table saved to `work/warm_scores.csv`. |
| I8 | Merge the two lane runners into one joint run per leg | J | DONE | `scripts/run_joint.py`: motion's `run_leg()` writes `shape.json`, then `solve.solve_warm()` reads it and writes `anchors.json` + submission — same `RunWriter` leg entry (`lane="joint"`), so the GUI shows shape+anchors+hydrated+score together. Wired into `web/`'s "New run" panel (`LANES` in `server.mjs` is now one entry). Order is load-bearing: motion must finish before `motion_window()` reads `shape.json`. |
| I6 | Name-matching layer: GTFS names are *French* (`Anvers-Central`), OSM + leg ids are *Dutch* (`antwerpen_centraal`) | A | DONE | `absolute/stations.py`: **UIC join** — GTFS `stop_id` `gs:nmbssncb:8896008` ↔ OSM `uic_ref`. 672/835 stations keyed. Name fallback = scorer normalisation + order-free token prefixes (`aspere gavere` → `Gavere-Asper`). 39/39 leg station names resolve. |

## 4. Phase 2 — Motion shape from IMU (`WORKLOG.md` step 2)

Whole phase is the **motion lane**. Output is `shape.json` — unscaled route
topology, no length or absolute position. Works on every leg; nothing here
depends on cell/wifi, so this lane never waits on the other.

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| O1 | Orientation recovery: gravity direction + longitudinal axis → rotate sensor axes to track axes | M | DONE | `motion/shape_stream.py`. Turns need only the **up** axis (`yaw = -dot(gyro,up)`), so O1 never gated M1. Gravity EMA must be slow (tau 60 s) or it eats the train's own acceleration. Lateral/forward axes come from the yaw-rate regression. |
| O2 | Handle orientation changes mid-leg (phone moved/picked up) | M | DONE | Tilt of the gravity EMA vs a reference >15° → `orientation_ok=false`, axes re-estimated, confidences halved. 0 pose changes on all 50 practice legs. |
| M1 | Turn segmentation: integrate yaw rate → left/right/straight events + signed `turn_deg` | M | DONE | Hysteresis state machine (enter 0.52 °/s, exit 0.26 °/s, min 2.5 s / 2.5°). Net heading error vs true polylines: **median 10°, p90 37°** over 44 good legs. |
| M2 | Stationary detector: rolling accel variance → `moving` flag per segment | M | DONE | High-pass accel RMS over a 2 s trailing window + gyro RMS, gate = max(0.085, 1.8 × running quiet floor), 2.5 s hysteresis. |
| M3 | Write `shape.json` to the frozen contract | M | DONE | `motion/shape_stream.py` → `work/<leg_id>/shape.json` (+ `shape_trace.csv`, `shape.svg`, `shape_stream.jsonl`). One extra field: `speed_source`. Also writes a `work/runs/<run_id>/` copy + live `events.ndjson` for the `web/` viewer (`--no-gui` opts out). |
| M4 | Sanity-check shapes against GT track geometry on `good` legs | M | DONE | `motion/validate_shape.py` (dev-only). Turn order/heading match well; **length** does not (see H2). Note: `scorer/polylines` chunks sometimes run backwards — reversals >90° are ordering artifacts, not turns. |

## 5. Phase 3 — Shape hydration (`WORKLOG.md` step 3) — new core

Turns the unscaled shape into metric geometry: give every segment a length.
Turn segments act as high-confidence anchors; straight segments get stretched
(or held flat, if stopped) to fit absolute estimates.

**Where the lanes meet.** Reads `shape.json` + `anchors.json`. `H4` is paired
on, not split — this is where the idea lives and a bad hand-off costs most.
`H1`/`H2` are lane-local because each writes into its own side's file.

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| H1 | Write `anchors.json`: cell + wifi estimates as multi-hypothesis candidates with radius | A | DONE | `absolute/anchors.py`. 10 s bins, multi-candidate. On `ic536_02`: 108 anchors, 87 % contain the true position within radius, centroid error median 931 m — cell is coarse. |
| H2 | Segment length prior from IMU: speed estimate | M | DONE (weak) | Accel integration and vibration energy both **fail** (see `WORKLOG.md` → Motion lane). What works: `v = a_lat/omega` in curves + gyro-derived cant correction → median 39% speed error, **turns only**. Dead-reckoned leg length: median 39% error, p90 71%. Scale must come from anchors/GTFS. |
| H3 | Stopped-vs-moving resolution per straight segment | J | DONE | `absolute/hydrate.py::_believed_stops`. A `moving:false` segment gets the stop penalty when ≥15 s, or when the ±20 s window around it is ≥50 % stopped (platform-dwell flicker); 2–5 s flicker at 25 m/s (`ic536_02`) is treated as moving. Penalty, not constraint — turns/anchors can overrule. |
| H4 | Hydration solver: assign lengths so the shape fits all anchors within their radii | J | DONE | `absolute/hydrate.py::hydrate` — exact DP on a ≤1600-cell distance grid. Costs in metres-of-penalty: turn match (50 m/deg beyond a 3° + drift deadband), stop (3/m), speed prior to mean moving speed (0.1/m) + IMU curve speed (0.1/m), cell anchors (0.5/m outside radius, capped 1 km), schedule trapezoid prior (0.3/m, capped 1 km), start/end slack. Hairpin artefacts in the OSM heading (>15°/25 m) are dropped. Practice warm: **324 → 202 m** median-of-medians, 687 → 470 m mean. |
| H5 | Per-segment confidence out of the solver | J | DONE | `sigma_m` per knot from forward+backward DP (softmin, T=150 m). Written to `hydrated.json` knots; not yet used downstream. |
| H6 | Output: hydrated polyline + `distance-along-time` curve | J | DONE | `work/<leg>/hydrated.json` (`knots[{t,distance_m,sigma_m}]`, cost, notes) + the GUI `hydrated.json` polyline with `knots`. `solve_warm` samples the curve every 5 s for `position.csv`; the 500 m-out call is the curve crossing L−500. |
| H7 | No-anchor fallback: leg with zero cell **and** useless wifi | J | DONE | Zero anchors is just fewer DP terms; the schedule prior + turns/stops carry it. Verified: all 24 cell-less legs solve; `ic2809_05` 1361 → 180 m with no anchors. |
| H8 | Stream it: shape + anchors on one leg-time clock, hydrated points out | J | DONE | `joint/stream.py` (`run_joint.py --stream`, what the GUI launches). IMU feeds `ShapeTracker`, cell anchors are released as their timestamp passes, and the DP re-fits the **prefix** every `--refit-every` leg-seconds (`hydrate(pin_end=False, grid_cells_max=350)` — no end pin because the train hasn't arrived, coarse grid because it runs ~18x/leg). The open segment is included provisionally (`ShapeTracker.preview_segment()`), else the map stalls through long straights. Route shortlist re-ranks live off the prefix fit: `ic4112_00` flips from the wrong `s1 1985` to the correct `ic4112` the moment the first anchors land at +210 s. Submission still comes from `solve_warm()` on the completed contracts — identical score (`ic830_00`: 203.9 m both ways). |

## 6. Phase 4 — Map matching & fusion (`WORKLOG.md` step 6) → metric #3

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| N1 | Stitch the OSM graph into routable components | A | WIP | Stitching done (I2). Remaining failures are **hairpin reversals** at junctions (Zedelgem↔Torhout 7.8 vs 10.4 km, Antwerpen-Zuid↔Linkeroever 3.1 vs 5.4 km) — same OSM line, physically impossible turn. Needs turn-angle penalties (edge-based dijkstra). |
| N2 | Precompute curvature signature per candidate OSM route | A | TODO | What M1's turn sequence gets matched against. Build it to consume `shape.json` directly. |
| N3 | Match hydrated shape → OSM route + offset; emit `distanceAlongTrackM` | J | DONE | Folded into hydration: the fit *is* the snap — knots are distances along the chosen OSM path, emitted as lat/lon (scorer projects). `sigma_m` (H5) not yet weighted in. |
| N4 | Clock-drift handling between device `epochMillis` and GTFS wall-clock | A | DONE | Measured, not corrected: t0 sits within ±2 min of scheduled departure on all legs, symmetric. No clock-drift term needed at this accuracy. |
| N5 | Align to GTFS timetable (arrival times along the matched route) | A | DONE | Folded into `absolute/gtfs.py::rank_hops` + `absolute/solve.py::motion_window`: scheduled dep/arr define the timing prior. |
| N6 | Tunnel / long-gap behaviour — keep emitting sane estimates | J | TODO | GT has >60 s gaps; we still have to output rows. |

## 7. Phase 5 — Route discovery (metrics #1, #2)

`P1–P3` feed `anchors.json` and belong to the absolute lane's early parallel
block; `R*` is that lane's endgame work.

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| P1 | Cell tower → position prior: join `cell_samples.cellId` against `flanders_cells.csv` | A | DONE | `absolute/cells.py`. Exact `(radio, cell)` match resolves 35 % of distinct ids. |
| P2 | Resolve the cellId join ambiguity | A | DONE | The ambiguity worry was unfounded — every exact match is **unique**. The real problem is coverage: West-Flanders rides (`ic2315`, `s51_761`) hit 0 %. **eNodeB fallback** (`cellId >> 8`, centroid of sibling cells) lifts 35 % → 56 % of ids. |
| P3 | WiFi AP fingerprinting — do BSSIDs recur across legs/stations? | A | TODO | Low expectations (as few as 1 distinct BSSID on a leg). Bonus signal. |
| R1 | Candidate trip generation: date → `calendar_dates` → active trips + stop patterns | A | DONE | `absolute/gtfs.py::hops_from` — `calendar_dates` → active trips → every downstream call (up to 4) per trip departing the start station ±10 min. |
| R2 | Eliminate candidates as the leg progresses: turn sequence, leg duration, direction, start/end spacing | A | DONE | Timing shortlist re-ranked by **median** cell-anchor misfit against each candidate's OSM path, 0.2 s/m (`solve.py::choose_hop`). 41 → **44/50**. Mean misfit was rejected: one bad eNB centroid dominated. Remaining misses: same-destination pairs (unresolvable here) and opposite directions on cell-less legs (→ motion lane heading, feed into `rank_hops`). |
| R3 | Match the leg's duration + endpoints against GTFS consecutive `stop_times` pairs | A | DONE | `rank_hops`: `\|t0 − dep\| + 1.5·\|T_obs − (sched + 110 s)\| + 120 s per skipped call`. The 110 s is measured leg overhead (dwell + tail). 40 → 41/50 top-1; truth in top-2 on 47/50. |
| R4 | Lock-in policy — commit early, then **never change** | A | DONE | By construction: `routeGuess` filled from row 0, never changes → `lockInTimeS = 0` on every correct leg. |
| R5 | Fallback guess when confidence stays low | A | DONE | Always guess; the top-ranked hop is emitted even at low confidence. |
| R6 | Format the guess as `meta.lineName` — lowercase line id, e.g. `ic4112`, `l1679`, `s51_785` | A | DONE | `Hop.route_guess`: `route_short_name + trip_short_name`, space only when the route name has digits → `ic830`, `l1679`, `s51 761`. Verified against all 11 practice lines after scorer normalisation. |

## 8. Phase 6 — The 500 m-out call (metric #6)

Rescoped after reading `scorer/metrics.py`. The metric is **one prediction per
leg**: name the destination, timed within ±90 s of the moment the train is
500 m from the leg's end. Only the first row of `station_calls.csv` counts.
Motion lane's endgame work — in practice it fell out of hydration (Phase 3)
and was closed by the absolute engineer on 2026-09-14: the call is where the
hydrated curve crosses `L − 500`, nothing more.

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| T1 | Predict the 500 m-out moment: `remaining_distance ≤ 500 m` off our own position estimate | M | DONE | `solve.py::solve_warm`: the call fires when the hydrated distance-along-time curve (`Hydrated.time_at_distance`) crosses `L − 500`; trapezoid fallback does the same on its profile. Fires while moving, before arrival. |
| T2 | Estimate leg length so "remaining" is computable without `routeLengthM` | M | DONE | `L` = length of our OSM path start→destination (`track.Path.length_m`). Against `routeLengthM` (dev check only): median 109 m short — the OSM station node vs the polyline end — worth ~4 s, no correction applied. Two outliers are hairpin routes (N1: `ic2315_00` −2.7 km, `ic3033_03` −2.4 km) and still land within tolerance. |
| T3 | Name the destination: identify `stationTo` from route + direction | M | DONE | Destination = `hop.to.name` from the GTFS pick (R3), emitted as-is; the scorer's substring match absorbs FR/NL and hyphenation (`Brussels Airport-Zaventem` ↔ `Brussels-Airport`). |
| T4 | Emit exactly one row, at the best single moment | M | DONE | Exactly one row per leg; on a leg with no route we write an empty `station_calls.csv` rather than a wrong early call. |
| T5 | Validate against the scorer across practice legs | M | DONE | 43 scoreable legs with a correct route: signed error median −1.5 s, mean +6.7 s, p90 |err| 43 s, tolerance is ±90 s. **42/50 detected.** The 8 misses: 6 have `referenceTimeMs = None` (500 m reference moment falls in a GT gap → unscoreable for anyone), 1 wrong route (`l1679_02`), 1 timing 124 s on a *bad*-GT leg (`ic536_01`). Nothing left to win here on the practice set. |
| T6 | Decide whether stop/dwell detection is still worth building | M | DONE | Decision: **not built as its own deliverable.** Stop/dwell detection lives in the motion lane's `moving` flag and is consumed by hydration (H3); the platform-dwell miss it still has (`ic3013_03`) is an M2 issue, not a Phase 6 one. |

## 9. Phase 7 — Tracks, first fix, and the final round

### Warm start

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| W1 | Warm-start entry point: start station/coords/time → one `source: "warm_start"` anchor | A | DONE | `solve.py::WarmStart` — only station name, coords, t0. Written as the first anchor. |
| W2 | Confirm route discovery narrows sharply given origin + departure time | A | DONE | 2–25 candidates per leg (median 4). Timing alone: 41/50. |
| W3 | **Decide: warm only, or both tracks** | J | DONE | **Both tracks.** Cold reuses the entire warm pipeline behind an inferred start (`absolute/cold.py`), so it cost ~1 h and cannot hurt the warm leaderboard. Legs with no usable cell get **no cold folder** — a wild fix scores worse than none. |

### Cold start / first fix (metrics #4, #5) — W3 decided: built

Practice cold track (12 legs with a fix, 7 good-GT): position 178 m
median-of-medians, TTFF 5 s, first-fix error 87 m, 9 routes, 11 station
calls. `scripts/run_scoring.py --dataset … --out … [--score]` runs both tracks.

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| F1 | Fastest coarse fix from the first cell/WiFi samples alone | A | DONE | `absolute/cold.py::station_candidates`: cell anchors from the first 60 s (radius ≤6 km), stations ranked by distance outside the towers' radii; t0 = first sensor sample. Fix on **12/50 practice legs** (the 26 with cell minus West-Flanders zero-coverage rides and `ic536_00`, whose first tower shows up at +219 s). First row is at t0 → TTFF 5 s median, first-fix error 87 m median. |
| F2 | Snap the coarse fix to the nearest plausible track segment (<1 km to count) | A | DONE | The fix *is* a station (OSM/GTFS coords), so it is on the track by construction; the position curve then follows the hydrated OSM path. |
| F3 | Tune emit-now vs wait: fast+wrong and slow+right both lose | A | DONE | Emit immediately. Adjacent-station ambiguity (Centraal/Berchem, Noord/Centraal) is resolved by scoring each start hypothesis with the full route ranking (`choose_hop`: timing + anchors + shape fit): start station right on **11/12**, routes 9/12 (the 2 misses are the same-minute GTFS pairs, as on warm). Miss: `ic536_01` picks Brussel-Noord for Centraal — underground, towers 1 km coarse. |

### Final round (16h00 → 17h00)

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| X1 | Freeze the pipeline ~15h30; no algorithm changes after handover | J | TODO | Freeze ~15h30. After that only `scripts/run_scoring.py` runs; no weight changes. |
| X2 | Smoke-test on a practice leg with `meta.json`/`ground_truth.csv` **removed** | A | DONE | `scripts/run_scoring.py` run on a scratch copy of two legs holding only `sensors.db` + a `meta.json` with just `stationFrom/coordFrom/tFromEpochMillis`: warm 2/2, cold 1/2 (the other has no cell), CSVs valid. Static check: no `ground_truth|routeLengthM|lineName|stationTo|polylines` reference in `absolute/`, `motion/`, or the runners (only a docstring). `paths.RAIL_DATASET_DIR` points the solvers at any leg folder; the scorer keeps reading practice. |
| X3 | Run on `datasets/scoring_release/` immediately at 16h00 | A | READY | `RAIL_DATASET_DIR` not needed — `.venv/bin/python scripts/run_scoring.py --dataset datasets/scoring_release --out work/final` (runs the shape generator per leg first; ~5 s/leg). Practice dry run reproduces 202 m / 47 / 42 warm and 178 m, TTFF 5 s cold. |
| X4 | Validate every output CSV (columns, row counts, no NaNs, monotonic time) | A | DONE | `run_scoring.py::validate`: columns, ≥2 rows, no NaN, monotonic time, lat/lon inside Belgium, routeGuess non-empty, ≤1 station call. A failing folder is deleted, never shipped; `run_report.csv` lists every leg's outcome. |
| X5 | Package `<team_name>/<cold\|warm>/<leg_id>/…`, send over Teams before 17h00 | A | READY | Output already in `<out>/<team>/<cold|warm>/<leg_id>/` — zip `work/final/televic` and send over Teams. Team name defaults to `televic` (`--team`). |
| X6 | Verify graceful degradation on a **cell-less** leg | M | DONE | All 24 cell-less legs solve and score through the absolute path (empty anchors, schedule-only timing). |
| X7 | **Ask Steven: will scoring legs come with polylines?** | A | OPEN | **Still to ask Steven** (also: does the scoring release's `meta.json` carry the warm fields under the same names, and are the leg folder names label-free?). Moot for our output either way — we submit lat/lon, and the organizers need polylines to score at all. |

---

## 10. Decisions & open questions

| Date | Decision | Rationale |
|------|----------|-----------|
| | Team name: **TBD** | Needed for the submission folder name |
| | Tracks entered: **TBD** | Resolve via W3. |
| 2026-09-14 | Language/stack: **Python 3.12**, `.venv` via `uv` (no root). numpy/pandas/scipy/shapely | Scorer is Python; `uv` available on the locked-down host. |
| 2026-09-14 | Submit **`latitude,longitude`**, not `distanceAlongTrackM` | Scorer projects onto its own polyline; we never need the organizers' distance origin (A). |
| 2026-09-14 | Absolute lane = engineer B | Lane owners: motion = A, absolute = B. |
| | Lane owners: motion = **TBD**, absolute = **TBD** | Two engineers, two lanes (§0). Put real names here so `Own` column is unambiguous. |
| 2026-09-14 | Cut the pipeline at `shape.json` + `anchors.json` | Only clean seam between the two lanes; lets each side work against a stub of the other. Contracts in `WORKLOG.md`. |
| 2026-09-14 | Merge the two lane runners: one joint run produces both files per leg (**I8**) | Both are needed for hydration + the OSM snap; a standalone motion-only or absolute-only run has no use once the contracts are real. `scripts/run_joint.py` is now the default entry point, and the web GUI only launches it. |

Resolved by `scorer/` (2026-09-14):
- ~~Where is the scorer?~~ → `scorer/`, the organizers' own code.
- ~~Exact tolerances?~~ → `metrics.py`: 60 s max interp gap, 90 s station
  window, 1000 m first-fix threshold, 500 m approach distance.
- ~~`routeGuess` format?~~ → `meta.lineName`, e.g. `ic4112` (**R6**).
- ~~Blank vs wrong `routeGuess`?~~ → scored identically, so always guess (**R5**).
- ~~500 m-out submission channel?~~ → `station_calls.csv`; it *is* metric #6,
  and only the first row counts.

Still open:
- **Do scoring legs ship a polyline?** Decides whether we can submit
  `latitude,longitude` and let the organizers project (**X7**). This is the
  single highest-leverage unknown left — it could remove N2/N3 entirely.
- Will scoring legs also have cell gaps, in the same ride-wide pattern?
- Are scoring legs contiguous within a ride? Practice legs are **not** —
  `ic830` has 00,01,02,04,05,06; no 03.
- **Will scoring leg folder names still embed line + stations**
  (`ic830_00_kortrijk_ingelmunster`)? If so that is a label leak. We do not
  read folder names anywhere; ask Steven whether they will be anonymised.
- Some practice polylines start with an out-and-back spur (`ic2315_00`:
  GT begins at 2643 m). Harmless for lat/lon submissions; would bite anyone
  emitting `distanceAlongTrackM`.

## 11. Findings — per-leg survey (S5)

Measured across all 50 practice legs.

**Cell coverage is the big one.** `datasets/README.md` describes empty
`cell_samples` as "a handful of legs". It's **24 of 50**, and it's
ride-correlated, not random — every leg of `ic2809`, `ic2933`, `ic3013`,
`ic830`, `l1679`, `s51_785` has zero cell rows; every leg of `ic2035`,
`ic2315`, `ic3033`, `ic4112`, `ic536`, `s51_761` has some. Consequences:
- Cell can't be the primary positioning signal — hence shape-first (Phase 2)
  with cell as hydration anchors (Phase 3), not the backbone.
- Hydration needs a no-anchor path (**H7**) and cold start has nothing to
  bootstrap from on those legs (**F1**).
- Anything tuned on cell-rich legs will look far better in dev than it scores.

**WiFi is thin.** 13–1,074 rows per leg; 1–174 distinct BSSIDs. The sparsest
legs (`s51_761_04`: 25 rows / 1 BSSID) give essentially nothing.

**IMU is dense and uniform.** Accel and gyro both ~483–496 Hz on every leg,
identical row counts. ~900k rows per sensor on a 30-min leg — pick the
working resample rate early (I1). This uniformity is why the shape stage can
be the backbone.

**Ground-truth quality**: 44 `good`, 2 `degraded` (`ic2035_00/01`), 4 `bad`
(all of `ic536`). Awkward overlap: the `ic536` legs have the **richest cell
data** (1,593 and 1,887 samples) but untrustworthy ground truth — good for
developing the cell join (P1/P2), useless for measuring accuracy.

**Leg sizes**: 214 s – 1,834 s duration; 1.8 km – 51.8 km route length. A
30× spread, so anything with a fixed window length needs checking at both
ends.

## 12. Notes

- Baseline position error (constant-speed interpolation): median **974 m**.
  That's the bar to beat.
- Baseline's known failure: its station call landed **275 s** off →
  `detected: false` despite a correct name. Now explainable: the reference
  moment is 500 m before the leg's end, not the arrival, so a dwell-based
  detector is aiming at the wrong instant entirely (**T1/T2**).
- Scorer tolerances live in `scorer/metrics.py` as module constants —
  `MAX_INTERP_GAP_S`, `STATION_CALL_TOLERANCE_S`,
  `FIRST_FIX_ACCURACY_THRESHOLD_M`, `STATION_APPROACH_DISTANCE_M`. Read them
  rather than hardcoding our own copies.
- Ground truth has *gaps* (tunnels) for >60 s GPS outages; rows with
  `isInterpolated=true` are filled small (<60 s) gaps.
- `calendar_dates` — not `calendar` — is authoritative for whether a service
  ran on a given date.
- Task IDs changed with the hydration redesign: old `C1–C7` were resplit into
  `O*` (orientation), `M*` (shape), `H*` (hydration) and `N*` (fusion); old
  `R1/R1a/R2` became `P1/P2/P3`.
