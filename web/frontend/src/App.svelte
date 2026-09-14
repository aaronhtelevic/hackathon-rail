<script>
  import { getConfig, listRuns, getRun, getLeg, getEvents, fileUrl, listJobs, watchChanges } from './lib/api.js'
  import RunLauncher from './lib/RunLauncher.svelte'
  import LaneTimeline from './lib/LaneTimeline.svelte'
  import AnchorMap from './lib/AnchorMap.svelte'
  import EventLog from './lib/EventLog.svelte'
  import ScorePanel from './lib/ScorePanel.svelte'
  import JsonBox from './lib/JsonBox.svelte'

  let config = $state(null)
  let runs = $state([])
  let run = $state(null)            // full run detail (legs + status)
  let leg = $state(null)            // full leg detail (shape/anchors/score)
  let selectedRun = $state(null)
  let selectedLeg = $state(null)
  let events = $state([])
  let cursor = 0
  let connState = $state('connecting')
  let autoSelect = $state(true)     // follow the newest run
  let jobs = $state([])             // lane runners started from the GUI
  let error = $state(null)

  const LANE_OF = { motion: 'motion', absolute: 'absolute', joint: 'joint' }

  async function refreshRuns () {
    const { runs: list } = await listRuns()
    runs = list
    if (autoSelect && list.length && (!selectedRun || !list.some(r => r.run_id === selectedRun))) {
      await selectRun(list[0].run_id)
    }
  }

  async function refreshRun () {
    if (!selectedRun) return
    run = await getRun(selectedRun)
    if (!selectedLeg || !run.legs.some(l => l.leg_id === selectedLeg)) {
      if (run.legs.length) await selectLeg(run.legs[0].leg_id)
      else { selectedLeg = null; leg = null; events = []; cursor = 0 }
    }
  }

  async function refreshLeg () {
    if (!selectedRun || !selectedLeg) return
    leg = await getLeg(selectedRun, selectedLeg)
  }

  async function pullEvents (reset = false) {
    if (!selectedRun || !selectedLeg) return
    if (reset) { cursor = 0; events = [] }
    const res = await getEvents(selectedRun, selectedLeg, cursor)
    if (res.events.length) events = [...events, ...res.events].slice(-2000)
    cursor = res.cursor
  }

  async function selectRun (runId) {
    selectedRun = runId
    selectedLeg = null
    await refreshRun()
  }

  async function selectLeg (legId) {
    selectedLeg = legId
    await Promise.all([refreshLeg(), pullEvents(true)])
  }

  // Map a changed run-dir relative path onto the refreshes it invalidates.
  async function onChange (paths) {
    let needRuns = false, needRun = false, needLeg = false, needEvents = false
    for (const p of paths) {
      const [runId, legId, name] = p.split('/')
      if (runId !== selectedRun) { needRuns = true; continue }
      if (!legId || name === undefined) { needRun = true; continue }
      needRun = true
      if (legId === selectedLeg) {
        if (name === 'events.ndjson') needEvents = true
        else needLeg = true
      }
    }
    try {
      if (needRuns) await refreshRuns()
      if (needRun) await refreshRun()
      if (needLeg) await refreshLeg()
      if (needEvents) await pullEvents()
      error = null
    } catch (e) { error = String(e.message ?? e) }
  }

  // A just-started run has no directory yet — jump to it as soon as it appears.
  async function onStarted (job) {
    autoSelect = false
    selectedRun = job.run_id
    selectedLeg = null
    await refreshRuns().catch(() => {})
    await refreshRun().catch(() => {})
  }

  $effect(() => {
    let stop
    ;(async () => {
      try {
        config = await getConfig()
        await Promise.all([refreshRuns(), listJobs().then(r => (jobs = r.jobs))])
        error = null
      } catch (e) { error = String(e.message ?? e) }
      stop = watchChanges(
        p => { onChange(p).catch(e => (error = String(e))) },
        s => (connState = s),
        j => (jobs = j),
      )
    })()
    return () => stop?.()
  })

  const legStatus = l => l?.status?.state ?? (l?.files?.['score.json'] ? 'done' : 'idle')
  const lane = l => LANE_OF[l?.status?.lane] ?? ''
  const ago = ms => {
    if (!ms) return ''
    const s = Math.round((Date.now() - ms) / 1000)
    if (s < 60) return `${s}s ago`
    if (s < 3600) return `${Math.round(s / 60)}m ago`
    return `${Math.round(s / 3600)}h ago`
  }
</script>

<header>
  <h1>Rail run viewer</h1>
  <span class="pill {connState === 'live' ? 'running' : ''}">{connState}</span>
  <button aria-pressed={autoSelect} onclick={() => (autoSelect = !autoSelect)}>follow newest run</button>
  <span class="dim mono path">{config?.runsDir ?? ''}</span>
</header>

