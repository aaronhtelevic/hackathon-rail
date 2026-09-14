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

## Constraints

- `meta.json` and `ground_truth.csv` are off-limits (labels/leakage) — must
  localize from raw sensor data + public reference data only.
- Known start point given per leg.

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
   classify turn as left/right/straight.
3. **Inter-station timing** — derive time-between-stations; must account
   for stop-before-stoplight case (stopped ≠ at station).
4. **Public reference data** used to support 1–3: OSM (rail network/
   stations), GTFS railway schedule, celltower reference CSV.
5. **Fusion** — estimate raw locations → snap to OSM rail network → use
   time/epoch (clock-drift adjusted) to align against GTFS timetable →
   resolve which station/segment train is at.
   - Snapping caveat: the OSM network is ~203 disconnected components, so
     "snap to network" needs the components stitched at way endpoints first.
   - Name-matching caveat: GTFS `stops.stop_name` is French by default
     (`Anvers-Central`) while OSM and the leg ids are Dutch
     (`antwerpen_centraal`) — join via `stop_name_nl`.

Open gap in this plan: device orientation. Sensor axes are not track axes and
the device pose is unknown, so steps 2 and 3 both need an orientation-recovery
stage before they can use raw accel/gyro axes.

Status: idea stage, not yet implemented. Task breakdown in `TASKS.md`.
