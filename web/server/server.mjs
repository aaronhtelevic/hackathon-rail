// Rail hackathon run viewer — zero-dependency Node server.
//
// Serves the Svelte GUI and exposes the run directory that the algorithms
// write into. See web/README.md for the on-disk contract.

import http from 'node:http'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import zlib from 'node:zlib'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(__dirname, '..', '..')

const PORT = Number(process.env.RAIL_GUI_PORT ?? 5174)
// The launcher below runs local processes, so stay on loopback unless told otherwise.
const HOST = process.env.RAIL_GUI_HOST ?? '127.0.0.1'
const RUNS_DIR = path.resolve(process.env.RAIL_RUNS_DIR ?? path.join(REPO_ROOT, 'work', 'runs'))
const STATIC_DIR = path.join(__dirname, '..', 'frontend', 'dist')
const PRACTICE_DIR = path.resolve(process.env.RAIL_PRACTICE_DIR ?? path.join(REPO_ROOT, 'datasets', 'practice'))
const LAUNCH_ENABLED = process.env.RAIL_GUI_LAUNCH !== '0'
const OSM_DIR = path.resolve(process.env.RAIL_OSM_DIR ?? path.join(REPO_ROOT, 'datasets', 'reference_data', 'osm'))
const POLL_MS = Number(process.env.RAIL_GUI_POLL_MS ?? 500)
const MAX_FILE_BYTES = 8 * 1024 * 1024

// OSM layers are big, static, read-only reference data — read once, gzip once, keep in memory.
const OSM_FILES = {
  'rail-network': 'belgium_rail_network.geojson',
  'rail-stations': 'belgium_rail_stations.geojson',
}
const osmCache = new Map() // layer -> { raw: Buffer, gz: Buffer }

async function loadOsmLayer (layer) {
  if (osmCache.has(layer)) return osmCache.get(layer)
  const name = OSM_FILES[layer]
  if (!name) return null
  const raw = await fsp.readFile(path.join(OSM_DIR, name))
  const gz = zlib.gzipSync(raw)
  const entry = { raw, gz }
  osmCache.set(layer, entry)
  return entry
}

fs.mkdirSync(RUNS_DIR, { recursive: true })

// ------------------------------------------------------------------ launcher
//
// The viewer stays read-only about run *data* — it never writes into a run
// directory. It can, however, start the lane runners, which is otherwise the
// one thing you need a second terminal for. Both commands are fixed here; the
// only client-controlled input is the leg list, and every name must match a
// real directory under datasets/practice before it becomes an argv item. No
// shell is involved at any point.

const LANES = {
  joint: {
    label: 'joint run',
    script: 'scripts/run_joint.py',
    needsPandas: true, // absolute-side deps; motion side is stdlib-only either way
    // run_joint.py defaults to every practice leg when --legs is omitted
    legArgs: (legs, all) => (all ? [] : ['--legs', ...legs]),
  },
}

const VENV_PYTHON = path.join(REPO_ROOT, '.venv', 'bin', 'python')
const FALLBACK_PYTHON = process.env.RAIL_PYTHON ?? 'python3'

/** Interpreter for a lane, or null when the lane's dependencies are missing. */
function pythonFor (lane) {
  if (fs.existsSync(VENV_PYTHON)) return VENV_PYTHON
  // Refuse rather than fail obscurely three seconds later on `import pandas`.
  return LANES[lane].needsPandas ? null : FALLBACK_PYTHON
}

/** Practice legs on disk — the dataset picker's source of truth. */
async function listLegs () {
  let entries
  try { entries = await fsp.readdir(PRACTICE_DIR, { withFileTypes: true }) } catch { return [] }
  const legs = []
  for (const e of entries) {
    if (!e.isDirectory()) continue
    let sizeBytes = 0
    try { sizeBytes = (await fsp.stat(path.join(PRACTICE_DIR, e.name, 'sensors.db'))).size } catch { continue }
    // ic830_00_kortrijk_ingelmunster -> ride ic830, leg 00, hop "kortrijk ingelmunster"
    const m = /^([a-z0-9]+)_(\d+)_(.*)$/.exec(e.name)
    legs.push({
      leg_id: e.name,
      ride: m ? m[1] : e.name,
      index: m ? Number(m[2]) : null,
      hop: m ? m[3].replace(/_/g, ' ') : '',
      sizeBytes,
    })
  }
  legs.sort((a, b) => a.leg_id.localeCompare(b.leg_id))
  return legs
}

