<script>
  // Both lanes on one time axis: shape segments (motion) above, anchors
  // (absolute) below. This is the visual form of the join contract.
  let { shape = null, anchors = null } = $props()

  const COLOR = { left: '#6ea8fe', right: '#f0b866', straight: '#39415a' }

  let span = $derived.by(() => {
    const ts = []
    if (shape?.t_start != null) ts.push(shape.t_start, shape.t_end)
    for (const s of shape?.segments ?? []) ts.push(s.t_start, s.t_end)
    for (const a of anchors?.anchors ?? []) ts.push(a.t)
    if (!ts.length) return null
    const t0 = Math.min(...ts); const t1 = Math.max(...ts)
    return { t0, t1, dur: Math.max(1, t1 - t0) }
  })

  const pct = t => span ? ((t - span.t0) / span.dur) * 100 : 0
  const secs = ms => (ms / 1000).toFixed(0)

  let hovered = $state(null)
</script>

{#if !span}
  <p class="dim">No shape or anchors yet.</p>
{:else}
  <div class="axis dim mono">
    <span>t+0s</span>
    <span>t+{secs(span.dur / 2)}s</span>
    <span>t+{secs(span.dur)}s</span>
  </div>

  <div class="track" aria-label="motion lane shape segments">
    {#each shape?.segments ?? [] as seg (seg.seg_id)}
      <div
        class="seg"
        class:stopped={seg.moving === false}
        style="left:{pct(seg.t_start)}%; width:{Math.max(0.15, pct(seg.t_end) - pct(seg.t_start))}%;
               background:{COLOR[seg.type] ?? COLOR.straight};
               opacity:{0.35 + 0.65 * (seg.confidence ?? 1)}"
        onmouseenter={() => (hovered = seg)}
        onmouseleave={() => (hovered = null)}
        role="presentation"
      ></div>
    {/each}
  </div>

  <div class="track anchors" aria-label="absolute lane anchors">
    {#each anchors?.anchors ?? [] as a, i (i)}
      <div
        class="anchor {a.source}"
        style="left:{pct(a.t)}%"
        title="{a.source} · {a.candidates?.length ?? 0} candidate(s)"
      ></div>
    {/each}
    {#if !(anchors?.anchors ?? []).length}
      <span class="empty dim">no anchors — shape-only leg (H7 path)</span>
    {/if}
  </div>

  <div class="legend dim">
    <span><i style="background:{COLOR.left}"></i>left</span>
    <span><i style="background:{COLOR.right}"></i>right</span>
    <span><i style="background:{COLOR.straight}"></i>straight</span>
    <span><i class="hatch"></i>stopped</span>
    <span><i class="dot cell"></i>cell</span>
    <span><i class="dot wifi"></i>wifi</span>
    <span><i class="dot warm_start"></i>warm start</span>
  </div>

  {#if hovered}
    <div class="tip mono">
      seg {hovered.seg_id} · <b>{hovered.type}</b> ·
      {((hovered.t_end - hovered.t_start) / 1000).toFixed(1)}s ·
      turn {hovered.turn_deg ?? 0}° ·
      moving {String(hovered.moving)} ·
      len prior {hovered.length_prior_m ?? '—'} m ·
      conf {hovered.confidence ?? '—'}
    </div>
  {:else}
    <div class="tip dim">Hover a segment for its contract fields.</div>
  {/if}
{/if}

<style>
  .axis { display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 4px; }
  .track {
    position: relative;
    height: 26px;
    background: var(--panel-2);
    border: 1px solid var(--line);
    border-radius: 6px;
    overflow: hidden;
  }
  .track.anchors { height: 18px; margin-top: 4px; }
  .seg { position: absolute; top: 0; bottom: 0; border-right: 1px solid rgba(0, 0, 0, .5); }
  .seg.stopped {
    background-image: repeating-linear-gradient(45deg, rgba(255,255,255,.28) 0 3px, transparent 3px 6px);
  }
  .anchor {
    position: absolute; top: 3px; width: 8px; height: 8px; margin-left: -4px;
    border-radius: 99px; background: var(--absolute); box-shadow: 0 0 0 2px var(--panel-2);
  }
  .anchor.wifi { background: var(--accent); }
  .anchor.warm_start { background: var(--ok); }
  .empty { position: absolute; left: 8px; top: 1px; font-size: 11px; }
  .legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 11px; margin-top: 6px; align-items: center; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
  .legend i.hatch { background: #39415a; background-image: repeating-linear-gradient(45deg, rgba(255,255,255,.28) 0 3px, transparent 3px 6px); }
  .legend i.dot { border-radius: 99px; }
  .legend i.dot.cell { background: var(--absolute); }
  .legend i.dot.wifi { background: var(--accent); }
  .legend i.dot.warm_start { background: var(--ok); }
  .tip { margin-top: 8px; font-size: 11px; min-height: 17px; }
</style>
