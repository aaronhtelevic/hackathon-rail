// Thin fetch wrappers over the read-only server API + the change stream.

async function getJson (url) {
  const res = await fetch(url, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`)
  return res.json()
}

export const getConfig = () => getJson('/api/config')
export const listRuns = () => getJson('/api/runs')
export const getRun = runId => getJson(`/api/runs/${encodeURIComponent(runId)}`)
export const getLeg = (runId, legId) =>
  getJson(`/api/runs/${encodeURIComponent(runId)}/legs/${encodeURIComponent(legId)}`)
export const getEvents = (runId, legId, cursor = 0, limit = 500) =>
  getJson(`/api/runs/${encodeURIComponent(runId)}/legs/${encodeURIComponent(legId)}/events?cursor=${cursor}&limit=${limit}`)
export const fileUrl = (runId, legId, name) =>
  `/api/runs/${encodeURIComponent(runId)}/legs/${encodeURIComponent(legId)}/file/${encodeURIComponent(name)}`

// Reference OSM layers (belgium_rail_network / belgium_rail_stations) — static, cache-friendly.
export const getOsmLayer = layer => getJson(`/api/osm/${encodeURIComponent(layer)}`)

/**
 * Subscribe to file-change notifications. `onChange(paths)` receives run-dir
 * relative paths, e.g. "20260914-1503-motion/ic830_00/shape.json".
 * Returns an unsubscribe function.
 */
export function watchChanges (onChange, onState) {
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
