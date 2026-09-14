<script>
  // Start a lane runner from the GUI. The server owns the command line; this
  // only picks a lane and a set of practice legs. See web/README.md.
  import { listLegs, listJobs, startRun, cancelRun } from './api.js'

  let { jobs = [], onstarted = () => {} } = $props()

  const LANES = [
    { id: 'motion', label: 'motion', hint: 'shape_stream.py — IMU shape, stdlib only' },
    { id: 'absolute', label: 'absolute', hint: 'run_warm_gui.py — GTFS + OSM warm solver, needs .venv' },
  ]

  let open = $state(false)
  let legs = $state([])
  let practiceDir = $state('')
  let loadError = $state(null)
  let lane = $state('motion')
  let filter = $state('')
  let selected = $state(new Set())
  let notes = $state('')
  let busy = $state(false)
  let error = $state(null)

  const running = $derived(jobs.find(j => j.state === 'running'))
  const laneRunning = $derived(jobs.find(j => j.state === 'running' && j.lane === lane))
  const shown = $derived(
    filter.trim()
      ? legs.filter(l => l.leg_id.includes(filter.trim().toLowerCase()))
      : legs
  )
  const allShownPicked = $derived(shown.length > 0 && shown.every(l => selected.has(l.leg_id)))

  async function load () {
    try {
      const res = await listLegs()
      legs = res.legs
      practiceDir = res.practiceDir
      loadError = null
    } catch (e) { loadError = String(e.message ?? e) }
  }

  function toggle (legId) {
    const next = new Set(selected)
    next.has(legId) ? next.delete(legId) : next.add(legId)
    selected = next
  }

  function toggleShown () {
    const next = new Set(selected)
    for (const l of shown) allShownPicked ? next.delete(l.leg_id) : next.add(l.leg_id)
    selected = next
  }

  async function start () {
    busy = true
    error = null
    try {
      const allLegs = selected.size === legs.length && legs.length > 0
      const { job } = await startRun({
        lane,
        legs: allLegs ? [] : [...selected],
        allLegs,
        notes: notes.trim() || undefined,
      })
      onstarted(job)
    } catch (e) { error = String(e.message ?? e) } finally { busy = false }
  }

  async function cancel (jobId) {
    try { await cancelRun(jobId) } catch (e) { error = String(e.message ?? e) }
  }

  const lastLine = j => j.log?.length ? j.log[j.log.length - 1].line : ''
  const secs = j => Math.round(((j.finishedAt ?? Date.now()) - j.startedAt) / 1000)

  $effect(() => { load() })
</script>

<div class="panel launcher">
  <div class="head">
    <h2>New run</h2>
    <button class="toggle" aria-pressed={open} onclick={() => (open = !open)}>
      {open ? 'hide' : 'configure'}
    </button>
  </div>

  {#if running}
    <div class="job running-job">
      <span class="pill running">running</span>
      <span class="mono grow">{running.run_id}</span>
      <span class="dim">{secs(running)}s</span>
      <button onclick={() => cancel(running.job_id)}>stop</button>
      {#if lastLine(running)}<span class="dim tail mono">{lastLine(running)}</span>{/if}
    </div>
  {/if}

  {#if open}
    {#if loadError}<p class="err">{loadError}</p>{/if}

    <div class="row lanes">
      {#each LANES as l (l.id)}
        <button class="lane {l.id}" aria-pressed={lane === l.id} title={l.hint} onclick={() => (lane = l.id)}>
          {l.label}
        </button>
      {/each}
    </div>

    <div class="row">
      <input class="filter" placeholder="filter legs (e.g. ic830)" bind:value={filter} />
      <button onclick={toggleShown} disabled={!shown.length}>
        {allShownPicked ? 'none' : 'all'}{filter.trim() ? ' shown' : ''}
      </button>
    </div>

    <div class="legs scroll">
      {#each shown as l (l.leg_id)}
        <label class="leg" class:on={selected.has(l.leg_id)}>
          <input type="checkbox" checked={selected.has(l.leg_id)} onchange={() => toggle(l.leg_id)} />
          <span class="mono id">{l.leg_id}</span>
          <span class="dim size">{(l.sizeBytes / 1048576).toFixed(0)} MB</span>
        </label>
      {:else}
        <p class="dim">No practice legs under {practiceDir || 'datasets/practice'}.</p>
      {/each}
    </div>

    <input class="notes" placeholder="notes (shown in the run list)" bind:value={notes} />

    {#if error}<p class="err">{error}</p>{/if}

    <div class="row actions">
      <span class="dim">{selected.size}/{legs.length} legs</span>
      <button class="go" disabled={busy || !selected.size || Boolean(laneRunning)} onclick={start}>
        {laneRunning ? `${lane} lane busy` : busy ? 'starting…' : `run ${lane} lane`}
      </button>
    </div>
  {/if}

  {#if jobs.length}
    <div class="history">
      {#each jobs.slice(0, 4) as j (j.job_id)}
        {#if j.state !== 'running'}
          <div class="job">
            <span class="pill {j.state}">{j.state}</span>
            <span class="mono grow">{j.run_id}</span>
            <span class="dim">{secs(j)}s</span>
            {#if j.error}<span class="err tail">{j.error}</span>{/if}
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</div>

<style>
  .launcher { display: flex; flex-direction: column; gap: 8px; }
  .head { display: flex; align-items: center; gap: 8px; }
  .head h2 { flex: 1; }
  .toggle { font-size: 11px; padding: 2px 7px; }

  .row { display: flex; gap: 6px; align-items: center; }
  .actions { justify-content: space-between; }
  .lanes .lane { flex: 1; }
  .lane.motion[aria-pressed="true"] { color: var(--motion); border-color: var(--motion); }
  .lane.absolute[aria-pressed="true"] { color: var(--absolute); border-color: var(--absolute); }

  input {
    font: inherit; color: inherit; background: var(--panel-2);
    border: 1px solid var(--line); border-radius: 6px; padding: 4px 7px; width: 100%;
  }
  input[type="checkbox"] { width: auto; accent-color: var(--accent); }
  .filter { flex: 1; }
  .notes { font-size: 12px; }

  .legs { max-height: 190px; display: flex; flex-direction: column; gap: 1px; }
  .leg {
    display: flex; align-items: center; gap: 6px;
    padding: 2px 4px; border-radius: 4px; cursor: pointer; font-size: 11px;
  }
  .leg:hover { background: var(--panel-2); }
  .leg.on { background: var(--panel-2); }
  .id { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .size { font-size: 10px; }

  .go { font-weight: 600; }
  .err { color: var(--bad); font-size: 11px; margin: 0; }

  .history { display: flex; flex-direction: column; gap: 3px; }
  .job { display: flex; align-items: center; gap: 6px; font-size: 11px; }
  .running-job { flex-wrap: wrap; }
  .grow { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tail { width: 100%; font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pill.cancelled { color: var(--dim); }
</style>
