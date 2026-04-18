<!--
  ResearchWorkspaceView — Loca's Research Partner surface.

  Panel structure matches VaultView / MemoryView: overlay card with header
  + close, tabbed body. Contents per tab:

    - Overview: pick / create a project, edit scope, counts, Related
      notes strip, Dig-deeper launcher
    - Sources: bookmarked items (conv / memory / quote / url / vault_sync)
      with filter + delete
    - Notes: markdown scratchpad, autosaves on idle
    - Watches: background schedules, create / trigger / delete

  No SwiftUI twin exists yet — that lands in the same PR in
  ResearchWorkspaceView.swift.
-->
<script lang="ts">
  import { app } from './app-store.svelte';
  import {
    addProjectItem,
    createProject as apiCreateProject,
    createWatch,
    deleteProject,
    deleteProjectItem,
    deleteWatch,
    digDeeper,
    fetchProject,
    fetchRelated,
    listProjectItems,
    patchProject,
    syncVault,
    type Project,
    type ProjectDetail,
    type ProjectItem,
    type ProjectItemKind,
    type RelatedItem,
  } from './api.client';
  import { renderMarkdown } from './markdown';

  interface Props { onClose?: () => void; }
  let { onClose }: Props = $props();

  type Tab = 'overview' | 'sources' | 'notes' | 'watches';
  let activeTab = $state<Tab>('overview');

  let detail = $state<ProjectDetail | null>(null);
  let items = $state<ProjectItem[]>([]);
  let filterKind = $state<ProjectItemKind | ''>('');
  let related = $state<RelatedItem[]>([]);
  let relatedLoading = $state(false);

  let createTitle = $state('');
  let createScope = $state('');

  let scopeDraft = $state('');
  let notesDraft = $state('');
  let notesTimer: ReturnType<typeof setTimeout> | null = null;

  let digDraft = $state('');
  let digBusy = $state(false);
  let digStatus = $state<string | null>(null);

  let vaultPath = $state('');
  let vaultBusy = $state(false);
  let vaultStatus = $state<string | null>(null);

  let watchScope = $state('');
  let watchMinutes = $state(1440);

  async function refreshDetail(id: string): Promise<void> {
    detail = await fetchProject(id);
    scopeDraft = detail.scope;
    notesDraft = detail.notes;
    await loadItems(id);
    await loadRelated(id);
  }

  async function loadItems(id: string): Promise<void> {
    const kind = filterKind || undefined;
    const r = await listProjectItems(id, kind as ProjectItemKind | undefined);
    items = r.items;
  }

  async function loadRelated(id: string): Promise<void> {
    relatedLoading = true;
    try {
      related = await fetchRelated(id, 8);
    } catch { related = []; }
    relatedLoading = false;
  }

  $effect(() => {
    const id = app.activeProjectId;
    if (!id) {
      detail = null;
      items = [];
      related = [];
      return;
    }
    void refreshDetail(id);
  });

  $effect(() => {
    void filterKind;
    const id = app.activeProjectId;
    if (id) void loadItems(id);
  });

  async function handleCreate(): Promise<void> {
    const title = createTitle.trim();
    if (!title) return;
    const p: Project = await apiCreateProject(title, createScope.trim());
    createTitle = '';
    createScope = '';
    await app.refreshProjects();
    app.setActiveProject(p.id);
  }

  async function handleDeleteProject(): Promise<void> {
    if (!app.activeProjectId || !detail) return;
    if (!confirm(`Delete project "${detail.title}"? Bookmarks and watches will be removed. Conversations stay put.`)) return;
    await deleteProject(app.activeProjectId);
    app.setActiveProject(null);
    await app.refreshProjects();
  }

  async function saveScope(): Promise<void> {
    if (!app.activeProjectId) return;
    await patchProject(app.activeProjectId, { scope: scopeDraft });
    await refreshDetail(app.activeProjectId);
  }

  function scheduleNotesSave(): void {
    if (!app.activeProjectId) return;
    if (notesTimer) clearTimeout(notesTimer);
    notesTimer = setTimeout(async () => {
      if (app.activeProjectId) {
        await patchProject(app.activeProjectId, { notes: notesDraft });
      }
    }, 700);
  }

  async function handleDeleteItem(itemId: string): Promise<void> {
    if (!app.activeProjectId) return;
    await deleteProjectItem(app.activeProjectId, itemId);
    await loadItems(app.activeProjectId);
    if (detail) detail.items_count = Math.max(0, (detail.items_count ?? 1) - 1);
  }

  async function handleDigDeeper(): Promise<void> {
    if (!app.activeProjectId) return;
    const sub = digDraft.trim();
    if (!sub) return;
    digBusy = true;
    digStatus = 'Searching…';
    try {
      const r = await digDeeper(app.activeProjectId, sub, 5);
      const fresh = r.bookmarks.filter((b) => !b.duplicate).length;
      digStatus = `Bookmarked ${fresh} new source${fresh === 1 ? '' : 's'} of ${r.total}.`;
      digDraft = '';
      await loadItems(app.activeProjectId);
    } catch (e) {
      digStatus = `Failed: ${e instanceof Error ? e.message : String(e)}`;
    } finally {
      digBusy = false;
    }
  }

  async function handleSyncVault(): Promise<void> {
    if (!app.activeProjectId) return;
    const p = vaultPath.trim();
    if (!p) return;
    vaultBusy = true;
    vaultStatus = 'Ingesting vault…';
    try {
      const r = await syncVault(app.activeProjectId, p);
      vaultStatus = `Synced: ${r.stored} stored, ${r.skipped} skipped (of ${r.total}).`;
      await loadItems(app.activeProjectId);
    } catch (e) {
      vaultStatus = `Failed: ${e instanceof Error ? e.message : String(e)}`;
    } finally {
      vaultBusy = false;
    }
  }

  async function handleCreateWatch(): Promise<void> {
    if (!app.activeProjectId) return;
    const s = watchScope.trim();
    if (!s) return;
    const minutes = Math.max(60, Math.min(watchMinutes, 60 * 24 * 14));
    await createWatch(app.activeProjectId, s, minutes);
    watchScope = '';
    await refreshDetail(app.activeProjectId);
  }

  async function handleDeleteWatch(wid: string): Promise<void> {
    if (!app.activeProjectId) return;
    await deleteWatch(app.activeProjectId, wid);
    await refreshDetail(app.activeProjectId);
  }

  async function handleAttachCurrent(): Promise<void> {
    if (!app.activeProjectId || !app.activeConvId) return;
    // Attach via the store endpoint and refresh detail.
    await fetch(
      `/api/projects/${encodeURIComponent(app.activeProjectId)}/attach-conversation`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conv_id: app.activeConvId }),
      },
    );
    await refreshDetail(app.activeProjectId);
  }

  async function quickAddQuote(): Promise<void> {
    if (!app.activeProjectId) return;
    const q = prompt('Paste a quote or claim to pin to this project:');
    if (!q || !q.trim()) return;
    await addProjectItem(app.activeProjectId, { kind: 'quote', body: q.trim() });
    await loadItems(app.activeProjectId);
    if (detail) detail.items_count = (detail.items_count ?? 0) + 1;
  }

  function kindIcon(k: ProjectItemKind): string {
    return {
      conv: '💬', memory: '🧠', vault_chunk: '📓',
      web_url: '🌐', quote: '❝', vault_sync: '🗃️',
    }[k] || '•';
  }

  function fmtDate(ts: number): string {
    return new Date(ts * 1000).toLocaleDateString();
  }
