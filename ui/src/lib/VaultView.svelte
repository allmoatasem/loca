<!--
  VaultView — Svelte port of Loca-SwiftUI/Sources/Loca/Views/VaultView.swift.

  Phase 6-c scope: vault detection + scan + 5-tab analysis surface
  (Overview, Orphans, Broken Links, Suggestions, Search). Identical
  endpoints the Swift app uses, so scan state is shared across UIs.

  The upcoming Knowledge-Memory Link (Tier 2 roadmap item 6) reimagines
  this panel with a second "Link" tab that reindexes a vault into
  memory via the knowledge-import pipeline. This PR only ports the
  existing Analyser surface; the Link tab lands with the wider redesign.
-->
<script lang="ts">
  import {
    detectVaults,
    fetchVaultAnalysis,
    scanVault,
    searchVault,
    type DetectedVault,
    type VaultAnalysis,
    type VaultSearchHit,
  } from './api.client';

  interface Props { onClose?: () => void }
  let { onClose }: Props = $props();

  type Tab = 'overview' | 'orphans' | 'broken' | 'suggestions' | 'search';
  let tab = $state<Tab>('overview');

  let detected = $state<DetectedVault[]>([]);
  let selectedPath = $state<string>('');
  let manualPath   = $state<string>('');
  let analysis = $state<VaultAnalysis | null>(null);
  let scanning = $state<boolean>(false);
  let analysing = $state<boolean>(false);
  let errorMsg = $state<string | null>(null);

  let searchQuery = $state<string>('');
  let searchHits  = $state<VaultSearchHit[]>([]);
  let searching   = $state<boolean>(false);

  // Detect on mount; remember the first one so users land in a ready state.
  $effect(() => {
    (async () => {
      try {
        detected = await detectVaults();
        if (detected.length > 0 && !selectedPath) {
          selectedPath = detected[0].path;
          await analyse(selectedPath);
        }
      } catch (e) {
        errorMsg = e instanceof Error ? e.message : String(e);
      }
    })();
  });

  async function analyse(path: string): Promise<void> {
    if (!path) return;
    analysing = true;
    errorMsg = null;
    try {
      analysis = await fetchVaultAnalysis(path);
    } catch (e) {
      // Not-scanned yet → backend returns empty stats; treat as "scan needed"
      errorMsg = e instanceof Error ? e.message : String(e);
      analysis = null;
    } finally {
      analysing = false;
    }
  }

  async function runScan(): Promise<void> {
    const path = selectedPath || manualPath.trim();
    if (!path) { errorMsg = 'Enter or select a vault path first.'; return; }
    scanning = true;
    errorMsg = null;
    try {
      await scanVault(path);
      selectedPath = path;
      await analyse(path);
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      scanning = false;
    }
  }

  let searchTimer = $state<ReturnType<typeof setTimeout> | null>(null);
  function onSearchInput(v: string): void {
    searchQuery = v;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 250);
  }
  async function runSearch(): Promise<void> {
    const q = searchQuery.trim();
    if (!q || !selectedPath) { searchHits = []; return; }
    searching = true;
    try {
      searchHits = await searchVault(selectedPath, q, 30);
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      searching = false;
    }
  }

  const stats = $derived(analysis?.stats ?? null);
</script>

