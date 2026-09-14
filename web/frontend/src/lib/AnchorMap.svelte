<script>
  // Real OSM-tiled map of anchor candidates + the hydrated polyline (Leaflet).
  import { onMount, onDestroy } from 'svelte'
  import L from 'leaflet'
  import 'leaflet/dist/leaflet.css'
  import { getOsmLayer } from './api.js'

  let { anchors = null, hydrated = null } = $props()

  const list = $derived(anchors?.anchors ?? [])
  const poly = $derived(hydrated?.polyline ?? [])

  let el
  let map
  let layer // L.LayerGroup holding all current markers/paths, redrawn on data change
  let osmNetworkLayer // L.GeoJSON — full Belgium rail network reference lines
  let osmStationsLayer // L.GeoJSON — reference station/halt points
  let showNetwork = $state(true)
  let showStations = $state(false)
  let showCircles = $state(false)
  let osmError = $state(null)

  onMount(() => {
    map = L.map(el, { attributionControl: true }).setView([50.85, 4.35], 8)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map)
    layer = L.layerGroup().addTo(map)
    loadOsmLayers()
    draw()
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
          radius: 3, color: '#0b0d12', weight: 1, fillColor: colorOf(a.source), fillOpacity: 1,
        }).bindTooltip(`${a.source} @ ${new Date(a.t).toLocaleTimeString()} · w=${c.weight ?? 1} · ±${c.radius_m ?? '?'} m`)
          .addTo(layer)
      }
    }

    if (poly.length > 1) {
      const line = poly.map(p => [p.lat ?? p[0], p.lon ?? p[1]])
      bounds.push(...line)
      L.polyline(line, { color: 'var(--joint)', weight: 2, lineJoin: 'round' }).addTo(layer)

      const last = line[line.length - 1]
      L.circleMarker(last, {
        radius: 7, color: '#fff', weight: 2, fillColor: '#1e6bff', fillOpacity: 1,
      }).bindTooltip('estimated current location').addTo(layer)
    }

    if (bounds.length) map.fitBounds(bounds, { padding: [26, 26] })
  }

  $effect(() => { list; poly; draw() })
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
  .map { width: 100%; height: 320px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-2); }
  .scalebar { font-size: 11px; margin: 6px 0 0; }
  .layers { display: flex; gap: 12px; align-items: center; font-size: 11px; margin: 0 0 6px; }
  .layers label { display: flex; gap: 4px; align-items: center; cursor: pointer; }
  .err { color: var(--bad); }
</style>