</script>

<section class="panel" role="dialog" aria-label="Research Workspace">
  <header>
    <div class="header-left">
      <h2>Research</h2>
      <select
        aria-label="Active project"
        value={app.activeProjectId ?? ''}
        onchange={(e) => app.setActiveProject((e.currentTarget as HTMLSelectElement).value || null)}
      >
        <option value="">No project</option>
        {#each app.projects as p (p.id)}
          <option value={p.id}>{p.title}</option>
        {/each}
      </select>
    </div>
    {#if onClose}
      <button class="close" aria-label="Close" onclick={onClose}>×</button>
    {/if}
  </header>

  <nav class="tabs">
    {#each (['overview', 'sources', 'notes', 'watches'] as const) as t}
      <button
        class="tab"
        class:active={activeTab === t}
        onclick={() => (activeTab = t)}
      >{t}</button>
    {/each}
  </nav>

  <div class="divider"></div>

  <div class="body">
    {#if !app.activeProjectId}
      <div class="empty-create">
        <h3>Start a research project</h3>
        <p class="hint">
          A project bundles a topic, its bookmarked sources, notes, and
          background watches. Pick an existing one from the header, or
          create a new one:
        </p>
        <label class="field">
          <span>Title</span>
          <input type="text" bind:value={createTitle} placeholder="Transformers in audio" />
        </label>
        <label class="field">
          <span>Scope</span>
          <textarea rows="3" bind:value={createScope}
            placeholder="Are transformer-based architectures replacing RNNs for audio generation?"></textarea>
        </label>
        <button class="primary" disabled={!createTitle.trim()} onclick={handleCreate}>
          Create project
        </button>
      </div>
    {:else if !detail}
      <p class="hint">Loading project…</p>

    {:else if activeTab === 'overview'}
      <section class="group">
        <h3>Scope</h3>
        <textarea rows="4" bind:value={scopeDraft}
          placeholder="What is this project about?"></textarea>
        <div class="row spaced">
          <div class="counts">
            <span><strong>{detail.items_count}</strong> source{detail.items_count === 1 ? '' : 's'}</span>
            <span><strong>{detail.conversations.length}</strong> conv{detail.conversations.length === 1 ? '' : 's'}</span>
            <span><strong>{detail.watches.length}</strong> watch{detail.watches.length === 1 ? '' : 'es'}</span>
          </div>
          <div class="actions">
            <button onclick={saveScope}>Save scope</button>
            <button class="danger" onclick={handleDeleteProject}>Delete project</button>
          </div>
        </div>
      </section>

      <section class="group">
        <h3>Quick actions</h3>
        <div class="row wrap">
          <button disabled={!app.activeConvId} onclick={handleAttachCurrent}>
            Attach current conversation
          </button>
          <button onclick={quickAddQuote}>Pin a quote</button>
        </div>
      </section>

      <section class="group">
        <h3>Dig deeper</h3>
        <p class="hint">
          Bounded web research on a narrower slice. Pulls the top 5 hits,
          ingests them into memory, and bookmarks the URLs here.
        </p>
        <div class="row tight">
          <input type="text" bind:value={digDraft}
            placeholder="e.g. 'WaveNet baselines' or 'recent audio transformer papers'"
            onkeydown={(e) => { if (e.key === 'Enter' && !digBusy) handleDigDeeper(); }} />
          <button class="primary" disabled={digBusy || !digDraft.trim()} onclick={handleDigDeeper}>
            {digBusy ? 'Working…' : 'Dig'}
          </button>
        </div>
        {#if digStatus}<p class="status">{digStatus}</p>{/if}
      </section>

      <section class="group">
        <h3>Related notes {#if relatedLoading}<span class="muted">(loading…)</span>{/if}</h3>
        {#if related.length === 0 && !relatedLoading}
          <p class="hint">
            No neighbours yet. Add a scope description and bookmark a few
            sources so Loca has something to crosswalk against.
          </p>
        {/if}
        <ul class="related">
          {#each related as r}
            <li>
              <span class="icon">{r.kind === 'memory' ? '🧠' : '📓'}</span>
              <div class="related-body">
                <div class="title">{r.title}</div>
                {#if r.snippet}<div class="snippet">{r.snippet}</div>{/if}
              </div>
              <span class="score">{(r.score * 100).toFixed(0)}%</span>
            </li>
          {/each}
        </ul>
      </section>

    {:else if activeTab === 'sources'}
      <section class="group">
        <div class="row spaced">
          <h3>Sources ({items.length})</h3>
          <select aria-label="Filter by kind" bind:value={filterKind}>
            <option value="">All</option>
            <option value="conv">Conversations</option>
            <option value="quote">Quotes</option>
            <option value="web_url">Web URLs</option>
            <option value="memory">Memories</option>
            <option value="vault_chunk">Vault chunks</option>
            <option value="vault_sync">Vault syncs</option>
          </select>
        </div>
        <div class="row tight">
          <input type="text" bind:value={vaultPath}
            placeholder="Obsidian vault path to sync" />
          <button disabled={vaultBusy || !vaultPath.trim()} onclick={handleSyncVault}>
            {vaultBusy ? 'Syncing…' : 'Sync vault'}
          </button>
        </div>
        {#if vaultStatus}<p class="status">{vaultStatus}</p>{/if}
      </section>

      {#if items.length === 0}
        <p class="hint">
          No sources pinned yet. Use the Overview tab to attach a
          conversation, pin a quote, dig deeper, or sync a vault.
        </p>
      {:else}
        <ul class="item-list">
          {#each items as it (it.id)}
            <li class="item">
              <span class="icon">{kindIcon(it.kind)}</span>
              <div class="item-body">
                {#if it.url}
                  <a class="title" href={it.url} target="_blank" rel="noopener noreferrer">{it.title || it.url}</a>
                {:else}
                  <div class="title">{it.title || '(untitled)'}</div>
                {/if}
                {#if it.body}<div class="snippet">{it.body.slice(0, 240)}</div>{/if}
                <div class="meta">{it.kind} · {fmtDate(it.created)}</div>
              </div>
              <button class="item-del" aria-label="Remove" onclick={() => handleDeleteItem(it.id)}>×</button>
            </li>
          {/each}
        </ul>
      {/if}

    {:else if activeTab === 'notes'}
      <section class="group">
        <h3>Notes</h3>
        <p class="hint">Freeform markdown scratchpad. Autosaves ~700ms after you stop typing.</p>
        <textarea
          rows="14"
          class="notes-editor"
          bind:value={notesDraft}
          oninput={scheduleNotesSave}
          placeholder="Questions, todos, open threads, quotes, hypotheses — anything."
        ></textarea>
        {#if notesDraft.trim()}
          <details>
            <summary>Preview</summary>
            <!-- eslint-disable-next-line svelte/no-at-html-tags -->
            <div class="md">{@html renderMarkdown(notesDraft)}</div>
          </details>
        {/if}
      </section>

    {:else if activeTab === 'watches'}
      <section class="group">
        <h3>New watch</h3>
        <p class="hint">
          Background search every N minutes; new URLs get appended to
          Sources. Minimum 60 min, max 2 weeks.
        </p>
        <label class="field">
          <span>Sub-scope</span>
          <input type="text" bind:value={watchScope}
            placeholder="new audio transformer papers on arXiv" />
        </label>
        <label class="field">
          <span>Every</span>
          <select bind:value={watchMinutes}>
            <option value={60}>1 hour</option>
            <option value={360}>6 hours</option>
            <option value={1440}>Daily</option>
            <option value={10080}>Weekly</option>
          </select>
        </label>
        <button class="primary" disabled={!watchScope.trim()} onclick={handleCreateWatch}>
          Create watch
        </button>
      </section>

      <section class="group">
        <h3>Active watches ({detail.watches.length})</h3>
        {#if detail.watches.length === 0}
          <p class="hint">No watches yet.</p>
        {:else}
          <ul class="item-list">
            {#each detail.watches as w (w.id)}
              <li class="item">
                <span class="icon">⏱</span>
                <div class="item-body">
                  <div class="title">{w.sub_scope}</div>
                  <div class="meta">
                    every {w.schedule_minutes < 1440
                      ? `${w.schedule_minutes}min`
                      : `${Math.round(w.schedule_minutes / 1440)}d`}
                    {w.last_run ? ` · last run ${fmtDate(w.last_run)}` : ' · never run'}
                  </div>
                </div>
                <button class="item-del" aria-label="Remove watch" onclick={() => handleDeleteWatch(w.id)}>×</button>
              </li>
            {/each}
          </ul>
        {/if}
      </section>
    {/if}
  </div>
</section>

<style>
  .panel {
    width: 620px;
    max-height: 720px;
    display: flex;
    flex-direction: column;
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-lg);
    overflow: hidden;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
  }
  .header-left { display: flex; align-items: center; gap: 12px; }
  h2 { font-size: 14px; font-weight: 600; margin: 0; color: var(--loca-color-text); }
  h3 { margin: 0 0 6px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; color: var(--loca-color-text-muted); }

  .close {
    width: 24px; height: 24px; border-radius: 50%; border: none;
    background: rgba(128, 128, 128, 0.1); color: var(--loca-color-text-muted);
    font-size: 14px; line-height: 1; cursor: pointer;
  }
  .close:hover { background: rgba(128, 128, 128, 0.2); }

  .tabs { display: flex; gap: 4px; padding: 0 20px 10px; }
  .tab {
    padding: 5px 12px; font-size: 12px; text-transform: capitalize;
    background: none; border: 1px solid transparent; border-radius: var(--loca-radius-sm);
    color: var(--loca-color-text-muted); cursor: pointer;
  }
  .tab:hover { color: var(--loca-color-text); }
  .tab.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-text); border-color: var(--loca-color-border);
  }

  .divider { height: 1px; background: var(--loca-color-border); }

  .body { flex: 1; padding: 14px 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }

  .group { display: flex; flex-direction: column; gap: 8px; }
  .row { display: flex; gap: 8px; align-items: center; }
  .row.tight { gap: 6px; }
  .row.spaced { justify-content: space-between; }
  .row.wrap { flex-wrap: wrap; }
  .field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--loca-color-text); }
  .field input, .field textarea, .field select, .body textarea, .body select, .row.tight input {
    background: var(--loca-color-surface); border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm); padding: 6px 10px; font-size: 12px;
    color: var(--loca-color-text); font-family: inherit;
  }
  .body textarea { resize: vertical; min-height: 60px; }
  .notes-editor { min-height: 220px; font-family: var(--loca-font-mono); font-size: 12px; }

  .counts { display: flex; gap: 14px; font-size: 12px; color: var(--loca-color-text-muted); }
  .counts strong { color: var(--loca-color-text); margin-right: 4px; }

  .actions { display: flex; gap: 6px; }
  button {
    padding: 5px 12px; font-size: 12px; cursor: pointer;
    border: 1px solid var(--loca-color-border); border-radius: var(--loca-radius-sm);
    background: var(--loca-color-surface); color: var(--loca-color-text);
  }
  button:hover:not(:disabled) { background: color-mix(in srgb, var(--loca-color-accent) 12%, var(--loca-color-surface)); }
  button:disabled { opacity: 0.45; cursor: not-allowed; }
  button.primary { background: var(--loca-color-accent); color: white; border-color: transparent; }
  button.primary:hover:not(:disabled) { background: var(--loca-color-accent-hover); }
  button.danger { color: var(--loca-color-danger); }

  .status { margin: 0; font-size: 11px; color: var(--loca-color-text-muted); }
  .hint { margin: 0; font-size: 11px; color: var(--loca-color-text-muted); line-height: 1.5; }
  .muted { font-weight: 400; color: var(--loca-color-text-muted); font-size: 11px; }

  .empty-create { display: flex; flex-direction: column; gap: 10px; max-width: 460px; }

  .related { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .related li { display: flex; gap: 10px; padding: 8px 10px; border: 1px solid var(--loca-color-border); border-radius: var(--loca-radius-sm); background: var(--loca-color-surface); }
  .related .related-body { flex: 1; min-width: 0; }
  .related .title { font-weight: 600; font-size: 12px; color: var(--loca-color-text); }
  .related .snippet { font-size: 11px; color: var(--loca-color-text-muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .related .score { color: var(--loca-color-text-muted); font-size: 10px; align-self: center; }
  .icon { font-size: 16px; flex-shrink: 0; }

  .item-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .item { display: flex; gap: 10px; padding: 8px 10px; border: 1px solid var(--loca-color-border); border-radius: var(--loca-radius-sm); background: var(--loca-color-surface); }
  .item-body { flex: 1; min-width: 0; }
  .item .title { font-weight: 600; font-size: 12px; color: var(--loca-color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }
  .item a.title { text-decoration: none; }
  .item a.title:hover { text-decoration: underline; }
  .item .snippet { font-size: 11px; color: var(--loca-color-text-muted); margin-top: 2px; white-space: pre-wrap; word-break: break-word; }
  .item .meta { font-size: 10px; color: var(--loca-color-text-muted); margin-top: 4px; }
  .item-del {
    width: 22px; height: 22px; border: none; background: none;
    color: var(--loca-color-text-muted); cursor: pointer; font-size: 16px; line-height: 1; border-radius: 50%;
    padding: 0;
  }
  .item-del:hover { color: var(--loca-color-danger); background: rgba(128, 128, 128, 0.12); }

  .md :global(p) { margin: 0 0 8px; font-size: 12px; }
  .md :global(h1), .md :global(h2), .md :global(h3) { margin: 10px 0 4px; font-size: 13px; }
  .md :global(ul), .md :global(ol) { margin: 4px 0; padding-left: 20px; font-size: 12px; }

  details { padding: 8px; background: var(--loca-color-surface); border: 1px solid var(--loca-color-border); border-radius: var(--loca-radius-sm); }
  summary { cursor: pointer; font-size: 11px; color: var(--loca-color-text-muted); }
</style>
