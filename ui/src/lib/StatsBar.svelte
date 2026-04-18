<!--
  StatsBar — mirrors Loca-SwiftUI/Sources/Loca/Views/ChatView.swift's
  GenerationStatsBar. Shows the last-turn telemetry: model name, TTFT,
  tokens/sec, total time, prompt + completion tokens, context-window
  usage %, and whether Search or Memory was involved.
-->
<script lang="ts">
  interface TurnStats {
    model: string;
    promptTokens: number;
    completionTokens: number;
    ttftMs: number;
    totalMs: number;
    tokensPerSec: number;
    searchTriggered: boolean;
    memoryInjected: boolean;
  }

  interface Props {
    stats: TurnStats | null;
    contextWindow: number;
  }
  let { stats, contextWindow }: Props = $props();

  const basename = $derived.by(() => {
    if (!stats) return '';
    const parts = stats.model.split('/');
    return parts[parts.length - 1] || stats.model;
  });
  const totalTokens = $derived(stats ? stats.promptTokens + stats.completionTokens : 0);
  const usagePct = $derived.by(() => {
    if (!stats || contextWindow <= 0) return 0;
    return Math.round((totalTokens / contextWindow) * 100);
  });
  const truncated = $derived(usagePct >= 95);

  function fmt(ms: number, decimals = 1): string {
    return (ms / 1000).toFixed(decimals) + 's';
  }
</script>

{#if stats}
  <div class="bar" role="note">
    <span class="model">{basename}</span>
    {#if stats.ttftMs > 0}
      <span class="sep">·</span>
      <span>TTFT {fmt(stats.ttftMs, 1)}</span>
    {/if}
    {#if stats.tokensPerSec > 0}
      <span class="sep">·</span>
      <span>{stats.tokensPerSec.toFixed(0)} tok/s</span>
    {/if}
    {#if stats.totalMs > 0}
      <span class="sep">·</span>
      <span>{fmt(stats.totalMs, 1)} total</span>
    {/if}
    {#if totalTokens > 0}
      <span class="sep">·</span>
      <span>P:{stats.promptTokens} + C:{stats.completionTokens}</span>
      <span class="sep">·</span>
      <span class:warn={usagePct > 80}>
        {totalTokens} / {Math.round(contextWindow / 1024)}K ({usagePct}%)
      </span>
    {/if}
    {#if stats.searchTriggered}
      <span class="sep">·</span>
      <span class="badge search" title="Web search was triggered">🔍</span>
    {/if}
    {#if stats.memoryInjected}
      <span class="sep">·</span>
      <span class="badge memory" title="Memories were injected">🧠</span>
    {/if}
    {#if truncated}
      <span class="sep">·</span>
      <span class="warn">⚠ truncated</span>
    {/if}
  </div>
{/if}

<style>
  .bar {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 4px;
    padding: 4px 40px;
    font-family: var(--loca-font-mono);
    font-size: 11px;
    color: var(--loca-color-text-muted);
    border-bottom: 1px solid var(--loca-color-border);
    background: var(--loca-color-bg);
  }
  .model { font-weight: 500; color: var(--loca-color-text); }
  .sep { opacity: 0.5; }
  .badge.search { color: var(--loca-color-accent); }
  .badge.memory { color: #8a5cf6; }
  .warn { color: var(--loca-color-warning); }
</style>
