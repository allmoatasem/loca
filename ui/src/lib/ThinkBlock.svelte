<!--
  ThinkBlock — collapsible reasoning trace for <think>…</think> output
  from reasoning models. Twins SwiftUI's ThinkBlockView in ChatView.swift.
-->
<script lang="ts">
  interface Props {
    text: string;
    defaultOpen?: boolean;
  }
  let { text, defaultOpen = false }: Props = $props();
  let open = $state(defaultOpen);
</script>

<div class="think">
  <button onclick={() => (open = !open)} aria-expanded={open}>
    <span class="chev" class:open>›</span>
    Thinking…
  </button>
  {#if open}
    <pre>{text}</pre>
  {/if}
</div>

<style>
  .think {
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 8px 10px;
    margin: 0 0 10px;
    font-size: 12px;
    color: var(--loca-color-text-muted);
  }
  button {
    background: none;
    border: none;
    padding: 0;
    font-size: 12px;
    font-weight: 500;
    color: var(--loca-color-text-muted);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .chev {
    display: inline-block;
    transition: transform 120ms;
    font-size: 11px;
  }
  .chev.open { transform: rotate(90deg); }
  pre {
    margin: 8px 0 0;
    white-space: pre-wrap;
    font-family: var(--loca-font-mono);
    font-size: 11px;
    line-height: 1.5;
    opacity: 0.9;
  }
</style>
