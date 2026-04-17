<!--
  SidebarView — mirrors Loca-SwiftUI/Sources/Loca/Views/SidebarView.swift.
  Phase 2 scope: controls panel (New Conv, mode tabs, model picker, context picker),
  a basic conversation list, and the footer nav buttons. Folders, drag-and-drop,
  multi-select delete, and conversation rename are explicitly deferred.
-->
<script lang="ts">
  import { app, CAPABILITIES } from './app-store.svelte';

  interface Props {
    onOpenRoute?: (route: string) => void;
  }
  let { onOpenRoute }: Props = $props();

  // Load sidebar data once on mount.
  $effect(() => { void app.refresh(); });

  const contextOptions = [4096, 8192, 16384, 32768, 65536, 131072, 262144];
  function ctxLabel(n: number): string { return n >= 1024 ? `${n / 1024}K` : `${n}`; }

  const activeModel = $derived(app.activeModelName);
  const displayedConvs = $derived(app.conversations);

  function openPanel(route: string): void { onOpenRoute?.(route); }
</script>

<aside class="sidebar">
  <!-- Controls panel -->
  <div class="controls">
    <button class="new-conv" onclick={() => app.newConversation()}>
      <span class="pencil">✎</span> New Conversation
    </button>

    <div class="cap-picker">
      {#each CAPABILITIES as cap, i (cap.id)}
        <button
          class:active={app.selectedCapability === cap.id}
          onclick={() => (app.selectedCapability = cap.id)}
        >
          {cap.label}
        </button>
        {#if i < CAPABILITIES.length - 1}<span class="sep"></span>{/if}
      {/each}
    </div>

    <div class="model-picker">
      {#if app.localModels.length > 0}
        <div class="model-row">
          <span class="dot" class:live={activeModel !== null}></span>
          <select
            value={activeModel ?? app.localModels[0]?.name ?? ''}
            onchange={(e) => (app.activeModelName = (e.currentTarget as HTMLSelectElement).value)}
          >
            {#each app.localModels as m (m.name)}
              <option value={m.name}>{m.name}</option>
            {/each}
          </select>
        </div>
      {:else}
        <div class="model-empty">No local models yet.</div>
      {/if}
      <button class="link" onclick={() => openPanel('/ui/manage-models')}>
        Manage Models
      </button>
    </div>

    <div class="ctx-row">
      <label for="ctx-select">Context</label>
      <select
        id="ctx-select"
        value={app.contextWindow}
        onchange={(e) => (app.contextWindow = parseInt((e.currentTarget as HTMLSelectElement).value, 10))}
      >
        {#each contextOptions as n}
          <option value={n}>{ctxLabel(n)}</option>
        {/each}
      </select>
    </div>
  </div>

  <div class="divider"></div>

  <!-- Conversation list -->
  <div class="conv-list">
    {#if app.loading}
      <div class="hint">Loading conversations…</div>
    {:else if displayedConvs.length === 0}
      <div class="hint">No conversations yet. Start one to see it here.</div>
    {:else}
      {#each displayedConvs as conv (conv.id)}
        <button
          class="conv-row"
          class:active={app.activeConvId === conv.id}
          onclick={() => app.selectConversation(conv.id)}
        >
          <span class="conv-title">{conv.title || 'Untitled'}</span>
        </button>
      {/each}
    {/if}
  </div>

  <div class="divider"></div>

  <!-- Footer -->
  <nav class="footer">
    <button onclick={() => openPanel('/ui/glossary')}>Glossary</button>
    <button onclick={() => openPanel('/ui/preferences')}>Preferences</button>
    <button onclick={() => openPanel('/ui/philosophy')}>Philosophy</button>
  </nav>
</aside>

<style>
  .sidebar {
    display: flex;
    flex-direction: column;
    width: 260px;
    height: 100vh;
    background: var(--loca-color-surface);
    border-right: 1px solid var(--loca-color-border);
    color: var(--loca-color-text);
    font-size: 12px;
  }

  .controls {
    padding: var(--loca-space-md);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .new-conv {
    background: var(--loca-color-accent);
    color: #fff;
    border: none;
    border-radius: var(--loca-radius-sm);
    padding: 6px 10px;
    text-align: left;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .new-conv:hover { background: var(--loca-color-accent-hover); }
  .pencil { font-size: 11px; }

  .cap-picker {
    display: flex;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    overflow: hidden;
  }
  .cap-picker button {
    flex: 1;
    background: transparent;
    border: none;
    padding: 5px 4px;
    font-size: 11px;
    font-weight: 500;
    color: var(--loca-color-text-muted);
    cursor: pointer;
  }
  .cap-picker button.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-accent);
  }
  .cap-picker .sep {
    width: 1px;
    background: var(--loca-color-border);
    margin: 4px 0;
    align-self: stretch;
  }

  .model-picker { display: flex; flex-direction: column; gap: 6px; }
  .model-row {
    display: flex; align-items: center; gap: 5px;
    padding: 4px 8px;
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
  }
  .model-row select {
    flex: 1;
    background: transparent;
    border: none;
    font-size: 12px;
    color: var(--loca-color-text);
    cursor: pointer;
    appearance: none;
  }
  .dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: rgba(127, 127, 127, 0.4);
  }
  .dot.live { background: var(--loca-color-success); }
  .model-empty {
    color: var(--loca-color-text-muted);
    font-size: 11px;
    padding: 4px 2px;
  }
  .link {
    background: none;
    border: none;
    color: var(--loca-color-accent);
    font-size: 11px;
    padding: 2px 0;
    text-align: left;
    cursor: pointer;
  }

  .ctx-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    color: var(--loca-color-text-muted);
  }
  .ctx-row label { font-size: 11px; }
  .ctx-row select {
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 3px 6px;
    font-size: 11px;
    color: var(--loca-color-text);
    cursor: pointer;
  }

  .divider { height: 1px; background: var(--loca-color-border); }

  .conv-list { flex: 1; overflow-y: auto; padding: 6px 8px; }
  .hint {
    padding: 12px 8px;
    color: var(--loca-color-text-muted);
    font-size: 11px;
    line-height: 1.5;
  }
  .conv-row {
    display: block;
    width: 100%;
    background: transparent;
    border: none;
    text-align: left;
    padding: 6px 8px;
    border-radius: var(--loca-radius-sm);
    color: var(--loca-color-text);
    cursor: pointer;
    font-size: 12px;
  }
  .conv-row:hover { background: rgba(127, 127, 127, 0.1); }
  .conv-row.active { background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent); }
  .conv-title { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .footer {
    display: flex;
    gap: 6px;
    padding: 8px 12px;
    flex-wrap: wrap;
  }
  .footer button {
    background: none;
    border: none;
    color: var(--loca-color-text-muted);
    font-size: 11px;
    padding: 2px 4px;
    cursor: pointer;
  }
  .footer button:hover { color: var(--loca-color-text); }
</style>
