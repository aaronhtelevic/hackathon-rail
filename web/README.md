# `web/` — live run viewer

A small Svelte GUI that watches a folder of algorithm output and renders it as
it appears. The point is to *see* a solve happen: which lane has produced what,
where the anchors landed, what the hydration solver did, what the scorer said.

Nothing in here is part of the submission pipeline. The GUI is **read-only
about run data** — it never writes into a run directory. It can *start* a lane
runner (see below), but from then on the algorithms write, the server reads and
the browser follows.

```
             ┌──POST /api/jobs──▶ node server ──spawn──┐
Svelte GUI ◀─┤                                         ▼
             └──SSE◀── node server ◀──reads── work/runs/<run_id>/… ◀──writes── algorithm (python)
```

---

## Quick start

```bash
cd web
npm run install:all      # installs frontend deps (vite + svelte)
npm run demo             # writes a synthetic run so there is something to look at
npm run dev              # API on :5174, GUI on :5173 (Vite proxies /api)
```

Then open <http://localhost:5173>.

For a single-port setup (no Vite), build once and let the Node server serve the
built files:

```bash
cd web
npm start                # build + serve everything on http://localhost:5174
```

### Environment

| Var | Default | Meaning |
|-----|---------|---------|
| `RAIL_RUNS_DIR` | `<repo>/work/runs` | Folder the server watches and algorithms write to |
| `RAIL_GUI_PORT` | `5174` | Node API/static port |
| `RAIL_GUI_WEB_PORT` | `5173` | Vite dev port |
| `RAIL_GUI_POLL_MS` | `500` | Change-detection interval |
| `RAIL_GUI_API` | `http://localhost:5174` | Proxy target for the Vite dev server |
| `RAIL_GUI_HOST` | `127.0.0.1` | Interface the Node server binds (loopback, because it can spawn runners) |
| `RAIL_GUI_LAUNCH` | `1` | `0` disables the run launcher — the server is then read-only |
| `RAIL_PRACTICE_DIR` | `<repo>/datasets/practice` | Where the launcher looks for legs |
| `RAIL_PYTHON` | `python3` | Interpreter for lanes that don't need `.venv` |

`work/` is gitignored — runs are scratch output, not artifacts.

---

## Starting a run from the GUI

The **New run** panel at the top of the sidebar starts the joint runner
(`scripts/run_joint.py`), so you don't need a second terminal:

1. tick the practice legs to run on (filter box + *all/none*),
2. pick a **demo speed** (flat-out, or 10x/30x/100x realtime),
3. optional notes (they show up in the run list),
4. **run joint**.

The GUI always launches the **streaming** mode (`run_joint.py --stream` →
`joint/stream.py`): shape segments and cell anchors advance together on one
leg-time clock, and the hydration DP re-fits the prefix as they arrive, so
`hydrated.json` grows point by point while the leg plays. That is what makes
the timeline and the map draw rather than blink into existence. Demo speed
paces the whole thing — a leg otherwise finishes in a couple of seconds.

Everything lands in one leg entry, so the viewer shows shape + anchors +
hydrated + score together. There is no standalone "motion run" or "absolute
run"; `motion/shape_stream.py` and `scripts/run_warm_gui.py` still work from
a shell for lane-local debugging, and `run_joint.py` without `--stream` is
the batch path used for the 50-leg scoring sweep.

The view jumps to the new run and fills in as the runner writes. One run at
a time; **stop** sends `SIGTERM` (then `SIGKILL` after 5 s), and the server
kills any child it still owns when it exits.

What the server will and won't do:

- The command line is **fixed in `server.mjs`**. The only client-supplied
  input is the leg list, and each name must match a directory under
  `datasets/practice` (with a `sensors.db`) before it becomes an argv item.
  Nothing goes through a shell.
- The joint run needs `pandas` (absolute side), so it only runs when
  `.venv/bin/python` exists at the repo root; otherwise the panel says so
  instead of failing halfway.
- Because the server can now run local processes, it binds **127.0.0.1** by
  default. `RAIL_GUI_HOST=0.0.0.0` opens it up — only do that on a trusted
  network, or set `RAIL_GUI_LAUNCH=0` to turn the launcher off entirely.

Everything about run *data* stays read-only: `POST` is accepted only on
`/api/jobs`, and the server never writes into a run directory.

---

## The on-disk contract

This is the whole interface. If an algorithm writes these files, the GUI shows
it; there is no registration step, no socket, no database.

