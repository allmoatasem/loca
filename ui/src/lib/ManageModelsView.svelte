<!--
  ManageModelsView — mirrors the Downloaded Models tab of
  Loca-SwiftUI/Sources/Loca/Views/SettingsView.swift (DownloadedModelsTab).

  Phase 6 scope: list + load + eject + delete for local LLM models.
  Speech-model rows and the Discover tab (HF search, hardware-aware
  recommendations, download queue) land in Phase 6b.
-->
<script lang="ts">
  import { app } from './app-store.svelte';
  import {
    deleteModel as apiDeleteModel,
    loadModel as apiLoadModel,
    fetchLocalModels,
    unloadModel as apiUnloadModel,
    type LocalModel,
  } from './api.client';

  interface Props {
    onClose?: () => void;
  }
  let { onClose }: Props = $props();

  let models = $state<LocalModel[]>([]);
  let loading = $state<boolean>(false);
  let errorMsg = $state<string | null>(null);
  let busyModel = $state<string | null>(null);       // model name currently loading/ejecting/deleting
  let confirmDelete = $state<LocalModel | null>(null);

  async function refresh(): Promise<void> {
    loading = true;
    errorMsg = null;
    try {
      models = await fetchLocalModels();
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => { void refresh(); });

  function formatSize(m: LocalModel): string {
    const gb = m.size_gb ?? (m.size_bytes ? m.size_bytes / 1_000_000_000 : null);
    if (gb == null) return '';
    return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(gb * 1000)} MB`;
  }

  function contextLabel(n: number | null | undefined): string {
    if (!n) return '';
    if (n >= 1024) return `${Math.round(n / 1024)}K ctx`;
    return `${n} ctx`;
  }

  async function load(m: LocalModel): Promise<void> {
    busyModel = m.name;
    errorMsg = null;
    try {
      await apiLoadModel(m.name, app.contextWindow);
      app.activeModelName = m.name;
      await refresh();
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      busyModel = null;
    }
  }

  async function unload(m: LocalModel): Promise<void> {
    busyModel = m.name;
    errorMsg = null;
    try {
      await apiUnloadModel();
      app.activeModelName = null;
      await refresh();
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      busyModel = null;
    }
  }

  async function confirmAndDelete(): Promise<void> {
    if (!confirmDelete) return;
    const m = confirmDelete;
    confirmDelete = null;
    busyModel = m.name;
    errorMsg = null;
    try {
      await apiDeleteModel(m.name);
      if (app.activeModelName === m.name) app.activeModelName = null;
      await refresh();
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      busyModel = null;
    }
  }
</script>

<section class="panel" aria-label="Manage Models">
  <header>
    <h2>Manage Models</h2>
    {#if onClose}
      <button class="close" aria-label="Close" onclick={onClose}>×</button>
    {/if}
  </header>

  <div class="body">
    {#if loading && models.length === 0}
      <p class="hint">Loading…</p>
    {:else if models.length === 0}
      <div class="empty">
        <div class="icon">💾</div>
        <p class="muted">No models downloaded yet.</p>
        <p class="hint">Discover + download lands in Phase 6b. For now, use the macOS app's Manage Models tab or download models manually into <code>~/loca_models/</code>.</p>
      </div>
    {:else}
      <div class="list">
        {#each models as m (m.name)}
          <div class="row">
            <span class="dot" class:live={m.is_loaded}></span>
            <div class="name-group">
              <span class="name">{m.name}</span>
              <span class="meta">
                <span class="badge" class:mlx={m.format === 'mlx'} class:gguf={m.format === 'gguf'}>
                  {m.format?.toUpperCase() ?? '?'}
                </span>
                {#if m.param_label}<span>{m.param_label}</span>{/if}
                <span>{formatSize(m)}</span>
                {#if m.context_length}<span>{contextLabel(m.context_length)}</span>{/if}
                {#if m.supports_vision}<span class="badge vision">vision</span>{/if}
              </span>
            </div>
            <div class="actions">
              {#if busyModel === m.name}
                <span class="busy">…</span>
              {:else if m.is_loaded}
                <button class="secondary" onclick={() => unload(m)} title="Unload from memory">Eject</button>
              {:else}
                <button class="primary" onclick={() => load(m)}>Load</button>
              {/if}
              <button class="danger" onclick={() => confirmDelete = m} title="Delete from disk">Delete</button>
            </div>
          </div>
        {/each}
      </div>
    {/if}

    {#if errorMsg}
      <p class="status err">{errorMsg}</p>
    {/if}
  </div>
</section>

{#if confirmDelete}
  <div class="confirm-overlay" role="presentation" onclick={(e) => {
    if (e.currentTarget === e.target) confirmDelete = null;
  }}>
    <div class="confirm">
      <h3>Delete "{confirmDelete.name}"?</h3>
      <p>Permanently deletes the file ({formatSize(confirmDelete)}). Cannot be undone.</p>
      <div class="confirm-actions">
        <button onclick={() => confirmDelete = null}>Cancel</button>
        <button class="danger" onclick={confirmAndDelete}>Delete</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .panel {
    width: 640px;
    max-height: 620px;
    display: flex;
    flex-direction: column;
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-lg);
    overflow: hidden;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
  }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px;
    border-bottom: 1px solid var(--loca-color-border);
  }
  h2 { font-size: 14px; font-weight: 600; margin: 0; }
  .close {
    width: 24px; height: 24px; border-radius: 50%;
    border: none; background: rgba(128, 128, 128, 0.1);
    color: var(--loca-color-text-muted);
    font-size: 14px; cursor: pointer;
  }
  .close:hover { background: rgba(128, 128, 128, 0.2); }

  .body {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
  }
  .empty {
    text-align: center;
    padding: 40px 20px;
  }
  .empty .icon { font-size: 28px; margin-bottom: 8px; }
  .muted { color: var(--loca-color-text-muted); font-size: 13px; margin: 0 0 8px; }
  .hint { color: var(--loca-color-text-muted); font-size: 11px; line-height: 1.5; margin: 0; }

  .list { display: flex; flex-direction: column; gap: 6px; }
  .row {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 12px;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-md);
  }
  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: rgba(127, 127, 127, 0.3);
  }
  .dot.live { background: var(--loca-color-success); }

  .name-group { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .name {
    font-size: 13px;
    color: var(--loca-color-text);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .meta {
    display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
    font-size: 11px;
    color: var(--loca-color-text-muted);
  }
  .badge {
    display: inline-flex; align-items: center;
    padding: 1px 6px; border-radius: 10px;
    font-size: 10px; font-weight: 600; line-height: 1.5;
  }
  .badge.mlx  { background: color-mix(in srgb, #8a5cf6 15%, transparent); color: #8a5cf6; }
  .badge.gguf { background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent); color: var(--loca-color-accent); }
  .badge.vision { background: color-mix(in srgb, var(--loca-color-warning) 18%, transparent); color: var(--loca-color-warning); }

  .actions { display: flex; gap: 6px; }
  .actions button {
    padding: 4px 10px;
    font-size: 11px;
    border-radius: var(--loca-radius-sm);
    cursor: pointer;
    border: 1px solid transparent;
  }
  .actions .primary {
    background: var(--loca-color-accent);
    color: #fff;
    border-color: var(--loca-color-accent);
  }
  .actions .primary:hover { background: var(--loca-color-accent-hover); }
  .actions .secondary {
    background: transparent;
    border-color: var(--loca-color-border);
    color: var(--loca-color-text);
  }
  .actions .secondary:hover { background: rgba(127, 127, 127, 0.08); }
  .actions .danger {
    background: transparent;
    border-color: var(--loca-color-border);
    color: var(--loca-color-danger);
  }
  .actions .danger:hover { background: color-mix(in srgb, var(--loca-color-danger) 10%, transparent); }
  .busy { color: var(--loca-color-text-muted); font-size: 14px; }

  .status.err {
    margin: 10px 0 0;
    padding: 8px 12px;
    font-size: 12px;
    background: color-mix(in srgb, var(--loca-color-danger) 12%, transparent);
    color: var(--loca-color-danger);
    border-radius: var(--loca-radius-sm);
  }

  .confirm-overlay {
    position: fixed; inset: 0;
    display: flex; align-items: center; justify-content: center;
    background: rgba(0, 0, 0, 0.4);
    z-index: 20;
  }
  .confirm {
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-md);
    padding: 20px;
    width: 400px;
  }
  .confirm h3 { font-size: 14px; margin: 0 0 8px; }
  .confirm p { font-size: 12px; color: var(--loca-color-text-muted); margin: 0 0 16px; }
  .confirm-actions { display: flex; justify-content: flex-end; gap: 8px; }
  .confirm-actions button {
    padding: 4px 14px;
    font-size: 12px;
    border: 1px solid var(--loca-color-border);
    background: transparent;
    color: var(--loca-color-text);
    border-radius: var(--loca-radius-sm);
    cursor: pointer;
  }
  .confirm-actions .danger {
    background: var(--loca-color-danger);
    color: #fff;
    border-color: var(--loca-color-danger);
  }
  code { font-family: var(--loca-font-mono); font-size: 0.9em; background: rgba(127,127,127,0.12); padding: 1px 4px; border-radius: 3px; }
</style>