const jobs = new Map() // job_id -> record
const MAX_LOG_LINES = 300

const publicJob = j => ({
  job_id: j.job_id,
  lane: j.lane,
  run_id: j.run_id,
  legs: j.legs,
  allLegs: j.allLegs,
  notes: j.notes,
  cmd: j.cmd,
  state: j.state,
  startedAt: j.startedAt,
  finishedAt: j.finishedAt,
  exitCode: j.exitCode,
  error: j.error,
  log: j.log,
})

const jobList = () => [...jobs.values()].sort((a, b) => b.startedAt - a.startedAt).map(publicJob)

function announceJobs () {
  broadcast({ type: 'jobs', jobs: jobList(), at: Date.now() })
}

function stamp () {
  const d = new Date()
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
}

function appendLog (job, stream, chunk) {
  for (const line of chunk.toString('utf8').split(/\r?\n/)) {
    if (!line.trim()) continue
    job.log.push({ at: Date.now(), stream, line: line.slice(0, 500) })
  }
  if (job.log.length > MAX_LOG_LINES) job.log = job.log.slice(-MAX_LOG_LINES)
}

/** Thrown for anything the user can fix by picking differently. */
class LaunchError extends Error {
  constructor (status, message) { super(message); this.status = status }
}

/** Spawn one lane runner. */
async function startJob ({ lane, legs, allLegs, notes }) {
  if (!LAUNCH_ENABLED) throw new LaunchError(403, 'launching is disabled (RAIL_GUI_LAUNCH=0)')
  const spec = LANES[lane]
  if (!spec) throw new LaunchError(400, `unknown lane ${JSON.stringify(lane)}`)

  for (const j of jobs.values()) {
    if (j.lane === lane && j.state === 'running') {
      throw new LaunchError(409, `the ${spec.label} is already running (${j.run_id})`)
    }
  }

  // Whitelist against the filesystem: a leg id is only ever a directory name.
  const known = new Set((await listLegs()).map(l => l.leg_id))
  const picked = []
  for (const legId of legs ?? []) {
    if (!known.has(legId)) throw new LaunchError(400, `no such practice leg: ${legId}`)
    if (!picked.includes(legId)) picked.push(legId)
  }
  if (!allLegs && !picked.length) throw new LaunchError(400, 'pick at least one leg')

  const python = pythonFor(lane)
  if (!python) {
    throw new LaunchError(412, `the ${spec.label} needs pandas — create .venv at the repo root first`)
  }

  const runId = `${stamp()}-${lane}`
  const args = [spec.script, '--run-id', runId, ...spec.legArgs(picked, allLegs)]
  if (notes) args.push('--notes', String(notes).slice(0, 300))

  const job = {
    job_id: runId,
    lane,
    run_id: runId,
    legs: allLegs ? [...known].sort() : picked,
    allLegs: Boolean(allLegs),
    notes: notes || null,
    cmd: [python, ...args].join(' '),
    state: 'running',
    startedAt: Date.now(),
    finishedAt: null,
    exitCode: null,
    error: null,
    log: [],
    child: null,
  }

  let child
  try {
    child = spawn(python, args, {
      cwd: REPO_ROOT,
      env: { ...process.env, RAIL_RUNS_DIR: RUNS_DIR, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
  } catch (err) {
    throw new LaunchError(500, `could not start ${python}: ${err.message}`)
  }
  job.child = child
  job.pid = child.pid
  jobs.set(job.job_id, job)

  child.stdout.on('data', c => { appendLog(job, 'out', c); announceJobs() })
  child.stderr.on('data', c => { appendLog(job, 'err', c); announceJobs() })
  child.on('error', err => {
    job.state = 'error'
    job.error = String(err.message ?? err)
    job.finishedAt = Date.now()
    job.child = null
    announceJobs()
  })
  child.on('close', (code, signal) => {
    if (job.state !== 'error') {
      job.state = job.cancelled ? 'cancelled' : code === 0 ? 'done' : 'error'
      if (code !== 0 && !job.cancelled) job.error = signal ? `killed by ${signal}` : `exit code ${code}`
    }
    job.exitCode = code
    job.finishedAt = Date.now()
    job.child = null
    announceJobs()
  })

  announceJobs()
  return job
}

function cancelJob (jobId) {
  const job = jobs.get(jobId)
  if (!job) return null
  if (job.child) {
    job.cancelled = true
    job.child.kill('SIGTERM')
    const child = job.child
    setTimeout(() => { if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL') }, 5000).unref?.()
  }
  return job
}

/** Read a JSON request body, with a hard size cap. */
function readJsonBody (req, limit = 256 * 1024) {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks = []
    req.on('data', c => {
      size += c.length
      if (size > limit) { reject(new LaunchError(413, 'body too large')); req.destroy() }
      else chunks.push(c)
    })
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8').trim()
      if (!text) return resolve({})
      try { resolve(JSON.parse(text)) } catch (err) { reject(new LaunchError(400, `bad JSON body: ${err.message}`)) }
    })
    req.on('error', reject)
  })
}

// Don't leave orphaned runners behind when the server goes down.
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => {
    for (const job of jobs.values()) job.child?.kill('SIGTERM')
    process.exit(0)
  })
}