```
work/runs/
└── 20260914-1503-joint/             # <run_id> — one directory per invocation
    ├── run.json                     # who ran, what, when
    ├── ic830_00/                    # <leg_id> — one directory per leg
    │   ├── status.json              # current state of this leg (overwritten)
    │   ├── events.ndjson            # append-only progress log
    │   ├── shape.json               # motion-lane contract (WORKLOG.md)
    │   ├── anchors.json             # absolute-lane contract (WORKLOG.md)
    │   ├── hydrated.json            # hydration output (polyline)
    │   ├── score.json               # scorer.score_leg() result
    │   ├── position.csv             # submission output (downloadable in GUI)
    │   └── station_calls.csv        #  "
    └── ic4112_02/
        └── …
```

**`run_id`** is free-form; `YYYYMMDD-HHMMSS-<algorithm>` sorts usefully and is
what the helper generates. Runs are listed newest-first by directory mtime.

**`leg_id`** must be the directory name. Any leg directory that appears shows up
in the sidebar immediately, even before it contains a single file.

### `run.json`

```jsonc
{
  "run_id": "20260914-1503-joint",
  "algorithm": "joint",              // free text, shown in the run list
  "lane": "joint",                   // "joint" is the only lane the GUI launches now
  "track": "warm",                   // "warm" | "cold" | null
  "state": "running",                // "running" | "done" | "error"
  "started_at": 1757830000000,       // epochMillis
  "finished_at": null,
  "notes": "H4 least-squares variant"
}
```

### `status.json` (per leg, overwritten in place)

```jsonc
{
  "leg_id": "ic830_00",
  "state": "running",                // "queued" | "running" | "done" | "error"
  "lane": "motion",
  "stage": "M1",                     // task id from TASKS.md — shown as-is
  "pct": 0.45,                       // 0..1, optional
  "message": "turn segmentation",
  "updated_at": 1757830115000
}
```

### `events.ndjson` (per leg, append-only)

One JSON object per line. The server tails it by byte offset and the GUI
appends only what is new, so this file can grow large without re-sending.

```jsonc
{"at": 1757830115000, "stage": "O1", "msg": "orientation recovered", "level": "info", "pct": 0.2}
{"at": 1757830119000, "stage": "H1", "msg": "no cell samples — H7 path", "level": "warn"}
```

`level` is `info` (default), `warn` or `error`. Any extra keys are preserved
and visible in the raw view. **Append and flush per line** — a partially
written trailing line is tolerated (it is held back until its newline lands),
but do not buffer for minutes or the GUI looks stalled.

### `shape.json` / `anchors.json`

Exactly the two contracts frozen in [`WORKLOG.md`](../WORKLOG.md) → *Data
contracts* (task **I7**). The GUI does not define them, it renders them — so
the stub producers from I7 are enough to light the whole screen up on day one.

- `shape.json` → the **Lane timeline**'s upper track. One block per segment,
  positioned by `t_start`/`t_end`, coloured by `type` (left / right /
  straight), opacity from `confidence`, hatched when `moving: false`.
- `anchors.json` → the lower track (a tick per anchor, coloured by `source`)
  and the **Geometry** scatter (one dot per candidate, circle radius =
  `radius_m`, opacity = `weight`).

An empty `anchors` array is rendered as *"no anchors — shape-only leg (H7
path)"*, not as an error. That is 24 of 50 practice legs and it should be
obvious at a glance which ones they are.

### `hydrated.json`

Where the two lanes have met. Free-form apart from `polyline`:

```jsonc
{
  "leg_id": "ic830_00",
  "polyline": [ {"t": 1757830000000, "lat": 51.2194, "lon": 4.4025, "distance_m": 0}, … ]
}
```

Drawn over the anchor scatter, so "did the hydrated track pass through the
anchor circles" is a one-look question.

### `score.json`

Whatever `scorer/scorer.py`'s `score_leg()` returned. The panel reads
`routeDiscovery`, `positionAccuracy`, `stationDetection` and `firstFix`, each
optional, and falls back to `—` for anything absent. Pass it straight through
from **I3** — no reshaping.

---

## Writing from an algorithm

Python helper: [`python/rail_gui.py`](python/rail_gui.py). Zero dependencies,
atomic writes, and every failure is swallowed — the GUI must never be able to
break a scoring run.

