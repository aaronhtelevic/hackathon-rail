<script>
  // Real OSM-tiled map of anchor candidates + the hydrated polyline (Leaflet).
  import { onMount, onDestroy } from 'svelte'
  import L from 'leaflet'
  import 'leaflet/dist/leaflet.css'

  let { anchors = null, hydrated = null } = $props()

  const list = $derived(anchors?.anchors ?? [])
  const poly = $derived(hydrated?.polyline ?? [])

  let el
  let map
  let layer // L.LayerGroup holding all current markers/paths, redrawn on data change

  onMount(() => {
    map = L.map(el, { attributionControl: true }).setView([50.85, 4.35], 8)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map)
    layer = L.layerGroup().addTo(map)
    draw()
  })

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
        L.circle(ll, {
          radius: c.radius_m ?? 500,
          color: colorOf(a.source), weight: 0, fillColor: colorOf(a.source),
          fillOpacity: 0.06 + 0.3 * (c.weight ?? 1),
        }).addTo(layer)
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
    }

    if (bounds.length) map.fitBounds(bounds, { padding: [26, 26] })
  }

  $effect(() => { list; poly; draw() })
</script>

{#if !list.length && poly.length < 2}
  <p class="dim">Nothing positioned yet — no anchor candidates, no hydrated polyline.</p>
{/if}
<div class="map" bind:this={el}></div>
<p class="dim scalebar">
  {list.length} anchor{list.length === 1 ? '' : 's'} ·
  {poly.length} hydrated point{poly.length === 1 ? '' : 's'} ·
  circle radius = anchor uncertainty
</p>

<style>
  .map { width: 100%; height: 320px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-2); }
  .scalebar { font-size: 11px; margin: 6px 0 0; }
</style>
