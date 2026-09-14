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

Motion lane (Phase 2 + H2) implemented: `motion/shape_stream.py` (causal,
stdlib-only, ~200x realtime) + `motion/validate_shape.py`. Absolute lane not
started. Schema documented
(`DATA_SCHEMA.md`), per-leg coverage survey done (§11), approach sketched in
`WORKLOG.md` → Algorithm. Next real work is Phase 1 — I7/I1/I2 block
everything downstream.

The organizers' scorer landed in `scorer/` — S3 unblocked, all tolerances now
exact, and metric #6 turned out to mean something different than assumed
(§1). `I3` shrank to a wrapper; `T1–T6` were rescoped.

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
| S1 | Python env with pandas/numpy (+ shapely/scipy) | J | TODO | |
| S2 | Run `baseline_solve.py` on one practice leg end-to-end | A | TODO | |
| S3 | Locate/obtain the scorer | A | DONE | It's `scorer/` — organizers' real logic, not a reimplementation. Tolerances now known exactly (§1); I3 drops to a thin wrapper. |
| S8 | Read `scorer/metrics.py` and confirm every task's assumed metric matches | A | DONE | Metric #6 turned out to be a 500 m-out prediction, not arrival detection → rescoped T1–T6. |
| S4 | Dump `sensors.db` schema + sample rates | — | DONE | → `DATA_SCHEMA.md`. Accel **and** gyro ~493 Hz, identical row counts per leg. |
| S5 | Inventory all 50 legs: duration, length, GT quality, cell/wifi coverage | — | DONE | → §11. |
| S6 | Inspect GTFS sqlite schema | — | DONE | → `DATA_SCHEMA.md`. Train number in `trips.trip_short_name`; `calendar_dates` authoritative. |
| S7 | Inspect OSM network + station geojson structure | — | DONE | → `DATA_SCHEMA.md`. Routable graph is I2. |

## 3. Phase 1 — Infrastructure (blocks everything else)

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| I7 | **Freeze the two data contracts + commit stub producers** | J | TODO | Do this first — 30 min. `shape.json` + `anchors.json` per `WORKLOG.md` → Data contracts. Stubs let both lanes start immediately. |
| I1 | Leg loader: `sensors.db` + `meta.json` → dataframes on a common time grid | M | TODO | Pick the resample rate: 493 Hz × 1800 s ≈ 900k rows/leg/sensor. |
| I2 | Track model: OSM → linestrings with cumulative distance; project lat/lon ↔ (edge, distance-along) | A | TODO | Network is **~203 disconnected components**; stitch at way endpoints. |
| I3 | Wrapper around `scorer/scorer.py` — import `score_leg()` directly, don't shell out per leg | A | TODO | Was "reimplement the scorer"; the real one is in `scorer/`. Just aggregation now. |
| I4 | Submission writer: exact `<team>/<track>/<leg_id>/` layout + columns | A | TODO | |
| I5 | Batch runner: all 50 legs → metrics table + per-leg diagnostics | A | TODO | |
| I6 | Name-matching layer: GTFS names are *French* (`Anvers-Central`), OSM + leg ids are *Dutch* (`antwerpen_centraal`) | A | TODO | Use `stops.stop_name_nl`; handle bilingual Brussels + 2 unnamed OSM stations. Needed by R2/R3, T3. |

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
| H1 | Write `anchors.json`: cell + wifi estimates as multi-hypothesis candidates with radius | A | TODO | Replaces the I7 stub. Consumes P1–P3. Empty list is a **valid** output (24/50 legs). |
| H2 | Segment length prior from IMU: speed estimate | M | DONE (weak) | Accel integration and vibration energy both **fail** (see `WORKLOG.md` → Motion lane, measured). What works: `v = a_lat/omega` in curves + gyro-derived cant correction → median 39% speed error, **turns only**. Dead-reckoned leg length: median 39% error, p90 71%. Scale must come from anchors/GTFS. |
| H3 | Stopped-vs-moving resolution per straight segment | J | TODO | M2's `moving` flag proposes, anchors ± radius confirm. Radius width gates confidence. |
| H4 | Hydration solver: assign lengths so the shape fits all anchors within their radii | J | TODO | **Pair on this.** Start with least-squares / monotone fit before reaching for a particle filter. |
| H5 | Per-segment confidence out of the solver | J | TODO | Feeds fusion weighting (N3) and the lock-in policy (R4). |
| H6 | Output: hydrated polyline + `distance-along-time` curve | J | TODO | The artifact Phase 4 snaps. |
| H7 | No-anchor fallback: leg with zero cell **and** useless wifi | J | TODO | 24/50 legs. H2 prior + GTFS timing is all there is. Don't discover this at 16h05 (→ X6). |