// ---------------------------------------------------------------- utilities

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.map': 'application/json; charset=utf-8',
}

/** Resolve a client-supplied path inside RUNS_DIR, refusing traversal. */
function safeRunPath (...parts) {
  const joined = path.resolve(RUNS_DIR, ...parts.map(p => String(p)))
  if (joined !== RUNS_DIR && !joined.startsWith(RUNS_DIR + path.sep)) return null
  return joined
}

function sendJson (res, status, body) {
  const buf = Buffer.from(JSON.stringify(body))
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': buf.length,
    'cache-control': 'no-store',
  })
  res.end(buf)
}

async function readJsonIfExists (file) {
  try {
    const stat = await fsp.stat(file)
    if (stat.size > MAX_FILE_BYTES) return { error: `file too large (${stat.size} bytes)` }
    return JSON.parse(await fsp.readFile(file, 'utf8'))
  } catch (err) {
    if (err.code === 'ENOENT') return null
    // A half-written file is normal while an algorithm is running.
    return { error: String(err.message ?? err) }
  }
}

async function listDirs (dir) {
  try {
    const entries = await fsp.readdir(dir, { withFileTypes: true })
    return entries.filter(e => e.isDirectory()).map(e => e.name).sort()
  } catch {
    return []
  }
}

/** Read the last `limit` NDJSON records of a file, plus a byte offset cursor. */
async function readEvents (file, fromByte = 0, limit = 500) {
  let stat
  try { stat = await fsp.stat(file) } catch { return { events: [], cursor: 0, size: 0 } }
  const start = Math.max(0, Math.min(fromByte, stat.size))
  if (start === stat.size) return { events: [], cursor: stat.size, size: stat.size }

  const fh = await fsp.open(file, 'r')
  try {
    const len = stat.size - start
    const buf = Buffer.alloc(len)
    await fh.read(buf, 0, len, start)
    const text = buf.toString('utf8')
    // Keep only whole lines; the tail may be a partially flushed record.
    const lastNl = text.lastIndexOf('\n')
    if (lastNl < 0) return { events: [], cursor: start, size: stat.size }
    const complete = text.slice(0, lastNl)
    const cursor = start + Buffer.byteLength(complete, 'utf8') + 1
    const events = []
    for (const line of complete.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed) continue
      try { events.push(JSON.parse(trimmed)) } catch { events.push({ level: 'error', msg: 'unparseable event line', raw: trimmed.slice(0, 400) }) }
    }
    return { events: events.slice(-limit), cursor, size: stat.size, dropped: Math.max(0, events.length - limit) }
  } finally {
    await fh.close()
  }
}

