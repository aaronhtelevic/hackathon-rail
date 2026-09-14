# Task Log — Train Localization Without GPS

Working task register for the team. Task IDs are stable — reference them in
later requests (e.g. "do P3", "C1 is done, what's next").

Companion docs — don't duplicate them here:
- [`WORKLOG.md`](WORKLOG.md) — living approach/state doc (what we believe and
  why, what's been tried and rejected). **Read before starting new work.**
- [`DATA_SCHEMA.md`](DATA_SCHEMA.md) — structural schema, no data.
- [`datasets/PARTICIPANT_BRIEF.md`](datasets/PARTICIPANT_BRIEF.md) — the ask.

Status key: `TODO` · `WIP` · `DONE` · `BLOCKED` · `DROPPED`

---

## 0. Where we are

Recon phase complete; **nothing implemented yet**. Schema is documented
(`DATA_SCHEMA.md`), the approach is sketched in five stages (`WORKLOG.md` →
Algorithm), and the per-leg sensor-coverage survey is done (§2, S5). Next
real work is Phase 1 infrastructure — I1/I2/I3 block everything downstream.

**The finding that most shapes the plan: 24 of 50 practice legs have zero
cell samples** (§12). Cell-based positioning can't be the backbone.

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
- Don't calibrate accuracy against `degraded`/`bad` legs (§12).

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
| S4 | Dump `sensors.db` schema + sample rates | DONE | → `DATA_SCHEMA.md`. Accel **and** gyro ~493 Hz, identical row counts per leg (synchronised sampling). |
| S5 | Inventory all 50 legs: duration, length, GT quality, cell/wifi coverage | DONE | → §12. |
| S6 | Inspect GTFS sqlite schema | DONE | → `DATA_SCHEMA.md`. Train number lives in `trips.trip_short_name`; `calendar_dates` is authoritative for "did it run". |
| S7 | Inspect OSM network + station geojson structure | DONE | → `DATA_SCHEMA.md`. Building the routable graph is I2. |

## 3. Phase 1 — Infrastructure (blocks everything else)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| I1 | Leg loader: `sensors.db` + `meta.json` → dataframes on a common time grid | TODO | Decide the resample rate — 493 Hz × 1800 s ≈ 900k rows/leg/sensor. |
| I2 | Track model: OSM → linestrings with cumulative distance; project lat/lon ↔ (edge, distance-along) | TODO | Network is **~203 disconnected components**; needs stitching at way endpoints. |
| I3 | Local scorer replicating the 6 metrics, over all practice legs | TODO | Shape is known from `starter_kit/example_scorer_output.json`. Blocked-ish on S3 for exact tolerances. |
| I4 | Submission writer: exact `<team>/<track>/<leg_id>/` layout + columns | TODO | |
| I5 | Batch runner: all 50 legs → metrics table + per-leg diagnostics | TODO | |
| I6 | **Name-matching layer**: GTFS stop names default to *French* (`Anvers-Central`); OSM + leg ids are *Dutch* (`antwerpen_centraal`) | TODO | Use `stops.stop_name_nl`; handle bilingual Brussels and the 2 unnamed OSM stations. Needed by R3/R4 and T4. |

## 4. Phase 2 — Core: speed & distance-along-track (metric #3)

Corresponds to `WORKLOG.md` Algorithm steps 2–3 and 5.

| ID | Task | Status | Notes |
|----|------|--------|-------|
| C1 | Speed estimation from IMU — accel integration w/ drift control and/or vibration-energy → speed regression | TODO | 493 Hz gives real vibration spectrum to work with. |
| C2 | Calibrate the speed model against `good`-quality legs only | TODO | 44 legs usable (§12). |
| C3 | Integrate speed → distance-along-track; zero-velocity updates at stops | TODO | |
| C4 | Curvature signature from gyro yaw rate, matched to track geometry | TODO | WORKLOG step 2 ("classify turn left/right/straight") — the absolute-correction lever, and it works on **every** leg unlike cell. |
| C5 | Fuse: track-constrained particle/Kalman filter; cell/WiFi as weak absolute observations | TODO | WORKLOG step 5. |
| C6 | Tunnel / GPS-denied behaviour — keep emitting sane estimates where truth is absent | TODO | |
| C7 | Device-orientation handling: sensor axes are not track axes and the device pose is unknown/arbitrary | TODO | Gap in the current plan — C1/C4 both depend on it. PCA on the gravity + longitudinal directions. |

## 5. Phase 3 — Route discovery (metrics #1, #2)

Corresponds to `WORKLOG.md` Algorithm steps 1, 3, 4.

| ID | Task | Status | Notes |
|----|------|--------|-------|
| R1 | Cell tower → position prior: join `cell_samples.cellId` against `flanders_cells.csv` | TODO | See R1a — the join is **not clean**. |
| R1a | Resolve the cellId join ambiguity: our recordings have **no LAC/TAC**, only `cellId`, but the true unique key is `(radio,mcc,net,area,cell)` | TODO | Expect multiple candidate towers per observation → treat as a multi-hypothesis prior, not a point. Also: `networkType=NR` has **no match at all** in the reference CSV (GSM/UMTS/LTE only). |
| R2 | WiFi AP fingerprinting — do BSSIDs recur across legs/stations? | TODO | Low expectations: as few as 13 rows / 1 distinct BSSID on some legs (§12). Bonus signal only. |
| R3 | Candidate trip generation: date → `calendar_dates` active services → trips + stop patterns | TODO | Filter by coarse position + time of day. |
| R4 | Eliminate candidates as the leg progresses (stop pattern, inter-stop timing, direction, travel time) | TODO | WORKLOG step 3: inter-station timing must not confuse a signal stop with a station. |
| R5 | Lock-in policy — commit early, then **never change** | TODO | Metric #2 scores the last change, not the first correct guess. |
| R6 | Fallback guess when confidence stays low | TODO | Open question: does a blank `routeGuess` beat a wrong one? |
| R7 | Format the guess correctly: `route_short_name` + `trip_short_name` → e.g. `IC830` | TODO | Confirm expected casing/spacing against the scorer. Leg ids use `ic830`. |
| R8 | Clock-drift handling between device `epochMillis` and GTFS wall-clock | TODO | WORKLOG step 5 calls for it; size the actual drift before building for it. |

## 6. Phase 4 — First fix (cold track, metrics #4, #5)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| F1 | Fastest coarse fix from first cell/WiFi samples alone | TODO | **Unavailable on 24/50 legs** (no cell) — needs a WiFi-only or dead-reckoning path. |
| F2 | Snap the coarse fix to the nearest plausible track segment (<1 km to count) | TODO | |
| F3 | Tune emit-now vs wait: fast+wrong and slow+right both lose | TODO | |

## 7. Phase 5 — Station detection (metric #6)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| T1 | Stop detector: rolling accel-std + **dwell duration** to reject signal stops | TODO | WORKLOG step 3. |
| T2 | Cross-check candidate stops against our own position vs the OSM station list | TODO | |
| T3 | Use GTFS dwell/stop patterns as a prior on which candidate is real | TODO | Note `pickup_type='1'` = pass-through, not a call. |
| T4 | Name the arrival from `belgium_rail_stations.geojson` (never `meta.json`) | TODO | 717 points = 458 `station` + 259 `halt`; depends on I6. |
| T5 | 500 m-out arrival prediction — fire exactly once per arrival | TODO | Submission channel unclear (§11). |
| T6 | Validate timing error within ±90 s across practice legs | TODO | |

## 8. Phase 6 — Warm-start variant

| ID | Task | Status | Notes |
|----|------|--------|-------|
| W1 | Warm-start entry point: given start station/coords/time as a hard prior | TODO | `WORKLOG.md` → Constraints currently assumes this ("known start point given per leg"). |
| W2 | Confirm route discovery narrows sharply given origin + departure time | TODO | Should collapse R3 to a handful of trips. |
| W3 | Decide: one track or both, and split effort | TODO | See §10 — currently unresolved, and it changes whether F1–F3 get built at all. |

## 9. Phase 7 — Final round (16h00 → 17h00)

| ID | Task | Status | Notes |
|----|------|--------|-------|
| X1 | Freeze the pipeline ~15h30; no algorithm changes after handover | TODO | |
| X2 | Smoke-test on a practice leg with `meta.json`/`ground_truth.csv` **removed**, to prove nothing leaks | TODO | The single highest-value pre-flight check. |
| X3 | Run on `datasets/scoring_release/` immediately at 16h00 | TODO | |
| X4 | Validate every output CSV (columns, row counts, no NaNs, monotonic time) | TODO | |
| X5 | Package `<team_name>/<cold\|warm>/<leg_id>/…`, send over Teams before 17h00 | TODO | |
| X6 | Verify the pipeline degrades gracefully on a **cell-less** leg | TODO | Nearly half the data looks like this; do not discover it at 16h05. |

---

## 10. Decisions

| Date | Decision | Rationale |
|------|----------|-----------|
| | Team name: **TBD** | Needed for the submission folder name |
| | Tracks entered: **TBD** | `WORKLOG.md` assumes a known start point (= warm), but this hasn't been decided explicitly. Resolve via W3 — it's the biggest scope lever left. |
| | Language/stack: **TBD** (Python assumed) | |

## 11. Open questions

- Where is `src/scoring/scorer.py`? Referenced by both READMEs, absent here
  (**S3**). Without it there's no local feedback loop.
- Exact scorer tolerances beyond the ±90 s station window and the 1 km
  first-fix threshold ("see the scorer's docs … once published").
- Expected `routeGuess` string format — `IC830`? `830`? (**R7**)
- Does a blank `routeGuess` score better or worse than a wrong one? (**R6**)
- Is the 500 m-out arrival prediction submitted via `station_calls.csv` or a
  separate channel? The brief mentions it; the format docs don't. (**T5**)
- Will scoring legs also have cell gaps, and in the same ride-wide pattern?
- Are scoring legs contiguous within a ride? Practice legs are **not** —
  indices skip (`ic830` has 00,01,02,04,05,06; no 03).

## 12. Findings — per-leg survey (S5)

Measured across all 50 practice legs.

**Cell coverage is the big one.** `datasets/README.md` describes empty
`cell_samples` as "a handful of legs". It's **24 of 50**, and it's
ride-correlated, not random — every leg of `ic2809`, `ic2933`, `ic3013`,
`ic830`, `l1679`, `s51_785` has zero cell rows; every leg of `ic2035`,
`ic2315`, `ic3033`, `ic4112`, `ic536`, `s51_761` has some. Consequences:
- Cell can't be the primary positioning signal — IMU + track geometry (C4)
  must carry it, with cell as an opportunistic correction.
- Cold-start first fix (F1) has no cell to bootstrap from on those legs.
- Anything tuned on cell-rich legs will look far better in dev than it scores.

**WiFi is thin.** 13–1,074 rows per leg; 1–174 distinct BSSIDs. The sparsest
legs (`s51_761_04`: 25 rows / 1 BSSID) give essentially nothing. Treat as
bonus signal, consistent with `WORKLOG.md`.

**IMU is dense and uniform.** Accel and gyro both ~483–496 Hz on every leg,
with identical row counts per leg. ~900k rows per sensor on a 30-min leg —
decide the working resample rate early (I1).

**Ground-truth quality**: 44 `good`, 2 `degraded` (`ic2035_00/01`), 4 `bad`
(all of `ic536`). Awkward overlap: the `ic536` legs have the **richest cell
data** (1,593 and 1,887 samples) but untrustworthy ground truth — good for
developing the cell join (R1), useless for measuring accuracy.

**Leg sizes**: 214 s – 1,834 s duration; 1.8 km – 51.8 km route length. A
30× spread, so anything with a fixed window length needs checking at both
ends.

## 13. Notes

- Baseline position error (constant-speed interpolation): median **974 m**.
  That's the bar to beat.
- Baseline's known failure: stop detector fired **275 s early** on a
  non-station slowdown → `detected: false` despite a correct name. Dwell
  duration + position cross-check is the fix (**T1–T3**).
- Ground truth has *gaps* (tunnels) for >60 s GPS outages; rows with
  `isInterpolated=true` are filled small (<60 s) gaps.
- `calendar_dates` — not `calendar` — is authoritative for whether a service
  ran on a given date.
