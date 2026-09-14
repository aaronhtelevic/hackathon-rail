<script>
  // Equirectangular scatter of anchor candidates + the hydrated polyline.
  // Deliberately map-tile free: this runs offline on a hackathon laptop.
  let { anchors = null, hydrated = null } = $props()

  const list = $derived(anchors?.anchors ?? [])
  const poly = $derived(hydrated?.polyline ?? [])

  const W = 520, H = 320, PAD = 26

  let box = $derived.by(() => {
    const pts = []
    for (const a of list) for (const c of a.candidates ?? []) pts.push([c.lat, c.lon])
    for (const p of poly) pts.push([p.lat ?? p[0], p.lon ?? p[1]])
    if (!pts.length) return null
    const lats = pts.map(p => p[0]); const lons = pts.map(p => p[1])
    const latMid = (Math.min(...lats) + Math.max(...lats)) / 2
    // Metres-per-degree ratio so the aspect is not wildly wrong at 51°N.
    const kx = Math.cos((latMid * Math.PI) / 180)
    let minLat = Math.min(...lats), maxLat = Math.max(...lats)
    let minLon = Math.min(...lons), maxLon = Math.max(...lons)
    const padLat = Math.max(1e-4, (maxLat - minLat) * 0.1)
    const padLon = Math.max(1e-4, (maxLon - minLon) * 0.1)
    minLat -= padLat; maxLat += padLat; minLon -= padLon; maxLon += padLon
    const spanLat = maxLat - minLat
    const spanLon = (maxLon - minLon) * kx
    const scale = Math.min((W - 2 * PAD) / spanLon, (H - 2 * PAD) / spanLat)
    return { minLat, maxLat, minLon, maxLon, kx, scale, latMid }
  })

  const x = lon => box ? PAD + (lon - box.minLon) * box.kx * box.scale : 0
  const y = lat => box ? H - PAD - (lat - box.minLat) * box.scale : 0
  // radius_m -> px, via metres per degree latitude (~111320 m).
  const r = m => box ? Math.max(1.5, (m / 111320) * box.scale) : 0

  const polyPath = $derived(
    poly.map((p, i) => `${i ? 'L' : 'M'}${x(p.lon ?? p[1]).toFixed(1)},${y(p.lat ?? p[0]).toFixed(1)}`).join(' ')
  )
</script>

{#if !box}
  <p class="dim">Nothing positioned yet — no anchor candidates, no hydrated polyline.</p>
{:else}
  <svg viewBox="0 0 {W} {H}" role="img" aria-label="anchor candidates and hydrated polyline">
    {#each list as a, i (i)}
      {#each a.candidates ?? [] as c, j (j)}
        <circle class="halo {a.source}" cx={x(c.lon)} cy={y(c.lat)} r={r(c.radius_m ?? 500)}
                opacity={0.06 + 0.3 * (c.weight ?? 1)} />
        <circle class="pt {a.source}" cx={x(c.lon)} cy={y(c.lat)} r="3">
          <title>{a.source} @ {new Date(a.t).toLocaleTimeString()} · w={c.weight ?? 1} · ±{c.radius_m ?? '?'} m</title>
        </circle>
      {/each}
    {/each}
    {#if poly.length > 1}
      <path class="poly" d={polyPath} />
    {/if}
  </svg>
  <p class="dim scalebar">
    {list.length} anchor{list.length === 1 ? '' : 's'} ·
    {poly.length} hydrated point{poly.length === 1 ? '' : 's'} ·
    circle radius = anchor uncertainty
  </p>
{/if}

<style>
  svg { width: 100%; height: auto; background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; }
  .halo { fill: var(--absolute); }
  .halo.wifi { fill: var(--accent); }
  .halo.warm_start { fill: var(--ok); }
  .pt { fill: var(--absolute); stroke: #0b0d12; stroke-width: 1; }
  .pt.wifi { fill: var(--accent); }
  .pt.warm_start { fill: var(--ok); }
  .poly { fill: none; stroke: var(--joint); stroke-width: 2; stroke-linejoin: round; }
  .scalebar { font-size: 11px; margin: 6px 0 0; }
</style>