{#if error}<div class="banner">{error}</div>{/if}

<main>
  <aside class="scroll">
    <RunLauncher {jobs} onstarted={onStarted} />

    <div class="panel picker">
      <h2>Runs</h2>
      {#each runs as r (r.run_id)}
        <button class="item" aria-pressed={r.run_id === selectedRun} onclick={() => selectRun(r.run_id)}>
          <span class="name mono">{r.run_id}</span>
          <span class="meta dim">{r.meta?.algorithm ?? '—'} · {r.nLegs} legs · {ago(r.mtimeMs)}</span>
        </button>
      {:else}
        <p class="dim">No runs yet. Write one into the runs dir — see web/README.md.</p>
      {/each}

      {#if run}
        <h2 class="legs-h">Legs</h2>
        {#each run.legs as l (l.leg_id)}
          <button class="item" aria-pressed={l.leg_id === selectedLeg} onclick={() => selectLeg(l.leg_id)}>
            <span class="name mono">{l.leg_id}</span>
            <span class="meta">
              <span class="pill {legStatus(l)}">{legStatus(l)}</span>
              {#if lane(l)}<span class="pill {lane(l)}">{lane(l)}</span>{/if}
              {#if l.status?.pct != null}<span class="dim">{Math.round(l.status.pct * 100)}%</span>{/if}
            </span>
          </button>
        {/each}
      {/if}
    </div>
  </aside>

  <section class="detail scroll">
    {#if !selectedLeg}
      <div class="panel snap-section"><p class="dim">Pick a leg.</p></div>
    {:else}
      <div class="panel snap-section">
        <h2>{selectedLeg} — {run?.meta?.algorithm ?? 'algorithm'}</h2>
        <p class="dim status-line">
          {leg?.status?.stage ?? 'no status.json'}
          {#if leg?.status?.message} · {leg.status.message}{/if}
          {#if leg?.status?.updated_at} · {ago(leg.status.updated_at)}{/if}
        </p>
        <ScorePanel score={leg?.score} />
      </div>

      <div class="snap-section geometry-group">
        <div class="panel">
          <h2>Geometry</h2>
          <AnchorMap anchors={leg?.anchors} hydrated={leg?.hydrated} shape={leg?.shape} legId={selectedLeg} />
        </div>

        <div class="panel">
          <h2>Lane timeline</h2>
          <LaneTimeline shape={leg?.shape} anchors={leg?.anchors} />
        </div>
      </div>

      <div class="panel snap-section">
        <h2>Events</h2>
        <EventLog {events} />
        <p class="dim files">
          {#each Object.entries(run?.legs?.find(l => l.leg_id === selectedLeg)?.files ?? {}) as [name, f] (name)}
            <a href={fileUrl(selectedRun, selectedLeg, name)} target="_blank" rel="noreferrer">{name}</a>
            <span class="dim">({(f.size / 1024).toFixed(1)} kB)</span>
          {/each}
        </p>
      </div>

      <div class="panel snap-section">
        <h2>Raw contracts</h2>
        <JsonBox label="shape.json" value={leg?.shape} />
        <JsonBox label="anchors.json" value={leg?.anchors} />
        <JsonBox label="hydrated.json" value={leg?.hydrated} />
        <JsonBox label="status.json" value={leg?.status} />
      </div>
    {/if}
  </section>
</main>

<style>
  header {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 14px; border-bottom: 1px solid var(--line); background: var(--panel);
    position: sticky; top: 0; z-index: 2;
  }
  header h1 { font-size: 14px; }
  .path { margin-left: auto; font-size: 11px; }
  .banner { background: #3a1e22; border-bottom: 1px solid var(--bad); color: var(--bad); padding: 6px 14px; font-size: 12px; }

  main { display: grid; grid-template-columns: 300px 1fr; gap: 12px; padding: 12px; height: calc(100vh - 46px); }
  aside { display: flex; flex-direction: column; gap: 12px; }
  .picker { display: flex; flex-direction: column; gap: 4px; }
  .legs-h { margin-top: 14px; }
  .item {
    display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
    width: 100%; text-align: left; background: transparent; border-color: transparent; padding: 5px 7px;
  }
  .item:hover { background: var(--panel-2); }
  .item[aria-pressed="true"] { background: var(--panel-2); border-color: var(--accent); }
  .name { font-size: 12px; }
  .meta { font-size: 11px; display: flex; gap: 5px; align-items: center; }

  .detail { display: flex; flex-direction: column; gap: 12px; scroll-snap-type: y proximity; }
  .snap-section { scroll-snap-align: start; scroll-margin-top: 0; }
  .geometry-group { display: flex; flex-direction: column; gap: 12px; }
  .status-line { margin: 4px 0 10px; font-size: 12px; }
  .files { font-size: 11px; margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; }

  @media (max-width: 900px) {
    main { grid-template-columns: 1fr; height: auto; }
  }
</style>
