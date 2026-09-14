# Work Log

Living record of the current approach + setup notes. Not a diary — sections
get edited/merged as understanding improves, not appended chronologically.
Check this before starting new work — avoid re-doing or re-proposing
something already covered here.

Reference: [`DATA_SCHEMA.md`](DATA_SCHEMA.md) — structural schema (no data)
for `practice/<leg_id>/sensors.db`, `meta.json`, `ground_truth.csv`,
submission CSVs, and `reference_data/` (GTFS sqlite, celltower CSV, OSM
rail network/stations).

## Setup

- Inspected `practice/ic536_00_brussel_noord_brussel_centraal/sensors.db`
  (accel_samples, gyro_samples, cell_samples, wifi_samples) and
  `reference_data/gtfs_schedule/nmbs_schedule.sqlite` (routes, trips, stops,
  stop_times, calendar, calendar_dates, feed_info) via `sqlite3 .schema`.
- Read `PARTICIPANT_BRIEF.md`, `datasets/README.md`, `meta.json`, and the
  `reference_data/{celltower,gtfs_schedule,osm}/README.md` files for field
  meaning/context.
- Wrote `DATA_SCHEMA.md` — structure-only reference for sensors.db, meta.json,
  ground_truth.csv, submission format, and all reference_data sources.
- Surveyed sensor coverage across all 50 practice legs (see `TASKS.md` §12).
  Non-obvious results:
  - **24 of 50 legs have zero `cell_samples`** — `datasets/README.md` calls
    this "a handful". It is ride-correlated, not random: all legs of ic2809,
    ic2933, ic3013, ic830, l1679, s51_785 are empty; all legs of ic2035,
    ic2315, ic3033, ic4112, ic536, s51_761 have cell data.
  - WiFi is thin — 13–1,074 rows/leg, 1–174 distinct BSSIDs.
  - Accel and gyro are both ~493 Hz with identical row counts per leg
    (synchronised sampling); ~900k rows/sensor on a 30-min leg.
  - Ground truth: 44 `good`, 2 `degraded` (ic2035), 4 `bad` (all ic536).
    The ic536 legs have the richest cell data but unusable ground truth.

## Run viewer (`web/`)

Svelte GUI + zero-dependency Node server that watch `work/runs/` and render a
solve as it happens — per-leg status, `shape.json` segments and `anchors.json`
candidates on one time axis, hydrated polyline over the anchor circles (with an
OSM rail layer under it), the `events.ndjson` tail and `score.json`.

Read-only about run *data*: algorithms write files, the GUI only reads them.
It can **start a run** — the "New run" panel picks a set of practice legs and
spawns `scripts/run_joint.py` — so a solve no longer needs a second terminal.
The command line lives in the server; the only client input is the leg list,
and each name must match a real `datasets/practice/<leg>/sensors.db` before it
becomes an argv item. Because it spawns processes the server binds loopback
(`RAIL_GUI_HOST` to change, `RAIL_GUI_LAUNCH=0` to disable).

Motion and absolute always run together now, not as separate lane runs
(`scripts/run_joint.py`, 2026-09-14): per leg, `motion.shape_stream.run_leg()`
writes `shape.json` first, then `absolute.solve.solve_warm()` runs and reads
it, writing `anchors.json` + the submission. Both calls share one
`RunWriter` leg entry (`lane="joint"`), so the viewer's per-leg pane already
shows shape + anchors + hydrated + score together without any GUI change.
Rationale: a standalone motion-only or absolute-only run produces only half
of what hydration needs, and hydration is what turns the shape into a
scale-correct position and lets it snap to OSM — so there's no point running
the lanes apart once both contracts are real.

