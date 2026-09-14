<script>
  // Small "what is this" tooltip. Hover or focus/tap the ⓘ to read it — works
  // with mouse and keyboard/touch alike. Positioned with `position: fixed` from
  // a measured rect (not CSS-relative to the button) so it always renders atop
  // scrollable panels / sticky table headers / everything else, and is clamped
  // to the viewport so it's never clipped or run off-screen.
  let { text = '' } = $props()

  let btn = $state(null)
  let open = $state(false)
  let style = $state('')

  const MARGIN = 8
  const WIDTH = 260

  function place () {
    if (!btn) return
    const r = btn.getBoundingClientRect()
    let left = r.left + r.width / 2 - WIDTH / 2
    left = Math.max(MARGIN, Math.min(left, window.innerWidth - WIDTH - MARGIN))
    const spaceAbove = r.top
    const above = spaceAbove > 120
    const top = above ? r.top - MARGIN : r.bottom + MARGIN
    style = `left:${left}px; top:${top}px; transform:translateY(${above ? '-100%' : '0'});`
  }

  function show () { place(); open = true }
  function hide () { open = false }
</script>

<svelte:window onscroll={() => open && place()} onresize={() => open && place()} />

<button
  type="button"
  class="info"
  aria-label={text}
  bind:this={btn}
  onmouseenter={show}
  onmouseleave={hide}
  onfocus={show}
  onblur={hide}
  onclick={(e) => { e.stopPropagation(); open ? hide() : show() }}
>
  <span class="dot" aria-hidden="true">ⓘ</span>
</button>

{#if open}
  <div class="tip" role="tooltip" style={style}>{text}</div>
{/if}

<style>
  .info {
    position: relative; display: inline-flex; margin-left: 3px; cursor: help;
    background: none; border: none; padding: 0; line-height: 1;
  }
  .info:hover { border-color: transparent; }
  .dot { font-size: 11px; color: var(--dim); line-height: 1; }
  .info:hover .dot, .info:focus .dot { color: var(--accent); }

  .tip {
    position: fixed;
    width: 260px;
    background: var(--panel-2);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 7px 9px;
    font-size: 11px;
    font-weight: 400;
    line-height: 1.4;
    text-transform: none;
    letter-spacing: normal;
    color: var(--fg);
    box-shadow: 0 6px 20px rgba(0, 0, 0, .35);
    z-index: 1000;
    pointer-events: none;
  }
</style>
