<!--
  DiscoverTab — HF search + download for the Manage Models panel.

  Phase 6-b scope: format toggle (GGUF / MLX), HF search, per-result
  download with a file picker for GGUF repos (which usually ship
  multiple quants). Active downloads render with a live progress bar
  and cancel button, consuming the SSE stream at
  /api/models/download/{id}/progress.

  Deferred: hardware-aware recommendations (llmfit fit-score UI),
  notes popovers, pause/resume (cancel-only for now).
-->
<script lang="ts">
  import {
    cancelDownload,
    listRepoFiles,
    searchHF,
    startDownload,
    type HFSearchHit,
    type RepoFile,
  } from './api.client';

  interface Props { onModelsChanged?: () => void }
  let { onModelsChanged }: Props = $props();

  type Format = 'gguf' | 'mlx';
  let format = $state<Format>('gguf');
  let query  = $state<string>('');
  let results = $state<HFSearchHit[]>([]);
  let searching = $state<boolean>(false);
  let searchError = $state<string | null>(null);

  // File-picker state — opened when the user clicks a GGUF result.
  let pickerFor = $state<HFSearchHit | null>(null);
  let pickerFiles = $state<RepoFile[]>([]);
  let pickerLoading = $state<boolean>(false);

  // Active downloads. Each entry owns an EventSource that streams
  // progress from the backend; we drop it when done / errored / cancelled.
  interface ActiveDownload {
    id: string;
    label: string;          // repo_id / filename for humans
    percent: number;
    speedMbps: number;
    etaS: number;
    error: string | null;
    source: EventSource;
  }
  let downloads = $state<ActiveDownload[]>([]);

  async function runSearch(): Promise<void> {
    const q = query.trim();
    if (!q) { results = []; return; }
    searching = true;
    searchError = null;
    try {
      results = await searchHF(q, format, 8);
    } catch (e) {
      searchError = e instanceof Error ? e.message : String(e);
    } finally {
      searching = false;
    }
  }

  let searchTimer = $state<ReturnType<typeof setTimeout> | null>(null);
  function onQueryInput(v: string): void {
    query = v;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 300);
  }

  async function onResultClick(hit: HFSearchHit): Promise<void> {
    if (format === 'mlx') {
      // MLX repos are single-directory; start download straight away.
      await kickoffDownload(hit, undefined);
      return;
    }
    // GGUF repos usually have multiple quants — let the user pick.
    pickerFor = hit;
    pickerFiles = [];
    pickerLoading = true;
    try {
      pickerFiles = await listRepoFiles(hit.repo_id, 'gguf');
    } catch (e) {
      searchError = e instanceof Error ? e.message : String(e);
      pickerFor = null;
    } finally {
      pickerLoading = false;
    }
  }

  async function kickoffDownload(hit: HFSearchHit, filename: string | undefined): Promise<void> {
    try {
      const id = await startDownload(hit.repo_id, format, filename);
      const label = filename ? `${hit.repo_id} · ${filename}` : hit.repo_id;
      const source = new EventSource(`/api/models/download/${encodeURIComponent(id)}/progress`);
      const dl: ActiveDownload = {
        id, label, percent: 0, speedMbps: 0, etaS: 0, error: null, source,
      };
      downloads = [...downloads, dl];
      source.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.heartbeat) return;
          const idx = downloads.findIndex((d) => d.id === id);
          if (idx < 0) return;
          const next = [...downloads];
          next[idx] = {
            ...next[idx],
            percent: Number(data.percent) || 0,
            speedMbps: Number(data.speed_mbps) || 0,
            etaS: Number(data.eta_s) || 0,
            error: data.error ?? null,
          };
          downloads = next;
          if (data.done || data.error) {
            source.close();
            setTimeout(() => { downloads = downloads.filter((d) => d.id !== id); }, 2500);
            if (data.done) onModelsChanged?.();
          }
        } catch { /* ignore malformed SSE lines */ }
      };
      source.onerror = () => { source.close(); };
      pickerFor = null;
    } catch (e) {
      searchError = e instanceof Error ? e.message : String(e);
    }
  }

  async function cancel(d: ActiveDownload): Promise<void> {
    d.source.close();
    await cancelDownload(d.id);
    downloads = downloads.filter((x) => x.id !== d.id);
  }

  function fmtEta(s: number): string {
    if (!s || !Number.isFinite(s)) return '';
    if (s < 60) return `${Math.round(s)}s`;
    if (s < 3600) return `${Math.round(s / 60)}m`;
    return `${(s / 3600).toFixed(1)}h`;
  }
</script>