**`--demo-speed X`** (`motion/shape_stream.py`, `joint/stream.py`, threaded
through `run_joint.py` and the web GUI's "New run" panel, 2026-09-14): a leg
solves in a couple of seconds flat-out, so the viewer had nothing to watch
draw — everything landed before the first poll tick. `X` paces the run to
X-times realtime instead. Purely a viewer aid; `0` (default) is flat-out and
nothing about the algorithms or the written files changes either way.

### Streaming vs batch (`joint/stream.py`, 2026-09-14)

Running the lanes back to back still meant *first* a whole shape, *then* all
the anchors, *then* one global fit — no intermediate state, and not how this
would run on a train. `joint/stream.py` joins them on one leg-time clock:

```
IMU samples  -> shape segments  \
                                 >-- prefix hydration --> hydrated points
cell samples -> anchors         /
```

IMU samples feed the `ShapeTracker`; cell anchors are released the instant
leg time passes their timestamp; every `--refit-every` leg-seconds the
hydration DP re-runs **on the prefix** and its knots are emitted as lat/lon.
Two properties make the prefix fit different from the batch one, both now
options on `hydrate.hydrate()`:

- **`pin_end=False`** — mid-leg the train has not arrived, so the "last knot
  sits at the path end" term has to go, or every fit is dragged forward.
- **`grid_cells_max`** — live refits run a coarse DP grid (350 cells) because
  they happen ~18x per leg; the final fit runs full resolution.

The still-open segment is included as a **provisional** entry
(`ShapeTracker.preview_segment()`). Without it the fit stops at the last
*closed* boundary, and a long straight means minutes with nothing new on the
map — measured on `ic830_00`: points stalled at 50 for four straight refits,
now they grow every tick.

Route choice streams too: the GTFS shortlist is built at t0 and re-ranked at
every refit by how well each candidate path hydrates the prefix. On
`ic4112_00` the guess sits on the wrong `s1 1985` until the first cell
anchors arrive at +210 s and then flips to the correct `ic4112` — the two
input streams visibly deciding the output together.

**Scoring is unaffected.** When the stream ends, the completed `shape.json` +
`anchors.json` are written and `solve.solve_warm()` builds the submission from
them exactly as the batch path does. Verified identical on `ic830_00`
(203.9 m median both ways). The streaming fits drive the live view and the
live route guess, never the CSVs.

Nothing in it is part of the submission pipeline, and the folder contract is
exactly the `shape.json`/`anchors.json` contracts below. Full folder/file
spec: `web/README.md`.

Note the two directories: `work/<leg_id>/` holds the **lane contracts** that
hydration consumes; `work/runs/<run_id>/<leg_id>/` is the viewer's copy. Both
lanes write both.

```bash
cd web && npm run install:all && npm run dev  # GUI :5173, API :5174 — then use "New run"
```

Or from a shell, same thing:

```bash
.venv/bin/python scripts/run_joint.py                       # batch: all 50 legs, scoring sweep
.venv/bin/python scripts/run_joint.py --stream --demo-speed 60 --legs ic830_00_kortrijk_ingelmunster
```

Takes `--legs A B C` (full leg directory names) and `--run-id`, which is how
the GUI names the run it starts. The GUI always passes `--stream`; the shell
default stays batch because the 50-leg sweep does not need ~18 prefix refits
per leg. `motion/shape_stream.py` and
`scripts/run_warm_gui.py` still exist and still run stand-alone from a shell
for lane-local debugging (e.g. iterating on the turn classifier without
paying for the GTFS/OSM solve every time) — but they no longer represent the
normal way to produce a leg's contract files, and the GUI doesn't offer them.

`shape_stream.py --no-gui` skips the run directory. Segment-close events go
into `events.ndjson` as each segment closes, so the timeline fills in while the
leg is still streaming — which is also the visible proof that the motion
algorithm is causal. Port 5174 is sometimes already taken by a server from
another worktree; `RAIL_GUI_PORT` moves it.

Motion `hydrated.json` is written only when `--warm-start LAT,LON,BEARING` is
given: dead reckoning has no absolute position or heading of its own, so
without a given start pose there is nothing to draw on a map. It is a debug
picture, and never feeds back into the algorithm.

## Constraints

- `meta.json` and `ground_truth.csv` are off-limits (labels/leakage) — must
  localize from raw sensor data + public reference data only. Warm track
  exception: `stationFrom`, `coordFrom`, `tFromEpochMillis` are what the
  organizers hand over, so the warm solver reads exactly those three.
- Known start point given per leg (warm track).
- Practice leg *folder names* embed the line and both stations
  (`ic830_00_kortrijk_ingelmunster`). Nothing reads them — if the scoring set
  keeps that format it is a label leak we must not build on. Ask Steven.

## Algorithm

Current plan, subject to revision as pieces get built/tested:

1. **Per-signal location estimate** — for each location-signal type
   (celltower, wifi), estimate location + radius. *(Corrected: an earlier
   version of this step listed `gps` — there is no GPS in `sensors.db`, that
   is the whole problem.)*
   - Celltower → tighter radius than wifi, but **absent on 24/50 legs**, so
     it cannot be the backbone. IMU + track geometry has to carry position;
     cell is an opportunistic correction where it exists.
   - Celltower join is ambiguous: our recordings carry only `cellId`, no
     LAC/TAC, while OpenCelliD's unique key needs `area` too. Expect several
     candidate towers per observation → multi-hypothesis prior, not a point.
     `networkType=NR` has no match in the reference CSV at all.
   - Wifi is bonus-only signal, not always present at a station — and
     sparser than hoped (see Setup).
2. **Motion classification** — per motion-sensor type (gyro, accel),
   classify turn as left/right/straight → build a route *shape* (sequence
   of turn/straight segments, no absolute scale/position yet).
   - Shape from gyro/accel alone has no length/scale per segment — a
     "straight" segment could be train moving straight OR train stopped.
     Hydration step (below) resolves which.
3. **Shape hydration** — stretch/pin the gyro/accel shape using per-signal
   location estimates (1) + their fault radius:
   - Segment classified "nothing" (straight/no turn) → gps/wifi/cell
     estimate ± radius decides: extend the line (train moved) vs. keep
     it flat (train stopped). Radius width gates confidence of the call.
   - Turn segments anchor the shape at higher confidence points; straight
     segments between anchors get stretched to fit estimated positions.
4. **Inter-station timing** — derive time-between-stations; must account
   for stop-before-stoplight case (stopped ≠ at station).
5. **Public reference data** used to support 1–4: OSM (rail network/
   stations), GTFS railway schedule, celltower reference CSV.
6. **Fusion** — hydrated best-effort shape → snap to OSM rail network →
   use time/epoch (clock-drift adjusted) to align against GTFS timetable →
   resolve which station/segment train is at.
   - Snapping caveat: the OSM network is ~203 disconnected components, so
     "snap to network" needs the components stitched at way endpoints first.
   - Name-matching caveat: GTFS `stops.stop_name` is French by default
     (`Anvers-Central`) while OSM and the leg ids are Dutch
     (`antwerpen_centraal`) — join via `stop_name_nl`.

Former open gap in this plan — device orientation — is **closed**: the up axis
alone (slow accelerometer EMA) carries turn detection, and the lateral/forward
axes fall out of regressing horizontal accel on yaw rate. See the motion lane
section. Step 3, hydration, is built — see the section below.

## Absolute lane — what is built and what was learned

Code in `absolute/`, entry point `scripts/run_warm.py`. Scores in `TASKS.md` §0.

- **Distance frame**: `scorer/polylines/<leg>.json` runs `stationFrom` (d=0) →
  `stationTo` (d≈`routeLengthM`, within 0.5 %). Ground truth can start well
  past 0 (`ic2315_00` at 2643 m: no GPS for the first 2.6 km). Some polylines
  contain an out-and-back spur at the start. We sidestep all of it by
  submitting lat/lon and letting the scorer project.
- **Leg timing structure** (44 good legs): train starts rolling **~85 s after
  scheduled departure** (median; range 20–400 s — the platform dwell is inside
  the leg), arrives ~24 s after scheduled arrival, recording ends ~30 s after
  arrival. t0 is within ±2 min of scheduled departure, symmetric. Encoded as
  `LEG_OVERHEAD_S = 110` in the ranking and `DWELL_AFTER_SCHED_S = 85` /
  `TAIL_AFTER_ARRIVAL_S = 30` in the solver — both are *priors* the motion
  lane's `moving` flags should replace.
- **GTFS route discovery** (`absolute/gtfs.py`): active `service_id`s from
  `calendar_dates`, trips departing the start station ±10 min, every downstream
  call as a destination candidate (`s51_785_00` really runs De Pinte→Zingem,
  skipping its scheduled Eke-Nazareth call). Cost = departure offset +
  1.5×duration mismatch + 120 s per skipped call. **41/50 top-1, 47/50 top-2.**
  Rejected: asymmetric "leaving early is rare" penalty — t0 is symmetric around
  the schedule. Unresolvable from timing: same hop, same minute (`ic2035` vs
  `s33 2963` Antwerpen-Centraal→Berchem) and same-duration opposite directions;
  cell anchors re-rank the shortlist for the latter.
- **routeGuess format**: `route_short_name + trip_short_name`, lowercase, space
  only for S-lines → `ic830`, `l1679`, `s51 761`. Practice `lineName` casing is
  inconsistent (`IC2315`, `S51 761`) but the scorer normalises.
- **Station registry** (`absolute/stations.py`): GTFS `stop_id`
  `gs:nmbssncb:8896008` carries the UIC code = OSM `uic_ref`. Exact join for
  672/835 stations; the French/Dutch name problem mostly disappears. Fallback:
  scorer normalisation + order-free token-prefix match (`aspere gavere` ↔
  `Gavere-Asper`).
- **Cell join** (`absolute/cells.py`): the LAC-less ambiguity feared earlier is
  a non-issue — every exact `(radio, cell)` hit is unique. Coverage is the
  issue: 35 % of distinct ids exact; **eNodeB fallback** (`cellId >> 8`,
  centroid of sibling cells, radius ≥1.5 km) → 56 %. Whole West-Flanders rides
  (`ic2315`, `s51_761` early legs) are 0 % either way. On `ic536_02` the
  anchors contain the true position 87 % of the time; centroid error median
  931 m — direction-grade, not position-grade.
- **OSM graph** (`absolute/track.py`): every vertex a node, stitch loose ends
  ≤8 m → 48 components (from 203), largest 249k nodes. Service/industrial
  track penalised, not removed. 23/50 legs within 2 % of true length. Known
  failure: **hairpin reversals** at junctions (same line, path 25–45 % too
  short). Fix = turn-angle penalty (edge-based dijkstra). Not done.
- **Our lengths run 1–3 % short** of `routeLengthM` even on good matches —
  the organizers' polyline is denser through curves. Irrelevant once we
  submit lat/lon.

## Motion lane — what is built and what was learned

### Measured (implemented in `motion/shape_stream.py`)

Orientation turned out cheaper than feared, and the speed prior far more
expensive. Measurements are from `motion/validate_shape.py` over the 44 `good`
practice legs.

- **Up axis is enough for turns.** Yaw rate = `-dot(gyro, up)` where `up` is a
  slow EMA of the accelerometer. It needs no heading and no longitudinal axis,
  so O1 does not gate M1 at all. Net heading error over a whole leg:
  **median 10 deg, p90 37 deg**. The turn sequence is trustworthy — it is the
  lane's real product.
- Gravity EMA must be **slow (tau 60 s)**. At tau 5 s it absorbs the train's
  own sustained acceleration into "gravity" and the longitudinal channel goes
  dead.
- **Accelerometer integration cannot give speed here.** Real train
  acceleration is ~0.15 m/s^2; track grade and (hand-held) device motion swamp
  it. Integrated speed on `ic830_00` drifted to **-15 m/s while the train was
  doing +32**. Dropped as a primary estimator.
- **Vibration energy does not give speed either.** Fitted accel high-pass RMS
  against ground-truth speed over 8 good legs at four cutoffs: best log-log
  correlation **r = 0.18**, exponent 0.3. Unusable. (Plausible cause: the
  recorder is hand-held, so handling noise dominates rail noise.)
- **What does work: curve geometry.** `a_lat = v * omega`, so
  `v = a_lat / omega` while turning. Cant (superelevation) hides part of
  `a_lat` — by design, at balance speed it hides nearly all of it — so the
  gyro-integrated roll angle about the forward axis is added back
  (`a_lat + beta * g * sin(cant)`). Grid-fitted `alpha=0.39, beta=1.0`:
  **median 39% relative speed error, and only while turning**.
- The lateral axis itself comes free from the same relation: regress
  horizontal accel on yaw rate (exponentially-forgetting, causal); the
  coefficient vector points along lateral and its length scales with mean
  speed. Forward = lateral x up.
- **Consequence for the contract**: `length_prior_m` is weak — dead-reckoned
  leg length lands at **median 39% error, p90 71%**, and legs with almost no
  curves have no speed evidence at all (`speed_source: "default"`, a flat
  22 m/s guess). Scale has to come from anchors + GTFS hop duration in
  hydration. The *topology* is the part to trust.

## Hydration — what is built and what was learned (step 3, joint)

Code: `absolute/hydrate.py`, called from `absolute/solve.py::solve_warm` and
`choose_hop`. Output: `work/<leg>/hydrated.json`. Dev A/B: `NO_HYDRATE=1`.

- **Formulation that worked first time**: exact dynamic programming, not a
  particle filter. Unknowns are the path distances at the shape's segment
  boundaries (monotone, start at 0, end near path length); the grid is ≤1600
  cells (25 m, coarser on long legs); one (J×J) cost matrix per segment. A
  50-segment, 25 km leg solves in ~1 s. Every cost is "metres of penalty" so
  the weights are exchange rates and easy to reason about.
- **Turns are the pins, exactly as hoped.** The path's smoothed, unwrapped
  bearing change over a segment must match the gyro `turn_deg` (deadband 3° +
  0.01°/s of segment duration for bias drift). On curvy legs this alone takes
  `ic2315_06` from 1875 m to 157 m median error.
- **Stationary flicker is the main hazard, both ways.** `ic536_02` flickers
  stop/move every 2–5 s while running at 25 m/s; `ic2035_00` flickers the same
  way while standing at the platform. A single "trust stops ≥15 s" rule fixes
  the first and breaks the second. What works: a short stop is believed when
  the ±20 s window around it is ≥50 % stopped (0.35 while running vs 0.6 at the
  platform). Stops are a penalty (3 m/m), never a hard constraint.
- **Cell anchors must be capped.** One wrong eNodeB centroid 4 km off, linear in
  distance, outweighed every turn (`ic4112_04` cost 140k, fit gave up on the
  end constraint). Capped at 1 km of overshoot per anchor: 995 → 300 m.
- **The schedule is still the strongest single prior — but only capped.**
  The shape is blind to *when* the leg runs, and the stationary detector
  sometimes misses the whole platform dwell (`ic3013_03`: a 235 s "moving"
  straight). Adding the schedule trapezoid as a weak unary prior at every knot
  (0.3 m/m) brought the median from 347 to 232 m — but uncapped it dragged
  well-pinned turns on the one leg where the schedule was wrong (`ic2315_06`
  157 → 2386 m). Capped at 1 km: 202 m median, 470 m mean over the good legs.
- **Weights were swept, not tuned to death** (`work/sweep_*.log`): W_TURN 25→50
  and W_SPEED 0.25→0.10 each helped; further changes (W_TURN 100, prior cap
  500–2000, W_PRIOR 0.6) move the median by <10 m either way. Stopped there —
  the scorer README's over-tuning warning applies.
- **Route discovery gets the turn sequence for free**: hydrating the shape
  onto each shortlisted candidate's path and adding `2 s × cost/segment` to the
  GTFS timing cost separates opposite directions (`ic2809_03`, `ic4112_00` now
  correct, 44 → 47/50). Per-*segment* normalisation matters: a flat per-metre
  rate let long flickery legs swamp timing and flipped `ic2809_04`. `l1679_02`
  stays wrong: it needs the shape weighted above the point where `ic2809_04`
  breaks. Candidates with no OSM path are now sent to the bottom (we could
  only stand still on them).
- **Rejected**: feeding the shape's first/last `moving` segment straight into
  the trapezoid window (`motion_window`) — 324 → 530 m median. Hydration
  consumes the flags with context; the raw first flicker does not mean the
  train rolled.
- **Not done**: `sigma_m` (H5) is written but nothing weights by it yet; the
  motion-lane dwell miss (`ic3013_03`) is best fixed at the source (M2), the
  schedule prior is a patch over it.

## Data contracts

Two engineers work in parallel, so the pipeline is cut at two files. These
are the *only* interfaces between the motion side (step 2) and the absolute
side (step 1); hydration (step 3) is the sole consumer of both. Freeze the
field names before writing algorithm code — both sides start against a
hand-written stub of the other's output, so neither ever blocks.

Shared conventions for both files:
- `t_*` is device `epochMillis` (int), raw and unadjusted. Clock-drift
  correction happens later, in fusion — nobody applies it here.
- Angles in degrees, distances in metres, speeds in m/s. WGS84 lat/lon.
- Files are per leg, written to `work/<leg_id>/`, JSON. Written to disk on
  purpose: they are the debuggable intermediates, and they let either side
  re-run without the other's code.

### `shape.json` — produced by the motion side (step 2)

Unscaled route topology. Ordered, contiguous, gap-free in time.

```jsonc
{
  "leg_id": "ic830_00",
  "t_start": 1757830000000,
  "t_end":   1757831800000,
  "orientation_ok": true,       // false → O1 failed, treat shape as suspect
  "segments": [
    {
      "seg_id": 0,
      "type": "straight",       // "left" | "right" | "straight"
      "t_start": 1757830000000,
      "t_end":   1757830042000,
      "turn_deg": 0.0,          // signed, + = right; 0 for straight
      "moving": false,          // from the stationary detector (M2)
      "speed_prior_mps": 0.0,   // mean over segment, from H2; null if unknown
      "length_prior_m": 0.0,    // speed_prior × duration; null if unknown
      "confidence": 0.9,        // 0..1, how sure the classifier is
      "speed_source": "curve"   // curve | regression | default | none
                                // -- provenance of speed_prior_mps; "default"
                                // means no IMU speed evidence at all
    }
  ]
}
```

Key point: `length_prior_m` is a **prior, not an answer**. A `straight`
segment with `moving: false` is the flat-vs-extend ambiguity of step 2 —
hydration resolves it, not this file.

### `anchors.json` — produced by the absolute side (step 1)

Sparse absolute position estimates with uncertainty. May legitimately be an
empty list: **24 of 50 legs have no cell at all**, and wifi alone is often
worthless. An empty `anchors` array is a valid input, not an error.

```jsonc
{
  "leg_id": "ic830_00",
  "anchors": [
    {
      "t": 1757830115000,
      "source": "cell",         // "cell" | "wifi" | "warm_start"
      "candidates": [           // multi-hypothesis — cellId join is ambiguous
        {"lat": 51.2194, "lon": 4.4025, "radius_m": 1200, "weight": 0.6},
        {"lat": 51.1050, "lon": 4.3900, "radius_m": 1200, "weight": 0.4}
      ]
    }
  ]
}
```

`candidates` is a list because our recordings carry only `cellId` with no
LAC/TAC, so one observation maps to several OpenCelliD towers (see step 1).
A single-hypothesis anchor is just a one-element list. `weight` sums to 1
per anchor. `source: "warm_start"` is the given start station/coords on the
warm track — one exact anchor, small radius, weight 1.

### Ownership

| Side | Owns | Produces / consumes |
|------|------|---------------------|
| Motion | leg loader, orientation recovery, turn segmentation, stationary detector, speed prior, stop detection | writes `shape.json` |
| Absolute | track model, OSM stitch + curvature signatures, cell join, wifi, GTFS candidate trips, name matching, `scorer/` harness, batch runner + submission writer | writes `anchors.json` |
| Joint | **hydration and fusion** (`absolute/hydrate.py`), and the streaming join (`joint/stream.py`) | reads both, emits hydrated points |

Hydration is deliberately not split. It is where the idea actually lives and
where a bad hand-off costs the most, so it gets paired on rather than divided.
The absolute side carries the scorer and harness because its signal work is
smaller and less uncertain — orientation recovery is the single hardest
unknown, so nothing else stacks on top of it.

Status: both lanes are end-to-end and now run **jointly, per leg**
(`scripts/run_joint.py`) — absolute (schedule-timed, no IMU) and motion
(validated by `motion/validate_shape.py`, dev-only, reads labels) each write
their contract file in the same pass, in place of the two separate runs this
used to require. **Hydration (step 3) — the join of `shape.json` and
`anchors.json` — is built** (`absolute/hydrate.py`, section above) and runs
inside `solve_warm`, so the joint runner now fuses both files per leg.
Practice warm: 202 m median-of-medians, 47/50 routes, 42/50 station calls.
`joint/stream.py` runs the same join as a live stream (both lanes on one
leg-time clock, prefix refits, hydrated points out as they go) for the viewer;
the submission still comes from `solve_warm` either way. Task breakdown +
owners in `TASKS.md`.
