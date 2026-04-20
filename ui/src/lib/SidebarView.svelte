<!--
  SidebarView — mirrors Loca-SwiftUI/Sources/Loca/Views/SidebarView.swift.

  Phase 2 + 2b scope:
    - Controls: New Conversation, capability tabs, model picker + eject,
      context picker
    - Conversation search (server-side via /api/search/conversations)
    - Folder grouping with an "Other" section for unfoldered items
    - Per-row hover-to-delete button
    - Footer nav (Glossary, Preferences, Philosophy)

  Deferred to Phase 2c:
    - Drag-and-drop between folders
    - Multi-select delete
    - Inline conversation rename
    - Starred / pinned conversations
-->
<script lang="ts">
  import { app, CAPABILITIES } from './app-store.svelte';

  interface Props {
    onOpenRoute?: (route: string) => void;
  }
  let { onOpenRoute }: Props = $props();

  $effect(() => { void app.refresh(); });

  // Poll system RAM every 10s while the sidebar is mounted so the
  // footer indicator stays current — same cadence as Swift's
  // `_pollSystemStats`.
  $effect(() => {
    void app.refreshSystemStats();
    const t = setInterval(() => { void app.refreshSystemStats(); }, 10_000);
    return () => clearInterval(t);
  });

  const contextOptions = [4096, 8192, 16384, 32768, 65536, 131072, 262144];
  function ctxLabel(n: number): string { return n >= 1024 ? `${n / 1024}K` : `${n}`; }

  const activeModel = $derived(app.activeModelName);

  // Debounced search. When the query is non-empty we show the server-side
  // match set; otherwise the local list.
  let searchInput = $state<string>('');
  let searchTimer = $state<ReturnType<typeof setTimeout> | null>(null);
  function onSearchInput(v: string): void {
    searchInput = v;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { void app.search(v); }, 180);
  }

  const displayed = $derived(
    searchInput.trim() ? app.searchResults : app.conversations,
  );

  // Group by folder. Null / empty folder → "Other" bucket (rendered last).
  interface Group { name: string; convs: typeof displayed }
  const groups = $derived.by<Group[]>(() => {
    const byFolder = new Map<string, typeof displayed>();
    for (const c of displayed) {
      const key = c.folder && c.folder.length > 0 ? c.folder : '';
      const list = byFolder.get(key) ?? [];
      list.push(c);
      byFolder.set(key, list);
    }
    const out: Group[] = [];
    for (const [name, convs] of byFolder.entries()) {
      if (name !== '') out.push({ name, convs });
    }
    out.sort((a, b) => a.name.localeCompare(b.name));
    const unfoldered = byFolder.get('') ?? [];
    if (unfoldered.length > 0) out.push({ name: out.length > 0 ? 'Other' : '', convs: unfoldered });
    return out;
  });

  function openPanel(route: string): void { onOpenRoute?.(route); }

  // Footer "More" popover — stacks secondary entries (Vault, Glossary,
  // Preferences, Philosophy, Acknowledgements) so the sidebar bottom
  // isn't a wrapping eye-chart of tiny links.
  let moreOpen = $state<boolean>(false);

  async function onDelete(id: string, title: string, e: MouseEvent): Promise<void> {
    e.stopPropagation();
    if (!confirm(`Delete "${title || 'Untitled'}"? This cannot be undone.`)) return;
    await app.deleteConv(id);
  }

  async function onToggleStar(id: string, e: MouseEvent): Promise<void> {
    e.stopPropagation();
    await app.toggleStar(id);
  }

  async function onMoveFolder(id: string, current: string | null | undefined, e: MouseEvent): Promise<void> {
    e.stopPropagation();
    const next = prompt(
      'Move to folder (leave empty to remove from folder):',
      current ?? '',
    );
    if (next === null) return; // user cancelled
    await app.setFolder(id, next);
  }
</script>

