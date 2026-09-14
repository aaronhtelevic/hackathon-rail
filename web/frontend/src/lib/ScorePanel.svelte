<script>
  // Renders whatever scorer/scorer.py:score_leg() produced, if the algorithm
  // dropped it as score.json. Shape-tolerant: unknown keys are shown raw.
  import Info from './Info.svelte'
  import { SCORE_INFO } from './scoreInfo.js'
  let { score = null } = $props()

  const num = (v, digits = 1) => (typeof v === 'number' ? v.toFixed(digits) : '—')
  const pos = $derived(score?.positionAccuracy ?? score?.position ?? null)
  const route = $derived(score?.routeDiscovery ?? score?.route ?? null)
  const station = $derived(score?.stationDetection ?? null)
  const firstFix = $derived(score?.firstFix ?? null)
</script>

{#if !score}
  <p class="dim">No score.json yet. Write one per leg to see metrics here.</p>
{:else if score.error}
  <p class="err">{score.error}</p>
{:else}
  <div class="grid">
    <div class="cell">
      <span class="k dim">route guess<Info text={SCORE_INFO.routeGuess} /></span>
      <span class="v">{route?.finalGuess ?? route?.guess ?? '—'}</span>
      <span class="sub" class:ok={route?.correct} class:bad={route?.correct === false}>
        {route?.correct == null ? '' : route.correct ? 'correct' : 'wrong'}
        {route?.lockInTimeS != null ? ` · lock-in ${num(route.lockInTimeS, 0)}s` : ''}
        <Info text={SCORE_INFO.lockIn} />
      </span>
    </div>
    <div class="cell">
      <span class="k dim">median err<Info text={SCORE_INFO.medianErr} /></span>
      <span class="v">{num(pos?.medianErrorM ?? pos?.median)} m</span>
      <span class="sub dim">baseline 974 m</span>
    </div>
    <div class="cell">
      <span class="k dim">mean / max err<Info text={SCORE_INFO.meanMaxErr} /></span>
      <span class="v">{num(pos?.meanErrorM ?? pos?.mean)} / {num(pos?.maxErrorM ?? pos?.max)} m</span>
      <span class="sub dim">rows scored {pos?.nScored ?? pos?.n ?? '—'}</span>
    </div>
    <div class="cell">
      <span class="k dim">station call<Info text={SCORE_INFO.station} /></span>
      <span class="v" class:ok={station?.detected} class:bad={station?.detected === false}>
        {station?.detected == null ? '—' : station.detected ? 'hit' : 'miss'}
      </span>
      <span class="sub dim">Δt {num(station?.timingErrorS, 0)}s (±90 s)<Info text={SCORE_INFO.stationTiming} /></span>
    </div>
    {#if firstFix}
      <div class="cell">
        <span class="k dim">first fix<Info text={SCORE_INFO.firstFix} /></span>
        <span class="v">{num(firstFix.timeToFirstFixS, 0)} s</span>
        <span class="sub dim">err {num(firstFix.errorM)} m (&lt;1000 m)<Info text={SCORE_INFO.ffErr} /></span>
      </div>
    {/if}
  </div>
{/if}

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
  .cell { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
  .k { display: block; font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase; }
  .v { display: block; font-family: var(--mono); font-size: 18px; margin-top: 2px; }
  .sub { display: block; font-size: 11px; margin-top: 2px; }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
  .err { color: var(--bad); }
</style>