// ------------------------------------------------------------ change watcher

/** relpath -> `${mtimeMs}:${size}` for everything under RUNS_DIR. */
let index = new Map()
const sseClients = new Set()

async function scan (dir, rel, out) {
  let entries
  try { entries = await fsp.readdir(dir, { withFileTypes: true }) } catch { return }
  for (const e of entries) {
    const child = path.join(dir, e.name)
    const childRel = rel ? `${rel}/${e.name}` : e.name
    if (e.isDirectory()) {
      await scan(child, childRel, out)
    } else if (e.isFile()) {
      try {
        const s = await fsp.stat(child)
        out.set(childRel, `${s.mtimeMs}:${s.size}`)
      } catch { /* vanished mid-scan */ }
    }
  }
}

async function pollOnce () {
  const next = new Map()
  await scan(RUNS_DIR, '', next)
  const changed = []
  for (const [rel, sig] of next) if (index.get(rel) !== sig) changed.push(rel)
  for (const rel of index.keys()) if (!next.has(rel)) changed.push(rel)
  index = next
  if (changed.length) broadcast({ type: 'change', paths: changed, at: Date.now() })
}

function broadcast (payload) {
  const frame = `data: ${JSON.stringify(payload)}\n\n`
  for (const res of sseClients) {
    try { res.write(frame) } catch { sseClients.delete(res) }
  }
}

// Seed the index so the first poll does not report every existing file.
await scan(RUNS_DIR, '', index)
setInterval(() => { pollOnce().catch(err => console.error('[watch]', err)) }, POLL_MS).unref?.()

// ------------------------------------------------------------------ handlers

/** Summary of one run: its run.json plus per-leg status. */
async function describeRun (runId) {
  const runDir = safeRunPath(runId)
  if (!runDir) return null
  const meta = await readJsonIfExists(path.join(runDir, 'run.json'))
  const legIds = await listDirs(runDir)
  const legs = []
  for (const legId of legIds) {
    const legDir = path.join(runDir, legId)
    const status = await readJsonIfExists(path.join(legDir, 'status.json'))
    const files = {}
    for (const name of ['shape.json', 'anchors.json', 'score.json', 'position.csv', 'station_calls.csv', 'events.ndjson']) {
      try {
        const s = await fsp.stat(path.join(legDir, name))
        files[name] = { size: s.size, mtimeMs: s.mtimeMs }
      } catch { /* absent */ }
    }
    legs.push({ leg_id: legId, status, files })
  }
  let mtimeMs = 0
  try { mtimeMs = (await fsp.stat(runDir)).mtimeMs } catch { /* gone */ }
  return { run_id: runId, meta, legs, mtimeMs }
}

