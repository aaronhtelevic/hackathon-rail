<script>
  // Every leg's score.json across every run, as one table — no map, no timeline.
  import { listScores } from './api.js'
  import Info from './Info.svelte'
  import { SCORE_INFO } from './scoreInfo.js'

  let { visible = false } = $props()

  let rows = $state([])
  let loading = $state(false)
  let error = $state(null)
  let sortKey = $state('run_id')
  let sortDir = $state(1)

  const num = (v, digits = 1) => (typeof v === 'number' ? v.toFixed(digits) : '—')

  function flat (r) {
    const s = r.score ?? {}
    const pa = s.positionAccuracy ?? {}
    const rd = s.routeDiscovery ?? {}
    const sd = s.stationDetection ?? {}
    const ff = s.firstFix ?? {}
    return {
      run_id: r.run_id,
      leg_id: r.leg_id,
      algorithm: r.algorithm ?? '—',
      track: r.track ?? '—',
      medErrM: pa.medianErrorM ?? null,
      meanErrM: pa.meanErrorM ?? null,
      maxErrM: pa.maxErrorM ?? null,
      route: rd.finalGuess ?? null,
      routeOK: rd.correct ?? null,
      lockInS: rd.lockInTimeS ?? null,
      stationOK: sd.detected ?? null,
      stTimingS: sd.timingErrorS ?? null,
      ttffS: ff.timeToFirstFixS ?? null,
      ffErrM: ff.firstFixErrorM ?? null,
      err: s.error ?? null,
    }
  }

  const flatRows = $derived(rows.map(flat))
  const sorted = $derived(
    [...flatRows].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      return av < bv ? -sortDir : av > bv ? sortDir : 0
    })
  )

  const scored = $derived(flatRows.filter(r => r.medErrM != null))
  const medOfMed = $derived(scored.length ? median(scored.map(r => r.medErrM)) : null)
  const routeAcc = $derived(flatRows.length ? flatRows.filter(r => r.routeOK).length / flatRows.length : null)
  const stationAcc = $derived(flatRows.length ? flatRows.filter(r => r.stationOK).length / flatRows.length : null)

  function median (arr) {
    const s = [...arr].sort((a, b) => a - b)
    const mid = Math.floor(s.length / 2)
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2
  }

  function sortBy (key) {
    if (sortKey === key) sortDir = -sortDir
    else { sortKey = key; sortDir = 1 }
  }

  async function load () {
    loading = true
    error = null
    try {
      const res = await listScores()
      rows = res.rows
    } catch (e) { error = String(e.message ?? e) } finally { loading = false }
  }

  $effect(() => { if (visible) load() })

  const COLS = [
    ['run_id', 'run', null], ['leg_id', 'leg', null],
    ['algorithm', 'algo', SCORE_INFO.algorithm], ['track', 'track', SCORE_INFO.track],
    ['medErrM', 'med err (m)', SCORE_INFO.medianErr],
    ['meanErrM', 'mean err (m)', SCORE_INFO.meanMaxErr],
    ['maxErrM', 'max err (m)', SCORE_INFO.meanMaxErr],
    ['route', 'route', SCORE_INFO.routeGuess], ['routeOK', 'route ok', SCORE_INFO.routeGuess],
    ['lockInS', 'lock-in (s)', SCORE_INFO.lockIn],
    ['stationOK', 'station', SCORE_INFO.station], ['stTimingS', 'Δt (s)', SCORE_INFO.stationTiming],
    ['ttffS', 'ttff (s)', SCORE_INFO.firstFix], ['ffErrM', 'ff err (m)', SCORE_INFO.ffErr],
  ]
</script>

<div class="panel summary">
  <div class="head">
    <h2>All scores</h2>
    <button onclick={load} disabled={loading}>{loading ? 'loading…' : 'refresh'}</button>
  </div>

  {#if error}<p class="err">{error}</p>{/if}

  {#if flatRows.length}
    <div class="stats">
      <span class="stat"><span class="k dim">legs scored</span><span class="v">{scored.length}/{flatRows.length}</span></span>
      <span class="stat"><span class="k dim">median-of-medians</span><span class="v">{num(medOfMed, 0)} m</span></span>
      <span class="stat"><span class="k dim">route accuracy</span><span class="v">{routeAcc != null ? `${Math.round(routeAcc * 100)}%` : '—'}</span></span>
      <span class="stat"><span class="k dim">station accuracy</span><span class="v">{stationAcc != null ? `${Math.round(stationAcc * 100)}%` : '—'}</span></span>
    </div>
  {/if}

  <div class="table-wrap scroll">
    <table>
      <thead>
        <tr>
          {#each COLS as [key, label, info] (key)}
            <th aria-sort={sortKey === key ? (sortDir === 1 ? 'ascending' : 'descending') : 'none'}>
              <button type="button" class="th-inner" onclick={() => sortBy(key)}>
                {label}{sortKey === key ? (sortDir === 1 ? ' ▲' : ' ▼') : ''}
              </button>
              {#if info}<Info text={info} />{/if}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each sorted as r (r.run_id + '/' + r.leg_id)}
          <tr>
            <td class="mono">{r.run_id}</td>
            <td class="mono">{r.leg_id}</td>
            <td>{r.algorithm}</td>
            <td>{r.track}</td>
            <td>{num(r.medErrM, 0)}</td>
            <td>{num(r.meanErrM, 0)}</td>
            <td>{num(r.maxErrM, 0)}</td>
            <td>{r.route ?? '—'}</td>
            <td class:ok={r.routeOK} class:bad={r.routeOK === false}>{r.routeOK == null ? '—' : r.routeOK ? 'yes' : 'no'}</td>
            <td>{num(r.lockInS, 0)}</td>
            <td class:ok={r.stationOK} class:bad={r.stationOK === false}>{r.stationOK == null ? '—' : r.stationOK ? 'hit' : 'miss'}</td>
            <td>{num(r.stTimingS, 0)}</td>
            <td>{num(r.ttffS, 0)}</td>
            <td>{num(r.ffErrM, 0)}</td>
          </tr>
        {:else}
          <tr><td colspan={COLS.length} class="dim">No score.json files found yet.</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>

<style>
  .summary { display: flex; flex-direction: column; gap: 10px; height: 100%; }
  .head { display: flex; align-items: center; gap: 8px; }
  .head h2 { flex: 1; }
  .stats { display: flex; gap: 16px; flex-wrap: wrap; }
  .stat { display: flex; flex-direction: column; gap: 2px; }
  .k { font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase; }
  .v { font-family: var(--mono); font-size: 15px; }
  .table-wrap { overflow: auto; flex: 1; }
  table { border-collapse: collapse; width: 100%; font-size: 11.5px; }
  th, td { padding: 4px 8px; text-align: left; white-space: nowrap; border-bottom: 1px solid var(--line); }
  th { position: sticky; top: 0; background: var(--panel); user-select: none; white-space: nowrap; }
  .th-inner {
    background: none; border: none; padding: 0; font: inherit; color: inherit;
    cursor: pointer; white-space: nowrap;
  }
  .th-inner:hover { border-color: transparent; }
  tbody tr:hover { background: var(--panel-2); }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
  .err { color: var(--bad); font-size: 11px; }
</style>
