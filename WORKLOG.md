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

Read-only by design: algorithms write files, the GUI only reads. Nothing in it
is part of the submission pipeline, and the folder contract is exactly the
`shape.json`/`anchors.json` contracts below. Full folder/file spec:
`web/README.md`.

Note the two directories: `work/<leg_id>/` holds the **lane contracts** that
hydration consumes; `work/runs/<run_id>/<leg_id>/` is the viewer's copy. Both
lanes write both.

```bash
python3 motion/shape_stream.py --all          # motion lane  → work/runs/<ts>-motion-lane/
python3 scripts/run_warm_gui.py               # absolute lane → work/runs/<ts>-warm/
cd web && npm run install:all && npm run dev  # GUI :5173, API :5174
```

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
section. The remaining open piece is step 3, hydration.

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
| Joint | **hydration and fusion** | reads both |

Hydration is deliberately not split. It is where the idea actually lives and
where a bad hand-off costs the most, so it gets paired on rather than divided.
The absolute side carries the scorer and harness because its signal work is
smaller and less uncertain — orientation recovery is the single hardest
unknown, so nothing else stacks on top of it.

Status: both lanes are end-to-end and independent — absolute
(`scripts/run_warm.py`, schedule-timed, no IMU) and motion
(`motion/shape_stream.py`, validated by `motion/validate_shape.py`, dev-only,
reads labels). **Hydration (step 3) — the join of `shape.json` and
`anchors.json` — is not started.** Task breakdown + owners in `TASKS.md`.
