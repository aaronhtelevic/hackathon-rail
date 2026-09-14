// Thin fetch wrappers over the server API + the change stream. Everything is
// read-only except the launcher (/api/jobs), which starts a lane runner.

async function request (url, init) {
  const res = await fetch(url, { cache: 'no-store', ...init })
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(body?.error ?? `${res.status} ${res.statusText} — ${url}`)
  return body
}

const getJson = url => request(url)

const postJson = (url, body) => request(url, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body ?? {}),
})

export const getConfig = () => getJson('/api/config')
export const listRuns = () => getJson('/api/runs')
export const getRun = runId => getJson(`/api/runs/${encodeURIComponent(runId)}`)
export const getLeg = (runId, legId) =>
  getJson(`/api/runs/${encodeURIComponent(runId)}/legs/${encodeURIComponent(legId)}`)
export const getEvents = (runId, legId, cursor = 0, limit = 500) =>
  getJson(`/api/runs/${encodeURIComponent(runId)}/legs/${encodeURIComponent(legId)}/events?cursor=${cursor}&limit=${limit}`)
export const fileUrl = (runId, legId, name) =>
  `/api/runs/${encodeURIComponent(runId)}/legs/${encodeURIComponent(legId)}/file/${encodeURIComponent(name)}`

// --- launcher ---------------------------------------------------------------
export const listLegs = () => getJson('/api/legs')
export const listJobs = () => getJson('/api/jobs')
/** opts: {lane, legs[], allLegs, notes, demoSpeed, cold} */
export const startRun = opts => postJson('/api/jobs', opts)
export const cancelRun = jobId => postJson(`/api/jobs/${encodeURIComponent(jobId)}/cancel`)

// Reference OSM layers (belgium_rail_network / belgium_rail_stations) — static, cache-friendly.
export const getOsmLayer = layer => getJson(`/api/osm/${encodeURIComponent(layer)}`)

/**
 * Subscribe to the server's event stream. `onChange(paths)` receives run-dir
 * relative paths, e.g. "20260914-1503-motion/ic830_00/shape.json";
 * `onJobs(jobs)` receives the launcher's job list whenever it moves.
 * Returns an unsubscribe function.
 */
export function watchChanges (onChange, onState, onJobs) {
  let source = null
  let closed = false
  let retry = null

  const connect = () => {
    if (closed) return
    source = new EventSource('/api/events')
    source.onopen = () => onState?.('live')
    source.onmessage = ev => {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'change') onChange(msg.paths)
      else if (msg.type === 'jobs') onJobs?.(msg.jobs)
      else if (msg.type === 'hello' && msg.jobs) onJobs?.(msg.jobs)
    }
    source.onerror = () => {
      onState?.('reconnecting')
      source?.close()
      if (!closed) retry = setTimeout(connect, 1500)
    }
  }
  connect()

  return () => { closed = true; clearTimeout(retry); source?.close() }
}