async function handleApi (req, res, url) {
  // Decode here so `%2f` can never smuggle a separator past safeRunPath().
  const seg = url.pathname.split('/').filter(Boolean).map(decodeURIComponent) // ['api', ...]

  // GET /api/config
  if (seg.length === 2 && seg[1] === 'config') {
    return sendJson(res, 200, { runsDir: RUNS_DIR, repoRoot: REPO_ROOT, pollMs: POLL_MS })
  }

  // GET /api/events  (SSE)
  if (seg.length === 2 && seg[1] === 'events') {
    res.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-store',
      connection: 'keep-alive',
      'x-accel-buffering': 'no',
    })
    res.write(`data: ${JSON.stringify({ type: 'hello', runsDir: RUNS_DIR, jobs: jobList() })}\n\n`)
    sseClients.add(res)
    const ping = setInterval(() => { try { res.write(': ping\n\n') } catch { /* closed */ } }, 20000)
    req.on('close', () => { clearInterval(ping); sseClients.delete(res) })
    return
  }

  // GET /api/osm/:layer  — rail-network | rail-stations (static reference GeoJSON)
  if (seg.length === 3 && seg[1] === 'osm') {
    const entry = await loadOsmLayer(seg[2])
    if (!entry) return sendJson(res, 404, { error: 'no such osm layer', layer: seg[2] })
    const acceptsGzip = (req.headers['accept-encoding'] ?? '').includes('gzip')
    const headers = { 'content-type': 'application/geo+json; charset=utf-8', 'cache-control': 'public, max-age=3600' }
    if (acceptsGzip) {
      res.writeHead(200, { ...headers, 'content-encoding': 'gzip', 'content-length': entry.gz.length })
      return res.end(entry.gz)
    }
    res.writeHead(200, { ...headers, 'content-length': entry.raw.length })
    return res.end(entry.raw)
  }

  // GET /api/legs  — practice legs available to run on
  if (seg.length === 2 && seg[1] === 'legs' && req.method === 'GET') {
    return sendJson(res, 200, { practiceDir: PRACTICE_DIR, legs: await listLegs() })
  }

  // GET /api/jobs  — lane runners started from this server
  if (seg.length === 2 && seg[1] === 'jobs' && req.method === 'GET') {
    return sendJson(res, 200, { enabled: LAUNCH_ENABLED, jobs: jobList() })
  }

  // POST /api/jobs  {lane, legs[], allLegs, notes}  — start a run
  if (seg.length === 2 && seg[1] === 'jobs' && req.method === 'POST') {
    const body = await readJsonBody(req)
    const job = await startJob({
      lane: body.lane,
      legs: Array.isArray(body.legs) ? body.legs.map(String) : [],
      allLegs: Boolean(body.allLegs),
      notes: body.notes,
    })
    return sendJson(res, 201, { job: publicJob(job) })
  }

  // POST /api/jobs/:jobId/cancel
  if (seg.length === 4 && seg[1] === 'jobs' && seg[3] === 'cancel' && req.method === 'POST') {
    const job = cancelJob(seg[2])
    if (!job) return sendJson(res, 404, { error: 'no such job', job_id: seg[2] })
    return sendJson(res, 200, { job: publicJob(job) })
  }

  // GET /api/runs
  if (seg.length === 2 && seg[1] === 'runs') {
    const runIds = await listDirs(RUNS_DIR)
    const runs = []
    for (const runId of runIds) {
      const meta = await readJsonIfExists(safeRunPath(runId, 'run.json'))
      const legs = await listDirs(safeRunPath(runId))
      let mtimeMs = 0
      try { mtimeMs = (await fsp.stat(safeRunPath(runId))).mtimeMs } catch { /* gone */ }
      runs.push({ run_id: runId, meta, nLegs: legs.length, mtimeMs })
    }
    runs.sort((a, b) => b.mtimeMs - a.mtimeMs)
    return sendJson(res, 200, { runs })
  }

  // GET /api/runs/:runId
  if (seg.length === 3 && seg[1] === 'runs') {
    const run = await describeRun(seg[2])
    if (!run) return sendJson(res, 400, { error: 'bad run id' })
    return sendJson(res, 200, run)
  }

  // GET /api/runs/:runId/legs/:legId
  if (seg.length === 5 && seg[1] === 'runs' && seg[3] === 'legs') {
    const legDir = safeRunPath(seg[2], seg[4])
    if (!legDir) return sendJson(res, 400, { error: 'bad path' })
    const [status, shape, anchors, score, hydrated] = await Promise.all([
      readJsonIfExists(path.join(legDir, 'status.json')),
      readJsonIfExists(path.join(legDir, 'shape.json')),
      readJsonIfExists(path.join(legDir, 'anchors.json')),
      readJsonIfExists(path.join(legDir, 'score.json')),
      readJsonIfExists(path.join(legDir, 'hydrated.json')),
    ])
    return sendJson(res, 200, { leg_id: seg[4], status, shape, anchors, score, hydrated })
  }

  // GET /api/runs/:runId/legs/:legId/events?cursor=N
  if (seg.length === 6 && seg[1] === 'runs' && seg[3] === 'legs' && seg[5] === 'events') {
    const legDir = safeRunPath(seg[2], seg[4])
    if (!legDir) return sendJson(res, 400, { error: 'bad path' })
    const cursor = Number(url.searchParams.get('cursor') ?? 0)
    const limit = Math.min(2000, Number(url.searchParams.get('limit') ?? 500))
    return sendJson(res, 200, await readEvents(path.join(legDir, 'events.ndjson'), cursor, limit))
  }

  // GET /api/runs/:runId/legs/:legId/file/:name  — raw passthrough (CSV etc.)
  if (seg.length === 7 && seg[1] === 'runs' && seg[3] === 'legs' && seg[5] === 'file') {
    const file = safeRunPath(seg[2], seg[4], seg[6])
    if (!file) return sendJson(res, 400, { error: 'bad path' })
    try {
      const stat = await fsp.stat(file)
      if (stat.size > MAX_FILE_BYTES) return sendJson(res, 413, { error: 'file too large' })
      const body = await fsp.readFile(file)
      res.writeHead(200, { 'content-type': MIME[path.extname(file)] ?? 'text/plain; charset=utf-8', 'cache-control': 'no-store' })
      return res.end(body)
    } catch {
      return sendJson(res, 404, { error: 'not found' })
    }
  }

  return sendJson(res, 404, { error: 'no such endpoint', path: url.pathname })
}