## 6. Phase 4 — Map matching & fusion (`WORKLOG.md` step 6) → metric #3

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| N1 | Stitch the OSM graph into routable components | A | TODO | Extends I2; ~203 components as delivered. |
| N2 | Precompute curvature signature per candidate OSM route | A | TODO | What M1's turn sequence gets matched against. Build it to consume `shape.json` directly. |
| N3 | Match hydrated shape → OSM route + offset; emit `distanceAlongTrackM` | J | TODO | Weight by H5. Track-constrained, so 1-D once the route is picked. |
| N4 | Clock-drift handling between device `epochMillis` and GTFS wall-clock | A | TODO | Contracts carry **raw** epochMillis — drift correction happens only here. Size the drift first. |
| N5 | Align to GTFS timetable (arrival times along the matched route) | A | TODO | Cross-checks N3 and feeds R3/T2. |
| N6 | Tunnel / long-gap behaviour — keep emitting sane estimates | J | TODO | GT has >60 s gaps; we still have to output rows. |

## 7. Phase 5 — Route discovery (metrics #1, #2)

`P1–P3` feed `anchors.json` and belong to the absolute lane's early parallel
block; `R*` is that lane's endgame work.

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| P1 | Cell tower → position prior: join `cell_samples.cellId` against `flanders_cells.csv` | A | TODO | See P2 — the join is **not clean**. |
| P2 | Resolve the cellId join ambiguity | A | TODO | We have only `cellId`, no LAC/TAC; OpenCelliD's key is `(radio,mcc,net,area,cell)`. Several candidate towers → the `candidates` list in `anchors.json`. `networkType=NR` has no match at all. |
| P3 | WiFi AP fingerprinting — do BSSIDs recur across legs/stations? | A | TODO | Low expectations (as few as 1 distinct BSSID on a leg). Bonus signal. |
| R1 | Candidate trip generation: date → `calendar_dates` → active trips + stop patterns | A | TODO | Filter by coarse position + time of day. |
| R2 | Eliminate candidates as the leg progresses: turn sequence, leg duration, direction, start/end spacing | A | TODO | `WORKLOG.md` step 4. A leg is **one hop**, so there is no in-leg stop pattern to observe — `shape.json`'s turn sequence and the hop's length/duration do the discriminating. |
| R3 | Match the leg's duration + endpoints against GTFS consecutive `stop_times` pairs | A | TODO | Replaces the multi-stop timing model. A ~252 s / 3.3 km hop narrows candidates hard. |
| R4 | Lock-in policy — commit early, then **never change** | A | TODO | Metric #2 only pays out **if the final guess is correct**; a wrong final guess scores zero on both #1 and #2. |
| R5 | Fallback guess when confidence stays low | A | TODO | Scorer treats null and wrong identically (`correct: false`, `lockInTimeS: null`), so a wrong guess costs nothing vs. blank — **always guess**. Resolved; keep the task for choosing *which* fallback. |
| R6 | Format the guess as `meta.lineName` — lowercase line id, e.g. `ic4112`, `l1679`, `s51_785` | A | TODO | Resolved from `scorer/scorer.py`: truth is `meta["lineName"]`, which equals the leg-id prefix. No trip number, no `IC` prefix; case/dashes are normalised away anyway. |

## 8. Phase 6 — The 500 m-out call (metric #6)

