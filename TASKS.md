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

Recon phase complete; **nothing implemented yet**. Schema documented
(`DATA_SCHEMA.md`), per-leg coverage survey done (§11), approach sketched in
`WORKLOG.md` → Algorithm. Next real work is Phase 1 — I1/I2/I3 block
everything downstream.

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

**Outputs per leg**: `position.csv` (`epochMillis, distanceAlongTrackM[,
routeGuess]`) and `station_calls.csv` (`epochMillis, stationNameGuess`).
Layout: `<team_name>/<cold|warm>/<leg_id>/position.csv`

**Two independent leaderboards**: *warm start* (start station + coords + time
given, route unknown) and *cold start* (nothing given). Not averaged.
Entering both is optional.

### Scoring dimensions

| # | Dimension | Track | Notes |
|---|-----------|-------|-------|
| 1 | Trip/route discovery | both | Open guess vs GTFS. **Final** value scores. |
| 2 | Speed of discovery | both | Scores the **earliest row after which the guess never changes**. Flip-flopping kills it. |
| 3 | Position accuracy | both | The core metric (median/mean/max error in m). |
| 4 | Time to first fix | cold | First estimate within 1 km. |
| 5 | First-fix accuracy | cold | Separate from #4. |
| 6 | Station-arrival detection | both | Right name, ±90 s of real arrival. Plus a one-shot 500 m-out prediction. |

### Rules / constraints

- OSM + GTFS + celltower CSV: use freely.
- `meta.json` and `ground_truth.csv` are **off-limits as algorithm inputs** —
  they're labels. Dev-time measurement/calibration only, and never one leg's
  truth as input to another scored leg.
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

| ID | Task | Status | Notes |
|----|------|--------|-------|
| S1 | Python env with pandas/numpy (+ shapely/scipy) | TODO | |
| S2 | Run `baseline_solve.py` on one practice leg end-to-end | TODO | |
| S3 | Locate/obtain the scorer (`src/scoring/scorer.py`) | BLOCKED | Referenced by both READMEs, **not in this repo**. Ask organizers or reimplement (→ I3). |
| S4 | Dump `sensors.db` schema + sample rates | DONE | → `DATA_SCHEMA.md`. Accel **and** gyro ~493 Hz, identical row counts per leg. |
| S5 | Inventory all 50 legs: duration, length, GT quality, cell/wifi coverage | DONE | → §11. |
| S6 | Inspect GTFS sqlite schema | DONE | → `DATA_SCHEMA.md`. Train number in `trips.trip_short_name`; `calendar_dates` authoritative. |
| S7 | Inspect OSM network + station geojson structure | DONE | → `DATA_SCHEMA.md`. Routable graph is I2. |

## 3. Phase 1 — Infrastructure (blocks everything else)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| I1 | Leg loader: `sensors.db` + `meta.json` → dataframes on a common time grid | TODO | Pick the resample rate: 493 Hz × 1800 s ≈ 900k rows/leg/sensor. |
| I2 | Track model: OSM → linestrings with cumulative distance; project lat/lon ↔ (edge, distance-along) | TODO | Network is **~203 disconnected components**; stitch at way endpoints. |
| I3 | Local scorer replicating the 6 metrics over all practice legs | TODO | Shape known from `starter_kit/example_scorer_output.json`. Exact tolerances blocked on S3. |
| I4 | Submission writer: exact `<team>/<track>/<leg_id>/` layout + columns | TODO | |
| I5 | Batch runner: all 50 legs → metrics table + per-leg diagnostics | TODO | |
| I6 | Name-matching layer: GTFS names are *French* (`Anvers-Central`), OSM + leg ids are *Dutch* (`antwerpen_centraal`) | TODO | Use `stops.stop_name_nl`; handle bilingual Brussels + 2 unnamed OSM stations. Needed by R2/R3, T3. |

## 4. Phase 2 — Motion shape from IMU (`WORKLOG.md` step 2)

Output: an ordered list of segments — `(type ∈ {left, right, straight},
start_t, end_t, turn_angle)` — with **no length or absolute position**. Works
on every leg; nothing here depends on cell/wifi.

