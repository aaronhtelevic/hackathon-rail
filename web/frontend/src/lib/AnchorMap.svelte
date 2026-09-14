<script>
  // Real OSM-tiled map of anchor candidates + the hydrated polyline (Leaflet).
  import { onMount, onDestroy } from 'svelte'
  import L from 'leaflet'
  import 'leaflet/dist/leaflet.css'
  import { getOsmLayer } from './api.js'

  let { anchors = null, hydrated = null, shape = null, legId = null } = $props()

  const list = $derived(anchors?.anchors ?? [])
  const poly = $derived(hydrated?.polyline ?? [])
  const segments = $derived(shape?.segments ?? [])

  let el
  let map
  let layer // L.LayerGroup holding all current markers/paths, redrawn on data change
  let osmNetworkLayer // L.GeoJSON — full Belgium rail network reference lines
  let osmStationsLayer // L.GeoJSON — reference station/halt points
  let showNetwork = $state(true)
  let showStations = $state(false)
  let showCircles = $state(false)
  let osmError = $state(null)
  let fittedLeg = null // leg_id we last fitBounds()'d for — avoids re-zooming on every live update
  let fittedFix = false // whether that fit actually included the live estimate (blue dot), not just anchors

  const turnIcon = label => L.divIcon({
    className: 'turn-icon',
    html: `<div class="turn-badge ${label === 'L' ? 'left' : 'right'}">${label}</div>`,
    iconSize: [20, 20], iconAnchor: [10, 10],
  })
  const stopIcon = L.divIcon({
    className: 'stop-icon',
    html: '<div class="stop-badge">STOP</div>',
    iconSize: [36, 20], iconAnchor: [18, 10],
  })

  // Nearest hydrated polyline point to a given timestamp — segments carry no lat/lon of their own.
  function posAtTime (t) {
    if (!poly.length || t == null) return null
    let best = null; let bestDelta = Infinity
    for (const p of poly) {
      const pt = p.t ?? p.time
      if (pt == null) continue
      const d = Math.abs(pt - t)
      if (d < bestDelta) { bestDelta = d; best = p }
    }
    return best ? [best.lat ?? best[0], best.lon ?? best[1]] : null
  }

  onMount(() => {
    map = L.map(el, { attributionControl: true }).setView([50.85, 4.35], 8)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map)
    layer = L.layerGroup().addTo(map)
    loadOsmLayers()
    draw()

    // Layout/panel resizes (e.g. moving panels around) leave Leaflet's cached
    // container size stale, which throws off zoom/pan math — keep it in sync.
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(el)
    onDestroy(() => ro.disconnect())
  })

  async function loadOsmLayers () {
    try {
      const [network, stations] = await Promise.all([
        getOsmLayer('rail-network'),
        getOsmLayer('rail-stations'),
      ])
      osmNetworkLayer = L.geoJSON(network, { style: { color: '#7a8699', weight: 1, opacity: 0.6 } })
      osmStationsLayer = L.geoJSON(stations, {
        pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
          radius: 3, color: '#7a8699', weight: 1, fillColor: '#c7ceda', fillOpacity: 0.9,
        }).bindTooltip(feature.properties?.name ?? feature.properties?.name_nl ?? 'station'),
      })
      if (showNetwork) osmNetworkLayer.addTo(map)
      if (showStations) osmStationsLayer.addTo(map)
    } catch (e) {
      osmError = String(e.message ?? e)
    }
  }

  function toggleNetwork () {
    showNetwork = !showNetwork
    if (!osmNetworkLayer) return
    if (showNetwork) osmNetworkLayer.addTo(map)
    else map.removeLayer(osmNetworkLayer)
  }

  function toggleStations () {
    showStations = !showStations
    if (!osmStationsLayer) return
    if (showStations) osmStationsLayer.addTo(map)
    else map.removeLayer(osmStationsLayer)
  }

  function toggleCircles () {
    showCircles = !showCircles
    draw()
  }

  onDestroy(() => map?.remove())

  const colorOf = source => ({ wifi: 'var(--accent)', warm_start: 'var(--ok)' }[source] ?? 'var(--absolute)')

  function draw () {
    if (!map || !layer) return
    layer.clearLayers()

    const bounds = []

    for (const a of list) {
      for (const c of a.candidates ?? []) {
        const ll = [c.lat, c.lon]
        bounds.push(ll)
        if (showCircles) {
          L.circle(ll, {
            radius: c.radius_m ?? 500,
            color: colorOf(a.source), weight: 0, fillColor: colorOf(a.source),
            fillOpacity: 0.06 + 0.3 * (c.weight ?? 1),
          }).addTo(layer)
        }
        L.circleMarker(ll, {
          radius: a.source === 'warm_start' ? 8 : 3, color: '#0b0d12', weight: 1, fillColor: colorOf(a.source), fillOpacity: 1,
        }).bindTooltip(`${a.source} @ ${new Date(a.t).toLocaleTimeString()} · w=${c.weight ?? 1} · ±${c.radius_m ?? '?'} m`)
          .addTo(layer)
      }
    }

    let liveFix = null
    if (poly.length > 1) {
      const line = poly.map(p => [p.lat ?? p[0], p.lon ?? p[1]])
      bounds.push(...line)
      L.polyline(line, { color: 'var(--joint)', weight: 5, lineJoin: 'round' }).addTo(layer)

      liveFix = line[line.length - 1]
      L.circleMarker(liveFix, {
        radius: 8, color: '#fff', weight: 2, fillColor: '#1e6bff', fillOpacity: 1,
      }).bindTooltip('estimated current location — live guess').addTo(layer)
    }

    for (const seg of segments) {
      if (seg.type === 'left' || seg.type === 'right') {
        const pos = posAtTime(seg.t_start)
        if (pos) {
          bounds.push(pos)
          L.marker(pos, { icon: turnIcon(seg.type === 'left' ? 'L' : 'R') })
            .bindTooltip(`${seg.type} turn · ${seg.turn_deg ?? '?'}°`)
            .addTo(layer)
        }
      }
      if (seg.moving === false) {
        const pos = posAtTime((seg.t_start + seg.t_end) / 2)
        if (pos) {
          bounds.push(pos)
          L.marker(pos, { icon: stopIcon }).bindTooltip('stopped').addTo(layer)
        }
      }
    }

    if (legId !== fittedLeg) { fittedLeg = legId; fittedFix = false }
    map.invalidateSize()

    if (liveFix) {
      // Always keep the live estimate centered — this is the thing the user is tracking.
      const z = fittedFix ? map.getZoom() : 15
      map.setView(liveFix, z, { animate: fittedFix })
      fittedFix = true
    } else if (bounds.length && !fittedFix) {
      // No live fix yet (fresh run, still waiting on hydration) — frame what we have.
      map.fitBounds(bounds, { padding: [40, 40] })
    }
  }

  $effect(() => { list; poly; segments; draw() })
