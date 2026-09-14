// Fake algorithm: writes a synthetic run into the runs dir so the GUI can be
// developed without the real pipeline. `npm run demo` from web/.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const RUNS = path.resolve(process.env.RAIL_RUNS_DIR ?? path.join(WEB, '..', 'work', 'runs'))
const runId = `${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '').replace(/(\d{8})(\d{6})/, '$1-$2')}-demo`
const runDir = path.join(RUNS, runId)
const LEGS = ['ic830_00', 'ic4112_02', 's51_785_01']

const write = (dir, name, obj) => {
  const tmp = path.join(dir, `.${name}.tmp`)
  fs.writeFileSync(tmp, typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2))
  fs.renameSync(tmp, path.join(dir, name))
}
const sleep = ms => new Promise(r => setTimeout(r, ms))

fs.mkdirSync(runDir, { recursive: true })
write(runDir, 'run.json', {
  run_id: runId, algorithm: 'demo', lane: 'joint', track: 'warm',
  state: 'running', started_at: Date.now(), notes: 'synthetic data — not a real solve',
})
console.log(`demo run → ${runDir}`)

for (const legId of LEGS) {
  const dir = path.join(runDir, legId)
  fs.mkdirSync(dir, { recursive: true })
  const t0 = Date.now() - 1_200_000
  const evPath = path.join(dir, 'events.ndjson')
  const ev = (stage, msg, pct, extra = {}) => {
    fs.appendFileSync(evPath, JSON.stringify({ at: Date.now(), stage, msg, pct, ...extra }) + '\n')
    write(dir, 'status.json', { leg_id: legId, state: 'running', lane: 'joint', stage, pct, message: msg, updated_at: Date.now() })
  }

  ev('I1', 'loaded 887k accel + 887k gyro rows', 0.05)
  await sleep(350)
  ev('O1', 'orientation recovered, gravity axis = -Y', 0.2)
  await sleep(350)

  // shape.json — motion lane
  const segments = []
  let t = t0
  for (let i = 0; i < 14; i++) {
    const type = i % 4 === 1 ? 'left' : i % 4 === 3 ? 'right' : 'straight'
    const dur = (type === 'straight' ? 60 + Math.random() * 120 : 12 + Math.random() * 18) * 1000
    const moving = !(type === 'straight' && i === 6)
    const speed = moving ? 18 + Math.random() * 12 : 0
    segments.push({
      seg_id: i, type, t_start: Math.round(t), t_end: Math.round(t + dur),
      turn_deg: type === 'straight' ? 0 : (type === 'left' ? -1 : 1) * (20 + Math.random() * 50),
      moving, speed_prior_mps: +speed.toFixed(1), length_prior_m: Math.round(speed * dur / 1000),
      confidence: +(0.6 + Math.random() * 0.4).toFixed(2),
    })
    t += dur
  }
  write(dir, 'shape.json', { leg_id: legId, t_start: t0, t_end: Math.round(t), orientation_ok: true, segments })
  ev('M3', `shape.json written — ${segments.length} segments`, 0.45)
  await sleep(400)

  // anchors.json — absolute lane (one leg deliberately has none: the H7 path)
  const anchors = legId.startsWith('s51_785') ? [] : Array.from({ length: 6 }, (_, i) => {
    const tt = t0 + (i + 1) * ((t - t0) / 8)
    const lat = 51.05 + i * 0.03 + Math.random() * 0.004
    const lon = 4.30 + i * 0.035 + Math.random() * 0.004
    return {
      t: Math.round(tt), source: i === 0 ? 'warm_start' : i % 3 === 0 ? 'wifi' : 'cell',
      candidates: i % 3 === 0
        ? [{ lat, lon, radius_m: 900, weight: 1 }]
        : [{ lat, lon, radius_m: 1200, weight: 0.6 },
           { lat: lat - 0.02, lon: lon + 0.01, radius_m: 1500, weight: 0.4 }],
    }
  })
  write(dir, 'anchors.json', { leg_id: legId, anchors })
  ev('H1', anchors.length ? `anchors.json — ${anchors.length} anchors` : 'no cell/wifi — H7 shape-only path', 0.6,
     anchors.length ? {} : { level: 'warn' })
  await sleep(400)

  // hydration output
  const polyline = segments.map((s, i) => ({
    t: s.t_start, lat: 51.05 + i * 0.013 + Math.random() * 0.002,
    lon: 4.30 + i * 0.016 + Math.random() * 0.002, distance_m: i * 1400,
  }))
  write(dir, 'hydrated.json', { leg_id: legId, polyline })
  ev('H4', 'hydration solved, residual 412 m', 0.85)
  await sleep(400)

  write(dir, 'position.csv', 'epochMillis,distanceAlongTrackM\n' +
    polyline.map(p => `${p.t},${p.distance_m}`).join('\n') + '\n')
  write(dir, 'station_calls.csv', `epochMillis,stationNameGuess\n${Math.round(t) - 60_000},Antwerpen-Centraal\n`)
  ev('T4', 'station call emitted 500 m out', 0.95)

  write(dir, 'score.json', {
    leg_id: legId,
    routeDiscovery: { guess: legId.split('_')[0], correct: true, lockInTimeS: 148 },
    positionAccuracy: { medianErrorM: 380 + Math.random() * 300, meanErrorM: 520, maxErrorM: 1840, nScored: polyline.length },
    stationDetection: { detected: true, timingErrorS: 41, nCallsInLeg: 1 },
  })
  write(dir, 'status.json', { leg_id: legId, state: 'done', lane: 'joint', stage: 'done', pct: 1, updated_at: Date.now() })
  fs.appendFileSync(evPath, JSON.stringify({ at: Date.now(), stage: 'done', msg: 'leg complete', pct: 1 }) + '\n')
  console.log(`  ${legId} done`)
}

write(runDir, 'run.json', {
  run_id: runId, algorithm: 'demo', lane: 'joint', track: 'warm',
  state: 'done', started_at: Date.now(), finished_at: Date.now(),
})