| ID | Task | Status | Notes |
|----|------|--------|-------|
| O1 | Orientation recovery: gravity direction + longitudinal axis → rotate sensor axes to track axes | TODO | Device pose is unknown and arbitrary. Everything in this phase depends on it. |
| O2 | Handle orientation changes mid-leg (phone moved/picked up) | TODO | Detect and re-estimate rather than assume a fixed pose. |
| M1 | Turn segmentation: integrate yaw rate → left/right/straight events + cumulative angle | TODO | The signal that carries the shape. |
| M2 | Stationary detector: rolling accel variance → moving vs stopped | TODO | Feeds H2 and T1. Stopped ≠ at station. |
| M3 | Assemble the shape object + serialize it (debuggable intermediate) | TODO | Everything downstream consumes this, so give it a stable form early. |
| M4 | Sanity-check shapes against GT track geometry on `good` legs | TODO | Eyeball turn count/order vs real route before trusting hydration. |

## 5. Phase 3 — Shape hydration (`WORKLOG.md` step 3) — new core

Turns the unscaled shape into metric geometry: give every segment a length.
Turn segments act as high-confidence anchors; straight segments get stretched
(or held flat, if stopped) to fit absolute estimates.

| ID | Task | Status | Notes |
|----|------|--------|-------|
| H1 | Absolute-position estimates on the leg timeline: `(t, lat, lon, radius)` from cell and wifi | TODO | Consumes P1–P3. Sparse or wholly absent — hydration must degrade, not fail. |
| H2 | Segment length prior from IMU: speed estimate (accel integration w/ drift control and/or vibration energy → speed regression) | TODO | Replaces pure dead reckoning as the *only* scale source; it's now a prior the anchors correct. |
| H3 | Stopped-vs-moving call per straight segment | TODO | M2 proposes, H1 estimates ± radius confirm. Radius width gates confidence. |
| H4 | Hydration solver: assign lengths so the shape fits all estimates within their radii | TODO | Start with the simplest thing that works (least-squares / monotone fit) before reaching for a particle filter. |
| H5 | Per-segment confidence out of the solver | TODO | Feeds fusion weighting (N3) and the lock-in policy (R4). |
| H6 | Output: hydrated polyline + `distance-along-time` curve | TODO | This is the artifact Phase 4 snaps. |
| H7 | No-anchor fallback: leg with zero cell **and** useless wifi | TODO | 24/50 legs. H2 prior + GTFS timing is all there is. Don't discover this at 16h05 (→ X6). |

## 6. Phase 4 — Map matching & fusion (`WORKLOG.md` step 6) → metric #3

| ID | Task | Status | Notes |
|----|------|--------|-------|
| N1 | Stitch the OSM graph into routable components | TODO | Extends I2; ~203 components as delivered. |
| N2 | Precompute curvature signature per candidate OSM route | TODO | The thing M1's turn sequence gets matched against. |
| N3 | Match hydrated shape → OSM route + offset; emit `distanceAlongTrackM` | TODO | Weight by H5. Track-constrained, so this is a 1-D problem once the route is picked. |
| N4 | Clock-drift handling between device `epochMillis` and GTFS wall-clock | TODO | Size the actual drift before building for it. |
| N5 | Align to GTFS timetable (arrival times along the matched route) | TODO | Cross-checks N3 and feeds R3/T2. |
| N6 | Tunnel / long-gap behaviour — keep emitting sane estimates | TODO | GT has >60 s gaps; we still have to output rows. |

## 7. Phase 5 — Route discovery (metrics #1, #2)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| P1 | Cell tower → position prior: join `cell_samples.cellId` against `flanders_cells.csv` | TODO | See P2 — the join is **not clean**. |
| P2 | Resolve the cellId join ambiguity | TODO | We have only `cellId`, no LAC/TAC; OpenCelliD's key is `(radio,mcc,net,area,cell)`. Expect several candidate towers → multi-hypothesis prior. `networkType=NR` has no match at all. |
| P3 | WiFi AP fingerprinting — do BSSIDs recur across legs/stations? | TODO | Low expectations (as few as 1 distinct BSSID on a leg). Bonus signal. |
| R1 | Candidate trip generation: date → `calendar_dates` → active trips + stop patterns | TODO | Filter by coarse position + time of day. |
| R2 | Eliminate candidates as the leg progresses: turn sequence, stop pattern, inter-stop timing, direction | TODO | `WORKLOG.md` step 4. The shape (M3) is a strong discriminator here — use it, not just timing. |
| R3 | Inter-station timing model — must not treat a signal stop as a station | TODO | Pairs with T1. |
| R4 | Lock-in policy — commit early, then **never change** | TODO | Metric #2 scores the last change, not the first correct guess. |
| R5 | Fallback guess when confidence stays low | TODO | Open question: does blank `routeGuess` beat wrong? (§10) |
| R6 | Format the guess: `route_short_name` + `trip_short_name` → e.g. `IC830` | TODO | Confirm casing/spacing against the scorer. Leg ids use `ic830`. |

