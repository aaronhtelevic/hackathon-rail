<script>
  let { label, value = null, open = false } = $props()
  const text = $derived(value == null ? '' : JSON.stringify(value, null, 2))
</script>

<details {open}>
  <summary>{label} <span class="dim">{value == null ? '(absent)' : `${text.length} chars`}</span></summary>
  {#if value != null}<pre class="mono scroll">{text}</pre>{/if}
</details>

<style>
  details { border-top: 1px solid var(--line); padding-top: 8px; margin-top: 8px; }
  summary { cursor: pointer; font-size: 12px; }
  pre { max-height: 320px; font-size: 11px; background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px; padding: 8px; margin: 8px 0 0; }
</style>
