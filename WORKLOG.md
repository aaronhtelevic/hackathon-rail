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

## Constraints

- `meta.json` and `ground_truth.csv` are off-limits (labels/leakage) — must
  localize from raw sensor data + public reference data only.
- Known start point given per leg.

## Algorithm

Current plan, subject to revision as pieces get built/tested:

1. **Per-signal location estimate** — for each location-signal type
   (celltower, gps, wifi), estimate location + radius.
   - Celltower → tighter radius than gps/wifi.
   - Celltower not always present either — some legs recorded on a phone
     with no cellular chip (wifi-only device). Don't assume cell_samples
     exists; fall back to gps/wifi.
   - Wifi is bonus-only signal, not always present at a station.
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

Status: idea stage, not yet implemented.