## 8. Phase 6 — Station detection (metric #6)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| T1 | Stop detector: M2 stops + **dwell duration** to reject signal stops | TODO | Baseline's known failure mode. |
| T2 | Cross-check candidate stops against our position (N3) vs the OSM station list | TODO | |
| T3 | Name the arrival from `belgium_rail_stations.geojson` (never `meta.json`) | TODO | 717 points = 458 `station` + 259 `halt`. Depends on I6. |
| T4 | Use GTFS dwell/stop patterns as a prior on which candidate is real | TODO | `pickup_type='1'` = pass-through, not a call. |
| T5 | 500 m-out arrival prediction — fire exactly once per arrival | TODO | Submission channel unclear (§10). |
| T6 | Validate timing error within ±90 s across practice legs | TODO | |

## 9. Phase 7 — Tracks, first fix, and the final round

### Warm start

| ID | Task | Status | Notes |
|----|------|--------|-------|
| W1 | Warm-start entry point: start station/coords/time as a hard anchor for H4 | TODO | A free, exact anchor — hydration gets much easier. `WORKLOG.md` → Constraints assumes this. |
| W2 | Confirm route discovery narrows sharply given origin + departure time | TODO | Should collapse R1 to a handful of trips. |
| W3 | **Decide: warm only, or both tracks** | TODO | Biggest scope lever left — it decides whether F1–F3 get built at all. |

### Cold start / first fix (metrics #4, #5) — gated on W3

| ID | Task | Status | Notes |
|----|------|--------|-------|
| F1 | Fastest coarse fix from the first cell/WiFi samples alone | TODO | **Unavailable on 24/50 legs** — needs a wifi-only or shape-only path. |
| F2 | Snap the coarse fix to the nearest plausible track segment (<1 km to count) | TODO | |
| F3 | Tune emit-now vs wait: fast+wrong and slow+right both lose | TODO | |

### Final round (16h00 → 17h00)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| X1 | Freeze the pipeline ~15h30; no algorithm changes after handover | TODO | |
| X2 | Smoke-test on a practice leg with `meta.json`/`ground_truth.csv` **removed** | TODO | Highest-value pre-flight check — proves nothing leaks. |
| X3 | Run on `datasets/scoring_release/` immediately at 16h00 | TODO | |
| X4 | Validate every output CSV (columns, row counts, no NaNs, monotonic time) | TODO | |
| X5 | Package `<team_name>/<cold\|warm>/<leg_id>/…`, send over Teams before 17h00 | TODO | |
| X6 | Verify graceful degradation on a **cell-less** leg | TODO | Nearly half the data. Exercises H7. |

---

## 10. Decisions & open questions

| Date | Decision | Rationale |
|------|----------|-----------|
| | Team name: **TBD** | Needed for the submission folder name |
| | Tracks entered: **TBD** | Resolve via W3. |
| | Language/stack: **TBD** (Python assumed) | |

Open:
- Where is `src/scoring/scorer.py`? Referenced by both READMEs, absent here
  (**S3**). Without it there's no local feedback loop.
- Exact scorer tolerances beyond ±90 s (station) and 1 km (first fix).
- Expected `routeGuess` string format — `IC830`? `830`? (**R6**)
- Does a blank `routeGuess` score better or worse than a wrong one? (**R5**)
- Is the 500 m-out prediction submitted via `station_calls.csv` or a separate
  channel? The brief mentions it; the format docs don't. (**T5**)
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
- Baseline's known failure: stop detector fired **275 s early** on a
  non-station slowdown → `detected: false` despite a correct name. Dwell
  duration + position cross-check is the fix (**T1/T2**).
- Ground truth has *gaps* (tunnels) for >60 s GPS outages; rows with
  `isInterpolated=true` are filled small (<60 s) gaps.
- `calendar_dates` — not `calendar` — is authoritative for whether a service
  ran on a given date.
- Task IDs changed with the hydration redesign: old `C1–C7` were resplit into
  `O*` (orientation), `M*` (shape), `H*` (hydration) and `N*` (fusion); old
  `R1/R1a/R2` became `P1/P2/P3`.