async function serveStatic (req, res, url) {
  let rel = decodeURIComponent(url.pathname).replace(/^\/+/, '')
  if (rel === '') rel = 'index.html'
  let file = path.resolve(STATIC_DIR, rel)
  if (file !== STATIC_DIR && !file.startsWith(STATIC_DIR + path.sep)) {
    res.writeHead(403); return res.end('forbidden')
  }
  try {
    const stat = await fsp.stat(file)
    if (stat.isDirectory()) file = path.join(file, 'index.html')
  } catch {
    file = path.join(STATIC_DIR, 'index.html') // SPA fallback
  }
  try {
    const body = await fsp.readFile(file)
    res.writeHead(200, { 'content-type': MIME[path.extname(file)] ?? 'application/octet-stream' })
    res.end(body)
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
    res.end('Frontend not built. Run `npm run build` in web/frontend, or use `npm run dev` for the Vite dev server.')
  }
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host ?? 'localhost'}`)
  res.setHeader('access-control-allow-origin', '*')
  res.setHeader('access-control-allow-methods', 'GET, POST, OPTIONS')
  res.setHeader('access-control-allow-headers', 'content-type')
  if (req.method === 'OPTIONS') { res.writeHead(204); return res.end() }
  // Run data is read-only; POST exists only for the launcher (/api/jobs).
  if (req.method !== 'GET' && req.method !== 'HEAD' && req.method !== 'POST') {
    return sendJson(res, 405, { error: 'method not allowed' })
  }
  if (req.method === 'POST' && !url.pathname.startsWith('/api/jobs')) {
    return sendJson(res, 405, { error: 'read-only except /api/jobs' })
  }
  const done = url.pathname.startsWith('/api/')
    ? handleApi(req, res, url)
    : serveStatic(req, res, url)
  Promise.resolve(done).catch(err => {
    if (!err?.status) console.error('[server]', err)
    if (!res.headersSent) sendJson(res, err?.status ?? 500, { error: String(err.message ?? err) })
    else res.end()
  })
})

server.listen(PORT, HOST, () => {
  console.log(`rail-gui server  http://${HOST}:${PORT}`)
  console.log(`  runs dir       ${RUNS_DIR}`)
  console.log(`  practice       ${PRACTICE_DIR}`)
  console.log(`  static         ${STATIC_DIR}`)
  console.log(`  launcher       ${LAUNCH_ENABLED ? `enabled (python: ${pythonFor('motion') ?? 'none'})` : 'disabled'}`)
})
