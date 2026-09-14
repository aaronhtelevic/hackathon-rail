# Data Schema Reference

Structure only, no data. For problem context see `PARTICIPANT_BRIEF.md` / `README.md`.

## practice/<leg_id>/sensors.db (SQLite)

Same schema every leg. No `gps_samples`, no `station_events` (must be inferred).

```sql
accel_samples (
  id INTEGER,
  sessionId INTEGER,
  sensorTimestampNanos INTEGER,
  epochMillis INTEGER,
  x REAL, y REAL, z REAL
)

gyro_samples (
  id INTEGER,
  sessionId INTEGER,
  sensorTimestampNanos INTEGER,
  epochMillis INTEGER,
  x REAL, y REAL, z REAL
)

cell_samples (
  id INTEGER,
  sessionId INTEGER,
  epochMillis INTEGER,
  cellId TEXT,
  networkType TEXT,   -- GSM / WCDMA / LTE / NR
  rssiDbm INTEGER,
  isRegistered INTEGER
)  -- may be empty for some legs (bad SIM/recorder on that ride)

wifi_samples (
  id INTEGER,
  sessionId INTEGER,
  epochMillis INTEGER,
  bssid TEXT,
  ssid TEXT,
  rssiDbm INTEGER
)
```

## practice/<leg_id>/meta.json

```
legId, knownRoute, lineName, direction, rollingStockId,
stationFrom, stationTo,
coordFrom [lon,lat], coordTo [lon,lat],
tFromEpochMillis, tToEpochMillis, durationS,
routeLengthM,
hasGroundTruth,
groundTruthQuality: good | degraded | bad,
groundTruthQualityDetail { quality, medianAccuracyM, pctGood30m, pctBad100m }
```
`bad`/`degraded` quality legs never end up in the scoring set — dev/sensor use only, don't trust point accuracy.

## practice/<leg_id>/ground_truth.csv

```
epochMillis, distanceAlongTrackM, longitude, latitude, lateralOffsetM, isInterpolated
```
Distance-along-track is primary (track-constrained problem). Rows absent during long GPS-denied stretches (tunnels) — on purpose. Never use as input for a *different* leg you're scoring.

## Submission format (your output, not input data)

`position.csv`
```
epochMillis, distanceAlongTrackM[, routeGuess]
```
(`latitude, longitude` accepted instead of `distanceAlongTrackM`.) `routeGuess` sparse/optional — any real Belgian train number, blank until committed.

`station_calls.csv`
```
epochMillis, stationNameGuess
```

---

## Reference data (public, global)

### reference_data/gtfs_schedule/nmbs_schedule.sqlite

NMBS/SNCB theoretical (scheduled) timetable. Rail only (`route_type=2`). Window 2026-07-03..2026-12-12.