Rescoped after reading `scorer/metrics.py`. The metric is **one prediction per
leg**: name the destination, timed within ±90 s of the moment the train is
500 m from the leg's end. Only the first row of `station_calls.csv` counts.
Motion lane's endgame work.

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| T1 | Predict the 500 m-out moment: `remaining_distance ≤ 500 m` off our own position estimate | M | TODO | This is a *distance* problem, not a stop-detection one — it fires **before** arrival, while still moving. |
| T2 | Estimate leg length so "remaining" is computable without `routeLengthM` | M | TODO | `routeLengthM` is in `meta.json` = label. Must come from the matched route (N3) or GTFS stop spacing (N5). **The crux of this metric.** |
| T3 | Name the destination: identify `stationTo` from route + direction | M | TODO | Scorer's name match is substring either direction, so close is good enough. Needs I6 (A) for NL/FR. |
| T4 | Emit exactly one row, at the best single moment | M | TODO | Extra calls are ignored, not penalised — but only the *first* is scored, so an early wrong call wastes the leg. |
| T5 | Validate against the scorer across practice legs | M | TODO | `stationDetection.timingErrorS` per leg. |
| T6 | Decide whether stop/dwell detection is still worth building | M | TODO | Nothing in the 6 metrics scores it. It may still help R3 timing and H3 — but it is no longer a deliverable of its own. |

## 9. Phase 7 — Tracks, first fix, and the final round

### Warm start

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| W1 | Warm-start entry point: start station/coords/time → one `source: "warm_start"` anchor | A | TODO | A free, exact anchor — hydration gets much easier. Costs A almost nothing: same contract, one row. |
| W2 | Confirm route discovery narrows sharply given origin + departure time | A | TODO | Should collapse R1 to a handful of trips. |
| W3 | **Decide: warm only, or both tracks** | J | TODO | Biggest scope lever left — it decides whether F1–F3 get built at all. Decide before step 4 of the lane plan. |

### Cold start / first fix (metrics #4, #5) — gated on W3

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| F1 | Fastest coarse fix from the first cell/WiFi samples alone | A | TODO | **Unavailable on 24/50 legs** — needs a wifi-only or shape-only path. |
| F2 | Snap the coarse fix to the nearest plausible track segment (<1 km to count) | A | TODO | |
| F3 | Tune emit-now vs wait: fast+wrong and slow+right both lose | A | TODO | |

### Final round (16h00 → 17h00)

| ID | Task | Own | Status | Notes |
|----|------|-----|--------|-------|
| X1 | Freeze the pipeline ~15h30; no algorithm changes after handover | J | TODO | |
| X2 | Smoke-test on a practice leg with `meta.json`/`ground_truth.csv` **removed** | A | TODO | Highest-value pre-flight check — proves nothing leaks. |
| X3 | Run on `datasets/scoring_release/` immediately at 16h00 | A | TODO | |
| X4 | Validate every output CSV (columns, row counts, no NaNs, monotonic time) | A | TODO | |
| X5 | Package `<team_name>/<cold\|warm>/<leg_id>/…`, send over Teams before 17h00 | A | TODO | |
| X6 | Verify graceful degradation on a **cell-less** leg | M | TODO | Nearly half the data. Exercises H7 — shape-only path must still emit. |
| X7 | **Ask Steven: will scoring legs come with polylines?** | A | TODO | If yes, submit `latitude,longitude` and let the scorer project — N2/N3 route-picking becomes optional. Ask early; it changes the plan. |

---

## 10. Decisions & open questions

| Date | Decision | Rationale |
|------|----------|-----------|
| | Team name: **TBD** | Needed for the submission folder name |
| | Tracks entered: **TBD** | Resolve via W3. |
| | Language/stack: **TBD** (Python assumed) | |
| | Lane owners: motion = **TBD**, absolute = **TBD** | Two engineers, two lanes (§0). Put real names here so `Own` column is unambiguous. |
| 2026-09-14 | Cut the pipeline at `shape.json` + `anchors.json` | Only clean seam between the two lanes; lets each side work against a stub of the other. Contracts in `WORKLOG.md`. |

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