```python
import sys; sys.path.insert(0, "web/python")
from rail_gui import RunWriter

with RunWriter("motion-lane", lane="motion", track="warm") as run:
    for leg_id in leg_ids:
        with run.leg(leg_id) as leg:          # status -> running, then done/error
            leg.event("I1", "loaded leg", pct=0.05)
            shape = build_shape(leg_id)
            leg.shape(shape)                  # -> shape.json
            leg.event("M3", f"{len(shape['segments'])} segments", pct=0.5)
            leg.hydrated(polyline)            # -> hydrated.json
            leg.score(score_leg(...))         # -> score.json
            leg.write_text("position.csv", csv_text)
```

The context managers do the bookkeeping: entering a leg marks it `running`,
leaving it normally marks it `done`, and an exception marks it `error` with the
exception text as the last event.

Any other language just writes the files — that is the entire API. Two rules:

1. **Write JSON atomically** (temp file in the same directory, then `rename`).
   The server polls, and a half-written `shape.json` would flash a parse error.
   `events.ndjson` is the exception: plain append.
2. **Never write from the server or the GUI.** One writer per leg directory.

---

## How the server reads it

`server/server.mjs` — plain Node, no dependencies.

It keeps an index of `mtime:size` for every file under the runs dir, rescans
every `RAIL_GUI_POLL_MS`, and pushes the list of changed paths to every browser
over Server-Sent Events. Polling rather than `fs.watch` on purpose: recursive
watch semantics differ per platform and a 500 ms scan over a few hundred small
files costs nothing. The browser then refetches only what the change touched —
a `shape.json` write refetches the leg, an `events.ndjson` write pulls just the
bytes after its cursor.

| Endpoint | Returns |
|----------|---------|
| `GET /api/config` | resolved runs dir, poll interval |
| `GET /api/runs` | all runs, newest first |
| `GET /api/runs/:run` | run meta + every leg's status and file sizes |
| `GET /api/runs/:run/legs/:leg` | status, shape, anchors, hydrated, score |
| `GET /api/runs/:run/legs/:leg/events?cursor=N` | events after byte `N`, plus the next cursor |
| `GET /api/runs/:run/legs/:leg/file/:name` | raw file (CSV download links) |
| `GET /api/osm/:layer` | static reference GeoJSON — `rail-network` or `rail-stations`, read + gzipped once, cached in memory |
| `GET /api/events` | SSE stream: file changes, and the launcher's job list |
| `GET /api/legs` | practice legs available to run on (the picker's source) |
| `GET /api/jobs` | lane runners this server started, newest first, with a log tail |
| `POST /api/jobs` | start one: `{lane, legs[], allLegs, notes}` → `{job}` |
| `POST /api/jobs/:job/cancel` | `SIGTERM` the runner (`SIGKILL` after 5 s) |

`POST` is accepted on `/api/jobs` only; everything else refuses non-GET. Every
client path is resolved and confined to the runs dir, files over 8 MB are
refused rather than buffered, and request bodies over 256 kB are dropped.

---

## Layout

```
web/
├── README.md
├── package.json          # dev / build / demo scripts
├── server/server.mjs     # HTTP + SSE over the runs dir, plus the run launcher
├── frontend/             # Vite + Svelte 5
│   └── src/
│       ├── App.svelte            # run list, leg list, detail panes
│       └── lib/
│           ├── api.js            # fetch + SSE client
│           ├── RunLauncher.svelte   # leg picker, start/stop, job log
│           ├── LaneTimeline.svelte   # shape segments + anchors, shared time axis
│           ├── AnchorMap.svelte      # anchor candidates + hydrated polyline
│           ├── EventLog.svelte       # events.ndjson tail
│           ├── ScorePanel.svelte     # score.json metrics
│           └── JsonBox.svelte        # raw contract inspector
├── python/rail_gui.py    # writer helper for the algorithms
└── scripts/
    ├── dev.mjs           # runs server + vite together
    └── demo-run.mjs      # synthetic run, for developing the GUI alone
```

## Notes

- The map is a plain equirectangular SVG scatter — no tiles, no network. It
  runs on a hackathon laptop with the wifi off.
- Timestamps are raw device `epochMillis` everywhere, matching the contracts.
  Clock-drift correction belongs in fusion (**N4**), not here.
- Starting a run from the GUI turns "follow newest run" off and pins the view
  to the run you just started.
- "follow newest run" in the header keeps the view pinned to the most recently
  touched run; turn it off to stay on one while another is running.