```sql
routes (1,277 rows)
  route_id TEXT,          -- e.g. "gr:nmbssncb:1262"
  route_short_name TEXT,  -- "IC","L","S51","P","EC",...
  route_long_name TEXT,   -- French station-pair name
  route_type TEXT         -- always "2"

trips (60,050 rows)
  trip_pk INTEGER,        -- PK, surrogate
  trip_id TEXT,           -- original GTFS id
  route_id TEXT,          -- FK routes.route_id
  service_id TEXT,        -- FK calendar/calendar_dates.service_id
  trip_short_name TEXT,   -- *** REAL TRAIN NUMBER *** e.g. "830","1679"
  route_short_name TEXT,  -- denormalized
  route_long_name TEXT,   -- denormalized
  trip_headsign TEXT

stops (2,195 rows)
  stop_pk INTEGER,        -- PK, surrogate
  stop_id TEXT,
  stop_name TEXT,         -- French (feed default), e.g. "Anvers-Central"
  stop_name_nl TEXT,      -- Dutch, e.g. "Antwerpen-Centraal"
  stop_name_en TEXT,
  stop_lat TEXT, stop_lon TEXT,  -- WGS84
  platform_code TEXT      -- ~850/2195 rows

stop_times (754,730 rows)
  trip_pk INTEGER,        -- FK trips.trip_pk
  stop_sequence INTEGER,  -- order, starts at 1
  stop_pk INTEGER,        -- FK stops.stop_pk
  arrival_time TEXT, departure_time TEXT,  -- "HH:MM:SS", can exceed 24:00:00
  pickup_type TEXT, drop_off_type TEXT     -- "0"=regular, "1"=pass-through

calendar (rail-relevant service_ids only)
  service_id TEXT, start_date TEXT, end_date TEXT,
  monday..sunday TEXT     -- mostly unused, calendar_dates is authoritative

calendar_dates (223,154 rows)  -- THE definitive "did service X run on date Y"
  service_id TEXT, date TEXT (YYYYMMDD), exception_type TEXT  -- "1"=added,"2"=removed

feed_info (1 row)
  feed_id, default_lang, feed_contact_email, feed_contact_url,
  feed_end_date, feed_lang, feed_publisher_name, feed_publisher_url,
  feed_start_date, feed_version

-- indexes: trips(trip_pk) unique, stops(stop_pk) unique,
--          stop_times(trip_pk), stop_times(stop_pk),
--          trips(trip_short_name), trips(route_id),
--          calendar_dates(service_id), calendar_dates(date), stops(stop_name)
```

How to use ("which train am I on"):
1. `SELECT DISTINCT service_id FROM calendar_dates WHERE date='YYYYMMDD' AND exception_type='1'` (minus type '2' rows).
2. Join trips+stop_times+stops for those service_ids → candidate stop patterns.
3. Match your detected station sequence + rough inter-stop timing against candidates; best match's `trip_short_name`+`route_short_name` (e.g. "IC"+"830"→IC830) is your guess.

### reference_data/celltower/flanders_cells.csv

Cell tower ID → location lookup (OpenCelliD, CC-BY-SA-4.0), 39,006 rows, Flanders + Brussels.

```
radio,mcc,net,area,cell,unit,lon,lat,range,samples,changeable,created,updated,averageSignal
```
- `radio`: GSM/UMTS/LTE (no NR/5G).
- `mcc`: 206 (Belgium, all rows).
- `net` (MNC): 1=Proximus, 10=Telenet/BASE, 20=Orange, 4=MVNO.
- `area`: LAC/TAC — **not present in our own `cell_samples` recordings**, only `cellId`.
- `cell`: CID/ECI — join key against `cell_samples.cellId`.
- `lon`/`lat`: crowdsourced centroid, not surveyed.
- `range`: coverage-radius / position uncertainty (m).
- `samples`: observation count (confidence proxy).

Join: map `cell_samples.networkType` → `radio` (LTE→LTE, WCDMA→UMTS, GSM→GSM; NR has no match), then look up `(radio, mcc, net, cell)` (prefer `+area` if you ever have LAC/TAC — true unique key).

### reference_data/osm/ — Belgian rail network (OpenStreetMap extract, EPSG:4326)

`belgium_rail_network.geojson` / `.shp` (+`.dbf`/`.shx`/`.prj`/`.cpg`) — 25,024 LineString features, one per OSM way (`railway=rail` only; excludes metro/tram/light_rail/disused).

Tags per line feature (shapefile field names truncated ≤10 chars, see README's `SHP_FIELD_MAP`):
```
railway, usage, service, electrified, voltage, frequency,
maxspeed, gauge, ref, name, operator, tunnel, bridge, layer, osm_id
```

`belgium_rail_stations.geojson` / `.shp` (+ sidecars) — 717 Point features (458 `station` + 259 `halt`).

Tags per station feature:
```
railway, name, name:nl, name:fr, name:de, uic_ref, uic_name, operator, network, osm_id
```

Notes: network is ~203 disconnected components (heritage lines, spurs — doesn't affect the 6 known routes); tunnels tagged `tunnel=yes`; 2 station nodes untagged for name; border-crossing lines cut at BE boundary; bilingual Brussels station names live under combined `name` / split via `name:nl`.
