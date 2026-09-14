// Rail hackathon run viewer — zero-dependency Node server.
//
// Serves the Svelte GUI and exposes the run directory that the algorithms
// write into. See web/README.md for the on-disk contract.

import http from 'node:http'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(__dirname, '..', '..')

const PORT = Number(process.env.RAIL_GUI_PORT ?? 5174)
const RUNS_DIR = path.resolve(process.env.RAIL_RUNS_DIR ?? path.join(REPO_ROOT, 'work', 'runs'))
const STATIC_DIR = path.join(__dirname, '..', 'frontend', 'dist')
const POLL_MS = Number(process.env.RAIL_GUI_POLL_MS ?? 500)
const MAX_FILE_BYTES = 8 * 1024 * 1024

fs.mkdirSync(RUNS_DIR, { recursive: true })

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
    res.write(`data: ${JSON.stringify({ type: 'hello', runsDir: RUNS_DIR })}\n\n`)
    sseClients.add(res)
    const ping = setInterval(() => { try { res.write(': ping\n\n') } catch { /* closed */ } }, 20000)
    req.on('close', () => { clearInterval(ping); sseClients.delete(res) })
    return
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
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    return sendJson(res, 405, { error: 'read-only server' })
  }
  const done = url.pathname.startsWith('/api/')
    ? handleApi(req, res, url)
    : serveStatic(req, res, url)
  Promise.resolve(done).catch(err => {
    console.error('[server]', err)
    if (!res.headersSent) sendJson(res, 500, { error: String(err.message ?? err) })
    else res.end()
  })
})

server.listen(PORT, () => {
  console.log(`rail-gui server  http://localhost:${PORT}`)
  console.log(`  runs dir       ${RUNS_DIR}`)
  console.log(`  static         ${STATIC_DIR}`)
})