</script>

{#if !list.length && poly.length < 2}
  <p class="dim">Nothing positioned yet — no anchor candidates, no hydrated polyline.</p>
{/if}
<div class="layers">
  <label><input type="checkbox" checked={showNetwork} onchange={toggleNetwork} /> OSM rail network</label>
  <label><input type="checkbox" checked={showStations} onchange={toggleStations} /> OSM stations</label>
  <label><input type="checkbox" checked={showCircles} onchange={toggleCircles} /> anchor uncertainty circles</label>
  {#if osmError}<span class="dim err">OSM layers failed: {osmError}</span>{/if}
</div>
<div class="map" bind:this={el}></div>
<p class="dim scalebar">
  {list.length} anchor{list.length === 1 ? '' : 's'} ·
  {poly.length} hydrated point{poly.length === 1 ? '' : 's'} ·
  circle radius = anchor uncertainty
</p>

<style>
  .map { width: 100%; height: 600px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-2); }
  .scalebar { font-size: 11px; margin: 6px 0 0; }
  .layers { display: flex; gap: 12px; align-items: center; font-size: 11px; margin: 0 0 6px; }
  .layers label { display: flex; gap: 4px; align-items: center; cursor: pointer; }
  .err { color: var(--bad); }
  :global(.turn-badge) {
    width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700; color: #0b0d12; border: 2px solid #0b0d12;
  }
  :global(.turn-badge.left) { background: #6ea8fe; }
  :global(.turn-badge.right) { background: #f0b866; }
  :global(.stop-badge) {
    display: flex; align-items: center; justify-content: center;
    width: 36px; height: 20px; background: #d1483f; color: #fff; font-size: 9px; font-weight: 700;
    border: 2px solid #fff; border-radius: 4px; letter-spacing: 0.5px;
  }
</style>
