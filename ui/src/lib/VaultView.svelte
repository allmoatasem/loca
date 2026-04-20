<!--
  VaultView — "Obsidian Watcher" surface. Registered vaults sync in
  the background via `/api/obsidian/*`; the bottom tabs render the
  live analysis for whichever watched vault is selected.

  The per-project "Sync Vault" button has been retired: Research Partner
  projects with `obsidian_source=true` read straight from the shared
  `vault_notes` index populated by this watcher, with no re-ingestion.
-->
<script lang="ts">
  import {
    detectVaults,
    fetchVaultAnalysis,
    listWatchedVaults,
    registerVault,
    scanVaultNow,
    searchVault,
    unregisterVault,
    type DetectedVault,
    type VaultAnalysis,
    type VaultSearchHit,
    type WatchedVault,
  } from './api.client';

  interface Props { onClose?: () => void }
  let { onClose }: Props = $props();

  type Tab = 'overview' | 'orphans' | 'broken' | 'suggestions' | 'search';
  let tab = $state<Tab>('overview');

  let detected = $state<DetectedVault[]>([]);
  let watched = $state<WatchedVault[]>([]);
  let selectedPath = $state<string>('');
  let manualPath   = $state<string>('');
  let analysis = $state<VaultAnalysis | null>(null);
  let scanning = $state<boolean>(false);
  let analysing = $state<boolean>(false);
  let errorMsg = $state<string | null>(null);

  let searchQuery = $state<string>('');
  let searchHits  = $state<VaultSearchHit[]>([]);
  let searching   = $state<boolean>(false);

  // Keep a live copy of the watched list so last_scan_at / busy flags
  // update while a background scan is in flight.
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  $effect(() => {
    (async () => {
      try {
        const [dets, ws] = await Promise.all([
          detectVaults(), listWatchedVaults(),
        ]);
        detected = dets;
        watched = ws;
        if (ws.length > 0 && !selectedPath) {
          selectedPath = ws[0].path;
          await analyse(selectedPath);
        } else if (dets.length > 0 && !selectedPath) {
          // No vaults watched yet — surface the first detected path
          // so the user can click "Watch" in a single step.
          manualPath = dets[0].path;
        }
      } catch (e) {
        errorMsg = e instanceof Error ? e.message : String(e);
      }
    })();
    pollTimer = setInterval(refreshWatched, 3000);
    return () => {
      if (pollTimer) clearInterval(pollTimer);
    };
  });

  async function refreshWatched(): Promise<void> {
    try {
      watched = await listWatchedVaults();
    } catch { /* transient network — silently retry next tick */ }
  }

  async function analyse(path: string): Promise<void> {
    if (!path) return;
    analysing = true;
    errorMsg = null;
    try {
      analysis = await fetchVaultAnalysis(path);
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
      analysis = null;
    } finally {
      analysing = false;
    }
  }

  async function watchPath(): Promise<void> {
    const path = manualPath.trim() || (detected[0]?.path ?? '');
    if (!path) { errorMsg = 'Pick a detected vault or paste a path first.'; return; }
    scanning = true;
    errorMsg = null;
    try {
      await registerVault(path);
      selectedPath = path;
      manualPath = '';
      await refreshWatched();
      // First scan is kicked off server-side; poll briefly then analyse.
      setTimeout(async () => {
        await refreshWatched();
        await analyse(path);
      }, 1500);
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      scanning = false;
    }
  }

  async function scanNow(path: string): Promise<void> {
    scanning = true;
    errorMsg = null;
    try {
      await scanVaultNow(path);
      await refreshWatched();
      if (path === selectedPath) await analyse(path);
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    } finally {
      scanning = false;
    }
  }

  async function removeWatch(path: string): Promise<void> {
    try {
      await unregisterVault(path);
      if (selectedPath === path) {
        selectedPath = '';
        analysis = null;
      }
      await refreshWatched();
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
    }
  }

  function relativeTime(ts: number | null): string {
    if (!ts) return 'never scanned';
    const secs = Math.max(1, Math.floor(Date.now() / 1000 - ts));
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    return `${Math.floor(secs / 86400)}d ago`;
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

<section class="panel" aria-label="Obsidian Watcher">
  <header>
    <div class="title">📚 <span>Obsidian Watcher</span></div>
    <div class="controls">
      {#if watched.length > 0}
        <select
          value={selectedPath}
          onchange={(e) => {
            const v = (e.currentTarget as HTMLSelectElement).value;
            selectedPath = v;
            if (v) void analyse(v);
          }}
        >
          {#each watched as v (v.path)}
            <option value={v.path}>{v.name}{v.busy ? ' (syncing…)' : ''}</option>
          {/each}
        </select>
      {/if}
      {#if onClose}
        <button class="close" aria-label="Close" onclick={onClose}>×</button>
      {/if}
    </div>
  </header>

  {#if errorMsg}
    <p class="status err">{errorMsg}</p>
  {/if}

  <!-- Register / watched list -->
  <div class="watcher-bar">
    {#if watched.length === 0}
      <div class="empty-inline">
        <span class="hint">No vaults watched yet.</span>
        {#if detected.length > 0}
          <select
            value={manualPath || detected[0].path}
            onchange={(e) => manualPath = (e.currentTarget as HTMLSelectElement).value}
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
          onclick={watchPath}
          disabled={scanning || (!manualPath.trim() && detected.length === 0)}
        >
          {scanning ? 'Registering…' : 'Watch this vault'}
        </button>
      </div>
    {:else}
      <ul class="watched-list">
        {#each watched as v (v.path)}
          <li class:active={v.path === selectedPath}>
            <button class="name-btn" onclick={() => { selectedPath = v.path; void analyse(v.path); }}>
              <span class="name">{v.name}</span>
              <span class="hint">
                {v.busy ? 'syncing…' : relativeTime(v.last_scan_at)}
                {#if v.last_stats?.total != null}· {v.last_stats.total} notes{/if}
              </span>
            </button>
            <button class="row-btn" onclick={() => scanNow(v.path)} disabled={v.busy || scanning}>Scan now</button>
            <button class="row-btn danger" onclick={() => removeWatch(v.path)}>Remove</button>
          </li>
        {/each}
      </ul>
      <div class="add-row">
        {#if detected.filter((d) => !watched.some((w) => w.path === d.path)).length > 0}
          <select
            value={manualPath}
            onchange={(e) => manualPath = (e.currentTarget as HTMLSelectElement).value}
          >
            <option value="">Add another detected vault…</option>
            {#each detected.filter((d) => !watched.some((w) => w.path === d.path)) as v (v.path)}
              <option value={v.path}>{v.name}</option>
            {/each}
          </select>
        {:else}
          <input
            type="text"
            placeholder="/path/to/another/vault"
            bind:value={manualPath}
          />
        {/if}
        <button
          onclick={watchPath}
          disabled={scanning || !manualPath.trim()}
        >
          {scanning ? 'Adding…' : 'Watch'}
        </button>
      </div>
    {/if}
  </div>

  {#if !selectedPath && watched.length === 0}
    <div class="empty">
      <div class="icon">📂</div>
      <p>Register a vault above to start watching.</p>
      <p class="hint">Loca keeps the index fresh in the background — read-only, your files are never modified.</p>
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
  .controls select {
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

  .watcher-bar {
    padding: 10px 16px;
    border-bottom: 1px solid var(--loca-color-border);
    background: color-mix(in srgb, var(--loca-color-surface) 40%, transparent);
  }
  .empty-inline {
    display: flex; align-items: center; gap: 8px;
    flex-wrap: wrap;
  }
  .empty-inline select,
  .empty-inline input[type='text'],
  .add-row select,
  .add-row input[type='text'] {
    flex: 1;
    min-width: 160px;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 4px 8px;
    font-size: 12px;
    color: var(--loca-color-text);
  }
  .empty-inline button, .add-row button {
    padding: 4px 12px;
    font-size: 12px;
    background: var(--loca-color-accent);
    color: #fff;
    border: none;
    border-radius: var(--loca-radius-sm);
    cursor: pointer;
  }
  .empty-inline button:disabled, .add-row button:disabled {
    opacity: 0.4; cursor: not-allowed;
  }

  .watched-list {
    list-style: none;
    padding: 0;
    margin: 0 0 8px 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .watched-list li {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 8px;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
  }
  .watched-list li.active {
    border-color: var(--loca-color-accent);
  }
  .name-btn {
    flex: 1;
    display: flex; flex-direction: column; align-items: flex-start;
    background: none; border: none;
    padding: 2px 4px;
    cursor: pointer;
    color: var(--loca-color-text);
    text-align: left;
  }
  .name-btn .name { font-size: 12px; font-weight: 500; }
  .name-btn .hint { font-size: 11px; color: var(--loca-color-text-muted); }
  .row-btn {
    padding: 3px 10px;
    font-size: 11px;
    background: var(--loca-color-surface);
    color: var(--loca-color-text);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    cursor: pointer;
  }
  .row-btn:hover { background: var(--loca-color-border); }
  .row-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .row-btn.danger { color: var(--loca-color-danger); }

  .add-row {
    display: flex; align-items: center; gap: 8px;
  }
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