<div class="discover">
  <!-- Active downloads panel (shown whenever something's in flight) -->
  {#if downloads.length > 0}
    <div class="downloads">
      {#each downloads as d (d.id)}
        <div class="download-row" class:err={!!d.error}>
          <div class="dl-head">
            <span class="dl-label">{d.label}</span>
            <button class="dl-cancel" onclick={() => cancel(d)}>Cancel</button>
          </div>
          <div class="dl-bar">
            <div class="dl-fill" style:width={`${Math.min(100, Math.max(0, d.percent))}%`}></div>
          </div>
          <div class="dl-meta">
            {#if d.error}
              Error: {d.error}
            {:else}
              {d.percent.toFixed(1)}%
              {#if d.speedMbps > 0} · {d.speedMbps.toFixed(1)} MB/s{/if}
              {#if d.etaS > 0} · ETA {fmtEta(d.etaS)}{/if}
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <div class="controls">
    <div class="seg">
      <button class:active={format === 'gguf'} onclick={() => { format = 'gguf'; results = []; }}>GGUF</button>
      <button class:active={format === 'mlx'}  onclick={() => { format = 'mlx'; results = []; }}>MLX</button>
    </div>
    <input
      type="text"
      placeholder={`Search Hugging Face for ${format.toUpperCase()} models…`}
      value={query}
      oninput={(e) => onQueryInput((e.currentTarget as HTMLInputElement).value)}
    />
  </div>

  {#if searchError}
    <p class="status err">{searchError}</p>
  {/if}

  {#if searching}
    <p class="hint">Searching…</p>
  {:else if query.trim() && results.length === 0}
    <p class="hint">No matches for "{query.trim()}".</p>
  {:else if results.length === 0}
    <p class="hint">
      Type to search Hugging Face. Click a result to download — GGUF repos
      will show a file picker so you can choose the quantisation.
    </p>
  {:else}
    <div class="results">
      {#each results as hit (hit.repo_id)}
        <button class="result" onclick={() => onResultClick(hit)}>
          <span class="repo">{hit.repo_id}</span>
          <span class="stats">
            ⬇ {hit.downloads.toLocaleString()} · ♥ {hit.likes.toLocaleString()}
          </span>
        </button>
      {/each}
    </div>
  {/if}
</div>

<!-- GGUF file picker overlay -->
{#if pickerFor}
  <div class="picker-overlay" role="presentation" onclick={(e) => {
    if (e.currentTarget === e.target) pickerFor = null;
  }}>
    <div class="picker">
      <header>
        <h3>Pick a file from <span class="mono">{pickerFor.repo_id}</span></h3>
        <button class="close" onclick={() => pickerFor = null}>×</button>
      </header>
      {#if pickerLoading}
        <p class="hint">Loading files…</p>
      {:else if pickerFiles.length === 0}
        <p class="hint">No downloadable {format.toUpperCase()} files in this repo.</p>
      {:else}
        <div class="files">
          {#each pickerFiles as f (f.name)}
            <button class="file" onclick={() => kickoffDownload(pickerFor!, f.name)}>
              <span class="fname">{f.name}</span>
              <span class="fsize">{f.size_gb.toFixed(1)} GB</span>
            </button>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .discover { display: flex; flex-direction: column; gap: 12px; }

  .controls { display: flex; gap: 8px; align-items: center; }
  .seg {
    display: inline-flex;
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    overflow: hidden;
  }
  .seg button {
    background: none;
    border: none;
    padding: 5px 12px;
    font-size: 11px;
    font-weight: 500;
    color: var(--loca-color-text-muted);
    cursor: pointer;
  }
  .seg button.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-accent);
  }
  .seg button + button { border-left: 1px solid var(--loca-color-border); }
  .controls input {
    flex: 1;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 6px 10px;
    font-size: 12px;
    color: var(--loca-color-text);
  }

  .hint { color: var(--loca-color-text-muted); font-size: 12px; margin: 4px 2px 0; line-height: 1.5; }
  .status.err {
    color: var(--loca-color-danger);
    font-size: 12px;
    padding: 6px 10px;
    background: color-mix(in srgb, var(--loca-color-danger) 10%, transparent);
    border-radius: var(--loca-radius-sm);
    margin: 0;
  }

  .results { display: flex; flex-direction: column; gap: 6px; }
  .result {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    text-align: left;
    width: 100%;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-md);
    padding: 10px 12px;
    font-size: 12px;
    color: var(--loca-color-text);
    cursor: pointer;
  }
  .result:hover { border-color: var(--loca-color-accent); }
  .repo { font-family: var(--loca-font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .stats { font-size: 11px; color: var(--loca-color-text-muted); white-space: nowrap; }

  .downloads {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px;
    background: color-mix(in srgb, var(--loca-color-accent) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--loca-color-accent) 30%, transparent);
    border-radius: var(--loca-radius-sm);
  }
  .download-row { display: flex; flex-direction: column; gap: 4px; font-size: 11px; }
  .download-row.err { color: var(--loca-color-danger); }
  .dl-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .dl-label {
    font-family: var(--loca-font-mono);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    flex: 1;
  }
  .dl-cancel {
    background: none;
    border: 1px solid var(--loca-color-border);
    color: var(--loca-color-text-muted);
    border-radius: var(--loca-radius-sm);
    font-size: 10px;
    padding: 2px 8px;
    cursor: pointer;
  }
  .dl-cancel:hover { color: var(--loca-color-danger); }
  .dl-bar { height: 4px; background: rgba(127, 127, 127, 0.2); border-radius: 2px; overflow: hidden; }
  .dl-fill { height: 100%; background: var(--loca-color-accent); transition: width 200ms; }
  .dl-meta { color: var(--loca-color-text-muted); font-family: var(--loca-font-mono); }

  /* File picker */
  .picker-overlay {
    position: fixed; inset: 0;
    display: flex; align-items: center; justify-content: center;
    background: rgba(0, 0, 0, 0.4);
    z-index: 30;
  }
  .picker {
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-md);
    width: 520px;
    max-height: 520px;
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  .picker header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--loca-color-border);
  }
  .picker h3 { font-size: 13px; margin: 0; font-weight: 500; }
  .picker .mono { font-family: var(--loca-font-mono); }
  .picker .close {
    width: 22px; height: 22px; border-radius: 50%;
    border: none; background: rgba(128, 128, 128, 0.1);
    color: var(--loca-color-text-muted);
    font-size: 13px; cursor: pointer;
  }
  .picker .close:hover { background: rgba(128, 128, 128, 0.2); }
  .files { overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 4px; }
  .file {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: 8px 10px;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    color: var(--loca-color-text);
    font-size: 12px;
    cursor: pointer;
    text-align: left;
  }
  .file:hover { border-color: var(--loca-color-accent); }
  .fname { font-family: var(--loca-font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fsize { color: var(--loca-color-text-muted); font-size: 11px; white-space: nowrap; }
</style>
