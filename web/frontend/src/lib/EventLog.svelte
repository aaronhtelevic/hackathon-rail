<script>
  // Tail of events.ndjson, newest last, autoscrolled while pinned to bottom.
  let { events = [], follow = true } = $props()
  let el = $state(null)

  $effect(() => {
    events.length // re-run when new events arrive
    if (follow && el) el.scrollTop = el.scrollHeight
  })

  const time = t => (t ? new Date(t).toLocaleTimeString('en-GB', { hour12: false }) : '')
</script>

<div class="log mono scroll" bind:this={el}>
  {#each events as ev, i (i)}
    <div class="row {ev.level ?? 'info'}">
      <span class="t dim">{time(ev.at)}</span>
      {#if ev.stage}<span class="stage">{ev.stage}</span>{/if}
      <span class="msg">{ev.msg ?? JSON.stringify(ev)}</span>
      {#if ev.pct != null}<span class="pct dim">{Math.round(ev.pct * 100)}%</span>{/if}
    </div>
  {:else}
    <div class="dim">No events yet.</div>
  {/each}
</div>

<style>
  .log { height: 240px; font-size: 11.5px; line-height: 1.65; }
  .row { display: flex; gap: 8px; white-space: pre-wrap; word-break: break-word; }
  .row.warn .msg { color: var(--absolute); }
  .row.error .msg { color: var(--bad); }
  .t { flex: 0 0 auto; }
  .stage { flex: 0 0 auto; color: var(--accent); }
  .pct { margin-left: auto; }
</style>