<aside class="sidebar">
  <div class="controls">
    <button class="new-conv" onclick={() => app.newConversation()}>
      <span class="pencil">✎</span> New Conversation
    </button>

    <div class="cap-picker">
      {#each CAPABILITIES as cap, i (cap.id)}
        <button
          class:active={app.selectedCapability === cap.id}
          onclick={() => (app.selectedCapability = cap.id)}
        >{cap.label}</button>
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
          {#if activeModel}
            <button class="eject" title="Eject model — unload from memory" onclick={() => app.unload()}>
              ⏏
            </button>
          {/if}
        </div>
        {#if activeModel && app.activeAdapter}
          <!-- LoRA adapter pill — clarifies that responses are coming
               from the base model + a fine-tuned layer on top of it. -->
          <div class="adapter-pill" title={`LoRA adapter: ${app.activeAdapter}. Open Manage Models to change.`}>
            <span class="adapter-dot">✨</span>
            <span class="adapter-name">{app.activeAdapter}</span>
          </div>
        {/if}
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

  <div class="search-row">
    <input
      type="text"
      placeholder="Search conversations…"
      value={searchInput}
      oninput={(e) => onSearchInput((e.currentTarget as HTMLInputElement).value)}
    />
    {#if searchInput.trim()}
      <button class="clear-search" onclick={() => onSearchInput('')} aria-label="Clear search">×</button>
    {/if}
  </div>

  <div class="divider"></div>

  <div class="conv-list">
    {#if app.loading}
      <div class="hint">Loading conversations…</div>
    {:else if displayed.length === 0}
      <div class="hint">
        {searchInput.trim() ? 'No matches.' : 'No conversations yet. Start one to see it here.'}
      </div>
    {:else}
      {#each groups as group (group.name || '__unfoldered__')}
        {#if group.name}
          <div class="group-header">{group.name}</div>
        {/if}
        {#each group.convs as conv (conv.id)}
          <div
            class="conv-row"
            class:active={app.activeConvId === conv.id}
            class:starred={conv.starred}
            role="button"
            tabindex="0"
            onclick={() => app.selectConversation(conv.id)}
            onkeydown={(e) => { if (e.key === 'Enter') app.selectConversation(conv.id); }}
          >
            {#if conv.starred}
              <span class="star-badge" aria-hidden="true">★</span>
            {/if}
            <span class="conv-title">{conv.title || 'Untitled'}</span>
            <div class="conv-actions">
              <button
                class="conv-act"
                aria-label={conv.starred ? 'Unstar conversation' : 'Star conversation'}
                title={conv.starred ? 'Unstar' : 'Star'}
                onclick={(e) => onToggleStar(conv.id, e)}
              >{conv.starred ? '★' : '☆'}</button>
              <button
                class="conv-act"
                aria-label="Move to folder"
                title="Move to folder"
                onclick={(e) => onMoveFolder(conv.id, conv.folder, e)}
              >◰</button>
              <button
                class="conv-act danger"
                aria-label="Delete conversation"
                title="Delete"
                onclick={(e) => onDelete(conv.id, conv.title, e)}
              >×</button>
            </div>
          </div>
        {/each}
      {/each}
    {/if}
  </div>

  <div class="divider"></div>

  {#if app.ramUsedGb != null && app.ramTotalGb != null}
    {@const used = app.ramUsedGb}
    {@const total = app.ramTotalGb}
    {@const ratio = total > 0 ? used / total : 0}
    <div class="ram-row" title="System RAM in use / total. Updated every 10s.">
      <span class="ram-label">{used.toFixed(1)} / {total.toFixed(0)} GB</span>
      <div class="ram-bar">
        <div
          class="ram-fill"
          class:hot={ratio > 0.85}
          style="width: {(Math.min(ratio, 1) * 100).toFixed(1)}%"
        ></div>
      </div>
    </div>
  {/if}

  <!-- Footer: two pinned primaries (Research, Memory) + an expandable
       "More" popover for the rest. The old seven-button flex-wrap row
       was an eye-chart at the bottom of every screen. -->
  <nav class="footer">
    <button class="primary" onclick={() => openPanel('/ui/research')}>Research</button>
    <button class="primary" onclick={() => openPanel('/ui/memory')}>Memory</button>
    <div class="more-wrap">
      <button
        class="more-btn"
        aria-haspopup="menu"
        aria-expanded={moreOpen}
        onclick={() => (moreOpen = !moreOpen)}
        title="More"
      >
        ⋯
      </button>
      {#if moreOpen}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="more-backdrop"
          onclick={() => (moreOpen = false)}
        ></div>
        <div class="more-menu" role="menu">
          <button role="menuitem" onclick={() => { moreOpen = false; openPanel('/ui/vault'); }}>Vault</button>
          <button role="menuitem" onclick={() => { moreOpen = false; openPanel('/ui/glossary'); }}>Glossary</button>
          <button role="menuitem" onclick={() => { moreOpen = false; openPanel('/ui/preferences'); }}>Preferences</button>
          <button role="menuitem" onclick={() => { moreOpen = false; openPanel('/ui/philosophy'); }}>Philosophy</button>
          <button role="menuitem" onclick={() => { moreOpen = false; openPanel('/ui/acknowledgements'); }}>Acknowledgements</button>
        </div>
      {/if}
    </div>
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

  .controls { padding: var(--loca-space-md); display: flex; flex-direction: column; gap: 10px; }

  .new-conv {
    background: var(--loca-color-accent);
    color: #fff; border: none;
    border-radius: var(--loca-radius-sm);
    padding: 6px 10px;
    text-align: left;
    font-size: 12px; font-weight: 500;
    cursor: pointer;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .new-conv:hover { background: var(--loca-color-accent-hover); }
  .pencil { font-size: 11px; }

  .cap-picker { display: flex; border: 1px solid var(--loca-color-border); border-radius: var(--loca-radius-sm); overflow: hidden; }
  .cap-picker button {
    flex: 1; background: transparent; border: none;
    padding: 5px 4px;
    font-size: 11px; font-weight: 500;
    color: var(--loca-color-text-muted);
    cursor: pointer;
  }
  .cap-picker button.active {
    background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent);
    color: var(--loca-color-accent);
  }
  .cap-picker .sep { width: 1px; background: var(--loca-color-border); margin: 4px 0; align-self: stretch; }

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
    background: transparent; border: none;
    font-size: 12px;
    color: var(--loca-color-text);
    cursor: pointer;
    appearance: none;
  }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: rgba(127, 127, 127, 0.4); }
  .dot.live { background: var(--loca-color-success); }
  .model-empty { color: var(--loca-color-text-muted); font-size: 11px; padding: 4px 2px; }
  .link { background: none; border: none; color: var(--loca-color-accent); font-size: 11px; padding: 2px 0; text-align: left; cursor: pointer; }
  .eject {
    background: none; border: none;
    color: var(--loca-color-text-muted);
    cursor: pointer;
    font-size: 11px;
    padding: 0 2px;
  }
  .eject:hover { color: var(--loca-color-text); }

  .ctx-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--loca-color-text-muted); }
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

  .search-row {
    display: flex; align-items: center; gap: 6px;
    padding: 8px var(--loca-space-md);
  }
  .search-row input {
    flex: 1;
    background: var(--loca-color-bg);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    padding: 5px 8px;
    font-size: 11px;
    color: var(--loca-color-text);
  }
  .clear-search {
    background: none; border: none;
    color: var(--loca-color-text-muted);
    font-size: 14px;
    cursor: pointer;
    padding: 0 4px;
  }

  .conv-list { flex: 1; overflow-y: auto; padding: 6px 8px; }
  .hint { padding: 12px 8px; color: var(--loca-color-text-muted); font-size: 11px; line-height: 1.5; }

  .group-header {
    padding: 10px 8px 4px;
    font-size: 10px;
    font-weight: 600;
    color: var(--loca-color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .conv-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 6px 8px;
    border-radius: var(--loca-radius-sm);
    color: var(--loca-color-text);
    cursor: pointer;
    font-size: 12px;
  }
  .conv-row:hover { background: rgba(127, 127, 127, 0.1); }
  .conv-row.active { background: color-mix(in srgb, var(--loca-color-accent) 15%, transparent); }
  .conv-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .star-badge {
    color: var(--loca-color-warning);
    font-size: 11px;
    line-height: 1;
    margin-right: 4px;
    flex-shrink: 0;
  }

  .conv-actions {
    display: inline-flex;
    gap: 2px;
    margin-left: 6px;
    opacity: 0;
    transition: opacity 120ms;
  }
  .conv-row:hover .conv-actions,
  .conv-row:focus-within .conv-actions { opacity: 1; }

  .conv-act {
    background: none;
    border: none;
    padding: 0 4px;
    font-size: 13px;
    line-height: 1;
    color: var(--loca-color-text-muted);
    cursor: pointer;
  }
  .conv-act:hover { color: var(--loca-color-text); }
  .conv-act.danger:hover { color: var(--loca-color-danger); }

  .adapter-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-top: 4px;
    padding: 2px 8px;
    font-size: 10px;
    color: color-mix(in srgb, var(--loca-color-accent) 85%, var(--loca-color-text));
    background: color-mix(in srgb, var(--loca-color-accent) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--loca-color-accent) 35%, transparent);
    border-radius: 999px;
    width: fit-content;
    max-width: 100%;
    overflow: hidden;
  }
  .adapter-dot { font-size: 10px; line-height: 1; }
  .adapter-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .ram-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 12px 0;
  }
  .ram-label {
    font-family: var(--loca-font-mono);
    font-size: 10px;
    color: var(--loca-color-text-muted);
    flex-shrink: 0;
  }
  .ram-bar {
    flex: 1;
    height: 3px;
    background: color-mix(in srgb, var(--loca-color-text) 12%, transparent);
    border-radius: 2px;
    overflow: hidden;
  }
  .ram-fill {
    height: 100%;
    background: var(--loca-color-accent);
    transition: width 0.3s ease, background 0.3s ease;
  }
  .ram-fill.hot { background: #f59e0b; }

  .footer {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 8px 12px;
  }
  .footer .primary {
    background: none;
    border: 1px solid transparent;
    border-radius: var(--loca-radius-sm);
    color: var(--loca-color-text-muted);
    font-size: 11px;
    font-weight: 500;
    padding: 4px 8px;
    cursor: pointer;
  }
  .footer .primary:hover {
    color: var(--loca-color-text);
    background: var(--loca-color-border);
  }
  .more-wrap { position: relative; margin-left: auto; }
  .more-btn {
    background: none;
    border: 1px solid transparent;
    border-radius: var(--loca-radius-sm);
    color: var(--loca-color-text-muted);
    font-size: 14px;
    line-height: 1;
    padding: 2px 8px;
    cursor: pointer;
  }
  .more-btn:hover,
  .more-btn[aria-expanded="true"] {
    color: var(--loca-color-text);
    background: var(--loca-color-border);
  }
  .more-backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: transparent;
    cursor: default;
  }
  .more-menu {
    position: absolute;
    bottom: calc(100% + 6px);
    right: 0;
    z-index: 50;
    min-width: 160px;
    padding: 4px;
    background: var(--loca-color-surface);
    border: 1px solid var(--loca-color-border);
    border-radius: var(--loca-radius-sm);
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .more-menu button {
    background: none;
    border: none;
    text-align: left;
    color: var(--loca-color-text);
    font-size: 12px;
    padding: 6px 10px;
    border-radius: 4px;
    cursor: pointer;
  }
  .more-menu button:hover {
    background: var(--loca-color-border);
  }
</style>