<section class="panel" aria-label="Vault Analyser">
  <header>
    <div class="title">📚 <span>Vault Analyser</span></div>
    <div class="controls">
      {#if detected.length > 0}
        <select
          value={selectedPath}
          onchange={(e) => {
            const v = (e.currentTarget as HTMLSelectElement).value;
            selectedPath = v;
            if (v) void analyse(v);
          }}
        >
          {#each detected as v (v.path)}
            <option value={v.path}>{v.name}</option>
          {/each}
        </select>
      {:else}
        <input
          type="text"
          placeholder="/path/to/vault"
          bind:value={manualPath}
        />
      {/if}
      <button
        onclick={runScan}
        disabled={scanning || (!selectedPath && !manualPath.trim())}
      >
        {scanning ? 'Scanning…' : 'Scan'}
      </button>
      {#if onClose}
        <button class="close" aria-label="Close" onclick={onClose}>×</button>
      {/if}
    </div>
  </header>

  {#if errorMsg}
    <p class="status err">{errorMsg}</p>
  {/if}

  {#if !selectedPath && detected.length === 0}
    <div class="empty">
      <div class="icon">📂</div>
      <p>Paste an Obsidian vault path above and press <strong>Scan</strong>.</p>
      <p class="hint">The scan is read-only — your files are not modified.</p>
    </div>
  {:else if analysing}
    <p class="hint loading">Analysing vault…</p>
  {:else if !analysis}
    <div class="empty">
      <div class="icon">📝</div>
      <p>Vault not indexed yet.</p>
      <p class="hint">Click <strong>Scan</strong> above to build the index.</p>
    </div>
  {:else}
    <nav class="tabs">
      <button class:active={tab === 'overview'}    onclick={() => tab = 'overview'}>Overview</button>
      <button class:active={tab === 'orphans'}     onclick={() => tab = 'orphans'}>Orphans ({analysis.orphans.length})</button>
      <button class:active={tab === 'broken'}      onclick={() => tab = 'broken'}>Broken ({analysis.broken_links.length})</button>
      <button class:active={tab === 'suggestions'} onclick={() => tab = 'suggestions'}>Suggestions ({analysis.link_suggestions.length})</button>
      <button class:active={tab === 'search'}      onclick={() => tab = 'search'}>Search</button>
    </nav>

    <div class="body">
      {#if tab === 'overview' && stats}
        <div class="stat-grid">
          <div class="stat"><span class="n">{stats.note_count}</span><span>notes</span></div>
          <div class="stat"><span class="n">{stats.link_count}</span><span>links</span></div>
          <div class="stat"><span class="n">{stats.total_words.toLocaleString()}</span><span>words</span></div>
          <div class="stat"><span class="n">{stats.tag_count}</span><span>tags</span></div>
          <div class="stat"><span class="n">{stats.folder_count}</span><span>folders</span></div>
          <div class="stat"><span class="n">{stats.daily_note_count}</span><span>daily notes</span></div>
          <div class="stat"><span class="n">{stats.open_tasks}</span><span>open tasks</span></div>
          <div class="stat"><span class="n">{stats.done_tasks}</span><span>done tasks</span></div>
        </div>

        {#if stats.top_tags.length > 0}
          <h4>Top tags</h4>
          <div class="tags">
            {#each stats.top_tags as t (t.tag)}
              <span class="tag">#{t.tag} <em>{t.count}</em></span>
            {/each}
          </div>
        {/if}
      {:else if tab === 'orphans'}
        {#if analysis.orphans.length === 0}
          <p class="hint">No orphan notes — every note is linked from somewhere.</p>
        {:else}
          <ul class="list">
            {#each analysis.orphans as n (n.rel_path)}
              <li><span class="path">{n.rel_path}</span><span class="title">{n.title}</span></li>
            {/each}
          </ul>
        {/if}
      {:else if tab === 'broken'}
        {#if analysis.broken_links.length === 0}
          <p class="hint">No broken links.</p>
        {:else}
          <ul class="list">
            {#each analysis.broken_links as b, i (b.source + b.target + i)}
              <li><span class="path">{b.source}</span> ➜ <span class="missing">{b.target}</span></li>
            {/each}
          </ul>
        {/if}
      {:else if tab === 'suggestions'}
        {#if analysis.link_suggestions.length === 0}
          <p class="hint">No link suggestions.</p>
        {:else}
          <ul class="list">
            {#each analysis.link_suggestions as s, i (s.source + s.target + i)}
              <li>
                <span class="path">{s.source}</span> ↔ <span class="path">{s.target}</span>
                <em class="score">{(s.score * 100).toFixed(0)}%</em>
              </li>
            {/each}
          </ul>
        {/if}
      {:else if tab === 'search'}
        <input
          class="search"
          type="text"
          placeholder="Semantic search across vault…"
          value={searchQuery}
          oninput={(e) => onSearchInput((e.currentTarget as HTMLInputElement).value)}
        />
        {#if searching}
          <p class="hint">Searching…</p>
        {:else if searchQuery.trim() && searchHits.length === 0}
          <p class="hint">No matches.</p>
        {:else if searchHits.length > 0}
          <ul class="list">
            {#each searchHits as h (h.rel_path)}
              <li>
                <div class="hit-title">{h.title}</div>
                <div class="path">{h.rel_path}</div>
                {#if h.snippet}<div class="snippet">{h.snippet}</div>{/if}
              </li>
            {/each}
          </ul>
        {/if}
      {/if}
    </div>
  {/if}
</section>

<style>
  .panel {
    width: 720px;
    max-height: 640px;
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
    padding: 12px 16px;
    gap: 12px;
    border-bottom: 1px solid var(--loca-color-border);
  }
  .title {
    display: inline-flex; align-items: center; gap: 8px;
    font-size: 14px; font-weight: 600;
  }
  .controls { display: flex; align-items: center; gap: 8px; }
  .controls select, .controls input[type='text'] {
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 4px 8px;
    font-size: 12px;
    color: var(--loca-color-text);
    max-width: 220px;
  }
  .controls button {
    padding: 4px 12px;
    font-size: 12px;
    background: var(--loca-color-accent);
    color: #fff;
    border: none;
    border-radius: var(--loca-radius-sm);
    cursor: pointer;
  }
  .controls button:disabled { opacity: 0.4; cursor: not-allowed; }
  .close {
    width: 24px; height: 24px;
    background: rgba(128, 128, 128, 0.1) !important;
    color: var(--loca-color-text-muted) !important;
    padding: 0 !important;
    border-radius: 50% !important;
  }

  .tabs { display: flex; gap: 4px; padding: 8px 16px; border-bottom: 1px solid var(--loca-color-border); }
  .tabs button {
    background: none;
    border: none;
    padding: 4px 10px;
    font-size: 11px;
    color: var(--loca-color-text-muted);
    border-radius: var(--loca-radius-sm);
    cursor: pointer;
  }
  .tabs button:hover { color: var(--loca-color-text); }
  .tabs button.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-accent);
    font-weight: 500;
  }

  .body { flex: 1; overflow-y: auto; padding: 16px; }

  .empty {
    display: flex; flex-direction: column; align-items: center;
    padding: 40px 20px;
    text-align: center;
    gap: 4px;
  }
  .empty .icon { font-size: 28px; }
  .empty p { margin: 4px 0; color: var(--loca-color-text); font-size: 13px; }
  .hint { color: var(--loca-color-text-muted); font-size: 12px; line-height: 1.5; margin: 4px 0; }
  .hint.loading { padding: 40px; text-align: center; }
  .status.err {
    margin: 10px 16px 0;
    padding: 8px 12px;
    font-size: 12px;
    background: color-mix(in srgb, var(--loca-color-danger) 12%, transparent);
    color: var(--loca-color-danger);
    border-radius: var(--loca-radius-sm);
  }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    margin-bottom: 16px;
  }
  .stat {
    display: flex; flex-direction: column; align-items: center;
    padding: 10px 6px;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
  }
  .stat .n { font-size: 18px; font-weight: 600; color: var(--loca-color-text); }
  .stat span:last-child { font-size: 10px; color: var(--loca-color-text-muted); text-transform: uppercase; letter-spacing: 0.5px; }

  h4 { font-size: 12px; font-weight: 600; margin: 16px 0 6px; color: var(--loca-color-text); }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; }
  .tag {
    background: color-mix(in srgb, var(--loca-color-accent) 10%, transparent);
    color: var(--loca-color-accent);
    font-family: var(--loca-font-mono);
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
  }
  .tag em { font-style: normal; opacity: 0.7; margin-left: 4px; }

  .list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 4px; }
  .list li {
    padding: 6px 8px;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    font-size: 12px;
  }
  .path { font-family: var(--loca-font-mono); color: var(--loca-color-text-muted); font-size: 11px; }
  .title { display: block; font-size: 12px; color: var(--loca-color-text); margin-top: 2px; }
  .missing { color: var(--loca-color-danger); font-family: var(--loca-font-mono); }
  .score { font-style: normal; font-size: 10px; color: var(--loca-color-text-muted); margin-left: 6px; }
  .hit-title { font-size: 12px; color: var(--loca-color-text); }
  .snippet { font-size: 11px; color: var(--loca-color-text-muted); margin-top: 2px; line-height: 1.4; }

  .search {
    width: 100%;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 6px 10px;
    font-size: 12px;
    margin-bottom: 10px;
    color: var(--loca-color-text);
  }
</style>
